"""
The assistant's graph, routing and checker (ADR-0016, 4.6.2), with a scripted
model: no model is called. The fake plays each role from a script and builds
its findings from the tool results it was actually given, so the checker runs
against real results from the Salesforce snapshot.

What these pin: the fast path skips the merge; two agents run in parallel and
are merged; model arithmetic is caught and revised once; what cannot be fixed
is removed and listed; with nothing left the answer is the tool results; the
step limit, the per-agent tool list and the tool timeout hold; out-of-scope
questions call no tool; a malformed plan falls back to the rules; a prediction
question gets the limitation; cancelling stops the run.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

import assistant.graph as graph_module  # noqa: E402
from assistant.checker import check_claim  # noqa: E402
from assistant.graph import MAX_TOOL_CALLS, OUT_OF_SCOPE, Assistant, Cancelled  # noqa: E402
from assistant.model import Reply, Structured, ToolCall, Usage  # noqa: E402
from assistant.routing import asks_for_prediction, names_in, route  # noqa: E402
from tests.test_assistant_tools import builder, tools_for  # noqa: E402

SALESFORCE = "data/salesforce"


# ------------------------------------------------------------------ fake model

def _agent_of(messages: list[dict[str, Any]]) -> str:
    system = messages[0]["content"]
    for key, marker in (("account", "account analyst"), ("book", "book analyst"), ("notes", "notes researcher")):
        if marker in system:
            return key
    return "supervisor"


def _results(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The tool results this conversation was given, by ref."""
    out = {}
    for m in messages:
        if m["role"] == "tool":
            body = json.loads(m["content"])
            out[body["ref"]] = body["result"]
    return out


class FakeModel:
    """Plays every role from a script. ``calls[agent]`` is a list of tool-call
    batches to make in turn; ``findings[agent]`` builds the claims from the
    results it was given; ``plan``, ``draft``, ``revise`` and ``verdicts`` are the
    structured outputs."""

    name = "fake"

    def __init__(self, *, plan: Any, calls: dict[str, list[list[tuple[str, dict]]]],
                 findings: dict[str, Callable[[dict[str, dict]], list[dict]]],
                 draft: Callable[[str], list[dict]] | None = None,
                 revise: Callable[[str], list[dict]] | None = None,
                 verdicts: Callable[[str], list[dict]] | None = None, delay: float = 0.0):
        self.plan, self.calls, self.findings = plan, calls, findings
        self.draft, self.revise, self.verdicts, self.delay = draft, revise, verdicts, delay
        self.turns: dict[str, int] = {}
        self.log: list[str] = []
        self.threads: set[str] = set()
        self._lock = threading.Lock()

    def complete(self, messages, tools) -> Reply:
        agent = _agent_of(messages)
        with self._lock:
            n = self.turns.get(agent, 0)
            self.turns[agent] = n + 1
            self.log.append(f"complete:{agent}")
            self.threads.add(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        batches = self.calls.get(agent, [])
        if n < len(batches):
            return Reply(tool_calls=[ToolCall(id=f"{agent}-{n}-{i}", name=name, args=args)
                                     for i, (name, args) in enumerate(batches[n])], usage=Usage(100, 20))
        return Reply(content="done", usage=Usage(100, 10))

    def structured(self, messages, schema) -> Structured:
        title = schema["title"]
        with self._lock:
            self.log.append(f"structured:{title}")
        user = messages[-1]["content"]
        if title == "Plan":
            data = self.plan(user) if callable(self.plan) else self.plan
        elif title == "Findings" and _agent_of(messages) != "supervisor":
            data = {"claims": self.findings[_agent_of(messages)](_results(messages))}
        elif title == "Findings":
            data = {"claims": self.draft(user)} if self.draft else None
        elif title == "Revision":
            data = {"claims": self.revise(user)} if self.revise else None
        elif title == "Verdicts":
            data = {"verdicts": self.verdicts(user)} if self.verdicts else {"verdicts": []}
        else:
            data = None
        return Structured(data=data, usage=Usage(50, 20))


def ref_for(results: dict[str, dict], tool_name_hint: str) -> str:
    """The ref of the first result whose data has the given key."""
    return next(r for r, v in results.items() if v.get("ok") and tool_name_hint in (v.get("data") or {}))


def starlight() -> dict:
    return next(r for r in builder(SALESFORCE).queue() if r["name"] == "Starlight Freight Co")


# -------------------------------------------------------------------- routing

def test_routing_rules():
    names = [r["name"] for r in builder(SALESFORCE).queue()]
    assert names_in("Why is Starlight Freight Co at risk?", names) == ["Starlight Freight Co"]
    assert names_in("why is starlight freight co at risk", names) == ["Starlight Freight Co"]
    r = lambda q, named=(), screen=False: route(q, named_accounts=list(named), account_on_screen=screen)  # noqa: E731
    assert r("Why is Starlight at risk and what should we do?", ["Starlight Freight Co"]) == ["account"]
    assert r("Which Enterprise accounts renew in the next 90 days?") == ["book"]
    assert r("Why did customers leave last year?") == ["book"]
    assert r("Has anyone at Titan Foods mentioned a competitor?", ["Titan Foods LLC"]) == ["account", "notes"]
    assert r("What did they say on the last call?", screen=True) == ["account", "notes"]
    assert asks_for_prediction("Which 10 accounts are most likely to churn next quarter?")
    assert asks_for_prediction("Who will cancel this year?")
    assert not asks_for_prediction("Which accounts are at risk?")


# --------------------------------------------------------------------- graph

def test_fast_path_one_agent_no_merge_and_a_checked_answer():
    s = starlight()
    model = FakeModel(
        plan={"agents": ["account"], "out_of_scope": False, "instructions": {"account": "Explain the risk."}},
        calls={"account": [[("find_accounts", {"name": "Starlight Freight"})],
                           [("get_story", {"account_id": s["id"]})]]},
        findings={"account": lambda res: [{"text": res[ref_for(res, "headline")]["data"]["headline"],
                                           "citations": [ref_for(res, "headline")]}]},
        verdicts=lambda text: [{"index": 0, "supported": True, "reason": "stated"}])
    events: list[dict] = []
    out = Assistant(model, tools_for(), on_event=events.append).ask("Why is Starlight Freight Co at risk?")
    assert out["fast_path"] is True and out["plan"]["source"] == "model" and out["plan"]["agents"] == ["account"]
    assert "structured:Findings" in model.log and model.log.count("structured:Findings") == 1   # no merge call
    assert out["answer"][0]["text"].startswith("Starlight Freight Co renews in")
    assert out["answer"][0]["citations"][0]["tool"] == "get_story"
    assert out["could_not_confirm"] == [] and out["revisions"] == 0 and out["fallback"] is None
    assert [c["name"] for c in out["tool_calls"]] == ["find_accounts", "get_story"]
    assert out["model_calls"] == 6 and out["usage"]["input_tokens"] > 0
    texts = [e["text"] for e in events]
    assert "Planning" in texts and "Looking up Starlight Freight" in texts
    assert "Reading Starlight Freight Co's story" in texts and "Checking every figure against the data" in texts
    assert out["prompt_version"] and out["snapshot_ids"] == ["snap-1"]


def test_model_arithmetic_is_caught_and_revised_once():
    def findings(res):
        ref = ref_for(res, "accounts")
        rows = res[ref]["data"]["accounts"]
        added = (rows[0]["arr"] + rows[1]["arr"]) / 1000
        return [{"text": f"{rows[0]['name']} and {rows[1]['name']} together hold ${added:.0f}K.", "citations": [ref]}]

    def revise(listing):
        assert "money" in listing and "not in the cited results" in listing
        return [{"index": 0, "text": "The 27 at-risk accounts hold $3.45M of ARR.", "citations": ["T1"]}]

    model = FakeModel(plan={"agents": ["book"], "out_of_scope": False},
                      calls={"book": [[("find_accounts", {"at_risk_only": True, "limit": 3})]]},
                      findings={"book": findings}, revise=revise,
                      verdicts=lambda t: [{"index": 0, "supported": True, "reason": ""}])
    out = Assistant(model, tools_for()).ask("How much ARR is at risk?")
    assert out["revisions"] == 1
    assert [c["text"] for c in out["answer"]] == ["The 27 at-risk accounts hold $3.45M of ARR."]
    assert out["could_not_confirm"] == []


def test_what_cannot_be_fixed_is_removed_and_with_nothing_left_the_results_are_the_answer():
    model = FakeModel(plan={"agents": ["book"], "out_of_scope": False},
                      calls={"book": [[("get_book_summary", {})]]},
                      findings={"book": lambda res: [{"text": "There are 999 accounts at risk.",
                                                      "citations": list(res)}]},
                      revise=lambda listing: [{"index": 0, "text": "There are 998 accounts at risk.",
                                               "citations": ["T1"]}])
    out = Assistant(model, tools_for()).ask("How many accounts are at risk?")
    assert out["answer"] == []
    assert out["could_not_confirm"][0]["text"] == "There are 999 accounts at risk."
    assert "998" in out["could_not_confirm"][0]["why"]
    assert out["fallback"] and out["fallback"][0]["tool"] == "get_book_summary"
    assert out["fallback"][0]["data"]["at_risk_accounts"] == 27


def test_an_unsupported_verdict_sends_the_claim_back():
    s = starlight()
    model = FakeModel(plan={"agents": ["account"], "out_of_scope": False},
                      calls={"account": [[("get_story", {"account_id": s["id"]})]]},
                      findings={"account": lambda res: [{"text": "Starlight Freight Co will churn in 350 days.",
                                                         "citations": ["T1"]}]},
                      verdicts=lambda t: [{"index": 0, "supported": "will churn" not in t,
                                           "reason": "turns the renewal into a prediction"}],
                      revise=lambda listing: [{"index": 0, "text": "Starlight Freight Co renews in 350 days.",
                                               "citations": ["T1"]}])
    out = Assistant(model, tools_for()).ask("When might Starlight Freight Co leave?")
    assert out["revisions"] == 1 and out["answer"][0]["text"] == "Starlight Freight Co renews in 350 days."


def test_two_agents_run_in_parallel_and_are_merged():
    s = starlight()
    model = FakeModel(
        plan={"agents": ["account", "notes"], "out_of_scope": False},
        calls={"account": [[("get_story", {"account_id": s["id"]})]],
               "notes": [[("find_accounts", {"name": "Starlight Freight"})]]},
        findings={"account": lambda res: [{"text": "Starlight Freight Co renews in 350 days.", "citations": list(res)}],
                  "notes": lambda res: [{"text": "Notes search is not configured here.", "citations": []}]},
        draft=lambda user: [{"text": "Starlight Freight Co renews in 350 days.", "citations": ["T1"]}],
        delay=0.05)
    out = Assistant(model, tools_for()).ask("What did Starlight Freight Co say, and why are they at risk?")
    assert out["fast_path"] is False and model.log.count("structured:Findings") == 3   # two agents + the merge
    assert len([t for t in model.threads if t.startswith("agent")]) == 2
    assert {c["agent"] for c in out["tool_calls"]} == {"account", "notes"}
    assert out["answer"][0]["text"] == "Starlight Freight Co renews in 350 days."


def test_the_step_limit_and_the_agents_own_tool_list_hold():
    model = FakeModel(plan={"agents": ["notes"], "out_of_scope": False},
                      calls={"notes": [[("get_causes", {})]] + [[("find_accounts", {"limit": 1})]] * 8},
                      findings={"notes": lambda res: [{"text": "Nothing found.", "citations": []}]})
    out = Assistant(model, tools_for()).ask("What did customers say?")
    calls = out["tool_calls"]
    assert calls[0]["name"] == "get_causes" and calls[0]["refused"] is True     # not a notes tool
    executed = [c for c in calls if not c.get("refused")]
    assert len(executed) == MAX_TOOL_CALLS - 3                                  # the turn limit stopped it first
    assert model.turns["notes"] == graph_module.MAX_TURNS


def test_the_step_limit_refuses_the_seventh_call(monkeypatch):
    model = FakeModel(plan={"agents": ["book"], "out_of_scope": False},
                      calls={"book": [[("find_accounts", {"limit": 1})] * 4] * 2},
                      findings={"book": lambda res: [{"text": "Done.", "citations": []}]})
    out = Assistant(model, tools_for()).ask("List accounts")
    executed = [c for c in out["tool_calls"] if not c.get("refused")]
    refused = [c for c in out["tool_calls"] if c.get("refused")]
    assert len(executed) == MAX_TOOL_CALLS and len(refused) == 2


def test_a_slow_tool_times_out_and_the_answer_still_comes(monkeypatch):
    monkeypatch.setattr(graph_module, "TOOL_TIMEOUT_SECONDS", 0.05)
    tools = tools_for()
    real = tools.call
    tools.call = lambda name, args=None: (time.sleep(0.3), real(name, args))[1]
    model = FakeModel(plan={"agents": ["book"], "out_of_scope": False},
                      calls={"book": [[("get_book_summary", {})]]},
                      findings={"book": lambda res: [{"text": "The summary was not available.", "citations": []}]})
    out = Assistant(model, tools).ask("How is the book?")
    assert out["tool_calls"][0]["ok"] is False and "timed out" in out["tool_calls"][0]["error"]
    assert out["answer"][0]["text"] == "The summary was not available."


def test_out_of_scope_calls_no_tool():
    model = FakeModel(plan={"agents": [], "out_of_scope": True}, calls={}, findings={})
    out = Assistant(model, tools_for()).ask("Email every at-risk customer a discount")
    assert out["out_of_scope"] is True and out["answer"][0]["text"] == OUT_OF_SCOPE
    assert out["tool_calls"] == [] and model.log == ["structured:Plan"]


def test_a_malformed_plan_falls_back_to_the_rules():
    model = FakeModel(plan=None, calls={"book": [[("get_book_summary", {})]]},
                      findings={"book": lambda res: [{"text": "27 accounts are at risk.", "citations": list(res)}]})
    out = Assistant(model, tools_for()).ask("Which accounts renew in the next 90 days?")
    assert out["plan"]["source"] == "rules" and out["plan"]["agents"] == ["book"]


def test_a_prediction_question_gets_the_limitation():
    model = FakeModel(plan={"agents": ["book"], "out_of_scope": False},
                      calls={"book": [[("find_accounts", {"limit": 10})]]},
                      findings={"book": lambda res: [{"text": "Ranked by score.", "citations": []}]})
    out = Assistant(model, tools_for()).ask("Which 10 accounts are most likely to churn next quarter?")
    assert out["limitation"].startswith("This data can't predict who will churn: the org holds 1 week of usage")
    out = Assistant(FakeModel(plan={"agents": ["book"], "out_of_scope": False}, calls={},
                              findings={"book": lambda res: []}), tools_for()).ask("Which accounts are at risk?")
    assert out["limitation"] is None


def test_cancelling_stops_the_run():
    cancel = threading.Event()
    model = FakeModel(plan=lambda user: (cancel.set(), {"agents": ["book"], "out_of_scope": False})[1],
                      calls={"book": [[("get_book_summary", {})]]}, findings={"book": lambda res: []})
    with pytest.raises(Cancelled):
        Assistant(model, tools_for(), cancel=cancel).ask("How is the book?")
    assert "complete:book" not in model.log


def test_a_citation_to_a_result_that_does_not_exist_fails():
    model = FakeModel(plan={"agents": ["book"], "out_of_scope": False},
                      calls={"book": [[("get_book_summary", {})]]},
                      findings={"book": lambda res: [{"text": "27 accounts are at risk.", "citations": ["T9"]}]})
    out = Assistant(model, tools_for()).ask("How many accounts are at risk?")
    assert "T9" in out["could_not_confirm"][0]["why"]


# -------------------------------------------------------------------- checker

def test_checker_precision_rates_and_question_numbers():
    tools = tools_for()
    summary = tools.call("get_book_summary")
    names = [r["name"] for r in builder(SALESFORCE).queue()]
    ok = lambda text, q="": not check_claim(text, [summary], names=names, question=q)  # noqa: E731
    assert ok("27 of 132 active accounts are at risk, holding $3.45M.")
    assert ok("27 of 132 active accounts are at risk, holding $3,450,579.")
    assert not ok("27 of 132 active accounts are at risk, holding $3.5M and $3.46M.")
    assert not ok("28 accounts are at risk.")
    assert ok("Of the top 10 you asked about, 27 are at risk.", q="show me the top 10")
    plays = tools.call("get_plays", {"account_id": starlight()["id"]})
    assert not check_claim("Internal champions programs renewed 6 of 7 (86%).", [plays], names=names)


def test_a_prediction_question_is_never_refused_as_out_of_scope():
    model = FakeModel(plan={"agents": [], "out_of_scope": True},
                      calls={"book": [[("find_accounts", {"limit": 5})]]},
                      findings={"book": lambda res: [{"text": "Ranked by score.", "citations": []}]})
    out = Assistant(model, tools_for()).ask("Which 5 accounts are most likely to churn next quarter?")
    assert out["out_of_scope"] is False and out["plan"]["agents"] == ["book"]
    assert out["plan"]["source"] == "rules: prediction is in scope" and out["limitation"]
    refused = Assistant(FakeModel(plan={"agents": [], "out_of_scope": True}, calls={}, findings={}),
                        tools_for()).ask("Write me a poem")
    assert refused["out_of_scope"] is True and refused["limitation"] is None


def test_out_of_scope_is_decided_by_code():
    from assistant.routing import asks_for_action
    assert asks_for_action("Email every at-risk customer a discount")
    assert asks_for_action("please update Starlight's risk band")
    assert not asks_for_action("Has any customer mentioned evaluating a competitor?")
    # The model refusing an in-scope question is overruled by the rules.
    model = FakeModel(plan={"agents": [], "out_of_scope": True},
                      calls={"book": [[("get_causes", {})]], "notes": [[("find_accounts", {"limit": 1})]]},
                      findings={"book": lambda res: [], "notes": lambda res: []},
                      draft=lambda user: [])
    out = Assistant(model, tools_for()).ask("Has any customer mentioned evaluating a competitor? Which competitors?")
    assert out["out_of_scope"] is False and out["plan"]["source"] == "rules: in scope by the routing rules"
    # An action is refused even when the model plans to answer it.
    model = FakeModel(plan={"agents": ["book"], "out_of_scope": False}, calls={}, findings={})
    out = Assistant(model, tools_for()).ask("Send Starlight Freight Co a renewal discount")
    assert out["out_of_scope"] is True and out["tool_calls"] == []


def test_prediction_wording_fails_the_check():
    summary = tools_for().call("find_accounts", {"limit": 1})
    names = [r["name"] for r in builder(SALESFORCE).queue()]
    top = summary["data"]["accounts"][0]["name"]
    assert check_claim(f"{top} is most likely to churn next quarter.", [summary], names=names)[0].kind == "prediction"
    assert check_claim(f"{top} will churn.", [summary], names=names)
    assert not check_claim(f"{top} has the highest risk score.", [summary], names=names)


def test_number_words_are_checked_like_digits():
    summary = tools_for().call("get_book_summary")
    names = [r["name"] for r in builder(SALESFORCE).queue()]
    assert check_claim("Fifteen accounts are at risk.", [summary], names=names)[0].value == "Fifteen"
    assert check_claim("A dozen accounts renew soon.", [], names=names)[0].kind == "citation"
    summary_ok = check_claim("One account stands out.", [summary], names=names)
    assert not summary_ok                                                     # "one" is an article, not a count
    causes = tools_for().call("get_causes")
    assert not check_claim("Fifteen accounts churned to competition.", [causes], names=names)
    assert "ALREADY churned" in causes["data"]["about"]


def test_uncited_claims_may_only_state_a_limitation_and_refs_leave_the_text():
    from assistant.checker import strip_refs
    names = [r["name"] for r in builder(SALESFORCE).queue()]
    assert not check_claim("The renewal history is not available.", [], names=names)
    assert not check_claim("I could not find any note about pricing.", [], names=names)
    assert check_claim("No other competitors were mentioned.", [], names=names)[0].kind == "citation"
    assert strip_refs("Titan Foods LLC ranks third (T1). Foxglove is second [T1, T2].") == \
        "Titan Foods LLC ranks third. Foxglove is second."


def test_a_how_much_question_must_get_its_figure():
    from assistant.checker import missing_figure
    q = "Which 5 accounts have the highest risk, and how much ARR do they hold?"
    assert missing_figure(q, ["Starlight Freight Co holds $56,187."])
    assert missing_figure(q, ["Together they hold $183,104."]) is None
    assert missing_figure("How many accounts are at risk?", ["27 accounts are at risk."]) is None
    assert missing_figure("Why is Starlight at risk?", ["It has low seat use."]) is None

    def findings(res):
        ref = ref_for(res, "accounts")
        return [{"text": f"{a['name']} is ranked {a['rank']}.", "citations": [ref]}
                for a in res[ref]["data"]["accounts"]]

    shown = f"${tools_for().call('find_accounts', {'limit': 5})['data']['shown_arr']:,.0f}"
    assert shown == "$178,104"                       # 56,187 + 27,665 + 59,500 + 13,752 + 21,000, summed by the tool

    def revise(listing):
        assert "how much" in listing and "shown_arr" in listing
        return [{"index": 0, "text": f"The five highest-risk accounts hold {shown} together.", "citations": ["T1"]}]

    model = FakeModel(plan={"agents": ["book"], "out_of_scope": False},
                      calls={"book": [[("find_accounts", {"limit": 5})]]}, findings={"book": findings}, revise=revise)
    out = Assistant(model, tools_for()).ask(q)
    assert out["revisions"] == 1 and out["answer"][0]["text"] == f"The five highest-risk accounts hold {shown} together."
