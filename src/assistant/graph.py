"""
The assistant's graph (ADR-0016 sections 2, 5, 6 and 8).

    plan ─▶ agents ─▶ draft ─▶ check ─┬─▶ finish
      │                         ▲     │
      │                         └─ revise (once)
      └─▶ finish   (out of scope: no tools, no model beyond the plan)

* **plan**: the supervisor proposes agents as a structured output; the routing
  rules (``routing.py``) are used when its plan is missing or malformed, and
  both are kept in the trace so the golden set can compare them.
* **agents**: each chosen specialist runs a tool loop with only its own tools,
  in parallel when more than one is chosen, then returns structured findings:
  claims, each citing tool results by ref.
* **draft**: with one agent, its findings are the draft (the fast path, no
  merge call); with more, one call merges them.
* **check**: code checks every claim against the results it cites
  (``checker.py``); a model judges faithfulness on what passed. Failures go back
  to **revise** once; what still fails is removed and listed under "could not
  confirm". If nothing survives, the answer is the tool results as a table.
* **finish**: the prediction guard's limitation sentence when the question asks
  who will churn and no model can say; a note when results come from more than
  one sync.

Caps: 6 tool calls a question, 4 model turns an agent, 5 s a tool call; the
model client carries its own timeout and one retry (``model.py``). A cancel
event, set when the page closes, stops the run between steps.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from assistant.checker import check_claim, cited_results, compact, describe, missing_figure, strip_refs
from assistant.model import Model, Reply, Structured, ToolCall, Usage
from assistant.routing import (
    AGENTS, asks_for_action, asks_for_prediction, names_in, prediction_limitation, route,
)
from assistant.tools import SPECS, Tools

logger = logging.getLogger("churnguard.assistant")

MAX_TOOL_CALLS = 6
MAX_TURNS = 4
TOOL_TIMEOUT_SECONDS = 5
AGENT_TOOLS = {
    "account": ("find_accounts", "get_story", "get_plays"),
    "book": ("find_accounts", "get_book_summary", "get_causes"),
    "notes": ("find_accounts", "search_notes"),
}
AGENT_LABEL = {"account": "Account analyst", "book": "Book analyst", "notes": "Notes researcher"}
OUT_OF_SCOPE = ("I can only answer questions about this customer book: accounts, their risk and renewals, "
                "why customers left, what has worked, and what customers said. I can't take actions.")

PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = hashlib.sha1(b"".join(p.read_bytes() for p in sorted(PROMPTS.glob("*.md")))).hexdigest()[:10]

_CLAIMS = {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                      "required": ["text", "citations"],
                                      "properties": {"text": {"type": "string"},
                                                     "citations": {"type": "array", "items": {"type": "string"}}}}}
PLAN_SCHEMA = {"title": "Plan", "description": "Which specialists answer the question.", "type": "object",
               "properties": {"agents": {"type": "array", "items": {"type": "string", "enum": list(AGENTS)}},
                              "out_of_scope": {"type": "boolean"},
                              "instructions": {"type": "object", "properties": {a: {"type": "string"} for a in AGENTS}}},
               "required": ["agents", "out_of_scope"]}
FINDINGS_SCHEMA = {"title": "Findings", "description": "Claims, each citing tool result refs.", "type": "object",
                   "properties": {"claims": _CLAIMS}, "required": ["claims"]}
REVISION_SCHEMA = {"title": "Revision", "description": "Rewritten claims by index.", "type": "object",
                   "properties": {"claims": {"type": "array", "items": {
                       "type": "object", "required": ["index", "text", "citations"],
                       "properties": {"index": {"type": "integer"}, "text": {"type": "string"},
                                      "citations": {"type": "array", "items": {"type": "string"}}}}}},
                   "required": ["claims"]}
VERDICT_SCHEMA = {"title": "Verdicts", "description": "Whether each claim is supported.", "type": "object",
                  "properties": {"verdicts": {"type": "array", "items": {
                      "type": "object", "required": ["index", "supported", "reason"],
                      "properties": {"index": {"type": "integer"}, "supported": {"type": "boolean"},
                                     "reason": {"type": "string"}}}}},
                  "required": ["verdicts"]}


class Cancelled(Exception):
    """The caller went away; nothing more is spent on this question."""


class Tracer:
    """Where each step is reported as it happens. The default does nothing; the
    service passes one that writes to Langfuse (ADR-0016 section 8). ``start``
    returns a handle that ``end`` closes, so durations are real."""

    def start(self, kind: str, name: str, **meta: Any) -> Any:
        return None

    def end(self, handle: Any, **out: Any) -> None:
        return None


def prompt(name: str, **values: str) -> str:
    text = (PROMPTS / f"{name}.md").read_text()
    if "{rules}" in text:
        text = text.replace("{rules}", (PROMPTS / "rules.md").read_text())
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


class State(TypedDict, total=False):
    question: str
    account_id: Optional[int]
    plan: dict[str, Any]
    findings: dict[str, list[dict[str, Any]]]
    claims: list[dict[str, Any]]
    failures: dict[int, str]
    pending: list[int]
    revisions: int
    fast_path: bool


class Assistant:
    """One question at a time. Not shared between requests: each has its own
    run state, so concurrent questions cannot see each other's tool results."""

    def __init__(self, model: Model, tools: Tools, *, judge: Optional[Model] = None,
                 predictions_available: bool = False,
                 on_event: Optional[Callable[[dict[str, Any]], None]] = None,
                 cancel: Optional[threading.Event] = None, tracer: Optional[Tracer] = None):
        self.model = model
        self.judge = judge or model
        self.tools = tools
        self.predictions_available = predictions_available
        self.on_event = on_event or (lambda e: None)
        self.cancel = cancel or threading.Event()
        self.tracer = tracer or Tracer()
        self.results: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.usage = Usage()
        self.model_calls = 0
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="assistant")
        builder = tools.ctx.builder
        self._names = [r["name"] for r in builder.queue()] if builder is not None else []
        self.account_line = "No account is on screen."

    # ---------------------------------------------------------------- public

    def ask(self, question: str, account_id: Optional[int] = None) -> dict[str, Any]:
        started = time.monotonic()
        if account_id is not None:
            r = self.tools.call("get_story", {"account_id": account_id})
            if r["ok"]:
                self.account_line = (f"The account on screen is {r['data']['name']} "
                                     f"(account_id {r['data']['account_id']}).")
        graph = StateGraph(State)
        graph.add_node("plan", self._plan)
        graph.add_node("agents", self._agents)
        graph.add_node("draft", self._draft)
        graph.add_node("check", self._check)
        graph.add_node("revise", self._revise)
        graph.add_node("finish", lambda s: {})
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", lambda s: "finish" if s["plan"]["out_of_scope"] else "agents",
                                    {"agents": "agents", "finish": "finish"})
        graph.add_edge("agents", "draft")
        graph.add_edge("draft", "check")
        graph.add_conditional_edges("check", lambda s: "revise" if s.get("failures") and not s.get("revisions")
                                    else "finish", {"revise": "revise", "finish": "finish"})
        graph.add_edge("revise", "check")
        graph.add_edge("finish", END)
        try:
            state = graph.compile().invoke({"question": question, "account_id": account_id, "revisions": 0})
            return self._answer(state, time.monotonic() - started)
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- nodes

    def _plan(self, s: State) -> State:
        self._step("supervisor", "Planning")
        named = names_in(s["question"], self._names)
        rules = route(s["question"], named_accounts=named, account_on_screen=s.get("account_id") is not None)
        out = self._structured([
            {"role": "system", "content": prompt("supervisor", as_of=self.tools.ctx.as_of)},
            {"role": "user", "content": f"Question: {s['question']}\n{self.account_line}\n"
                                        f"Accounts the question names: {', '.join(named) or 'none'}"}],
            PLAN_SCHEMA, role="supervisor")
        source, agents, out_of_scope, instructions = "rules", rules, False, {}
        if out.data is not None:
            proposed = [a for a in AGENTS if a in (out.data.get("agents") or [])]
            out_of_scope = bool(out.data.get("out_of_scope"))
            instructions = out.data.get("instructions") or {}
            if proposed or out_of_scope:
                source, agents = "model", proposed
        # Out of scope is decided by code the golden set can test, not by the
        # model alone (found on the first real runs: the model refused a
        # forecast question and a notes question, both in scope). An action
        # request is refused whatever the plan says; otherwise the model may
        # only refuse a question the routing rules give to no specialist.
        if asks_for_action(s["question"]):
            out_of_scope, agents, source = True, [], "rules: an action request"
        elif out_of_scope and asks_for_prediction(s["question"]):
            out_of_scope, agents, source = False, rules or ["book"], "rules: prediction is in scope"
        elif out_of_scope and rules:
            out_of_scope, agents, source = False, rules, "rules: in scope by the routing rules"
        if not agents and not out_of_scope:
            agents = rules or ["book"]
        return {"plan": {"agents": agents, "rules": rules, "source": source, "out_of_scope": out_of_scope,
                         "instructions": {a: instructions.get(a, "") for a in agents}, "named": named}}

    def _agents(self, s: State) -> State:
        agents = s["plan"]["agents"]
        if len(agents) == 1:
            return {"findings": {agents[0]: self._run_agent(agents[0], s)}}
        with ThreadPoolExecutor(max_workers=len(agents), thread_name_prefix="agent") as ex:
            futures = {a: ex.submit(self._run_agent, a, s) for a in agents}
            return {"findings": {a: f.result() for a, f in futures.items()}}

    def _draft(self, s: State) -> State:
        findings = s["findings"]
        if len(findings) == 1:
            return {"claims": list(next(iter(findings.values()))), "fast_path": True}
        self._step("supervisor", "Writing the answer")
        merged = "\n\n".join(f"{AGENT_LABEL[a]}:\n" + json.dumps(c, indent=1) for a, c in findings.items())
        out = self._structured([
            {"role": "system", "content": prompt("draft", as_of=self.tools.ctx.as_of)},
            {"role": "user", "content": f"Question: {s['question']}\n\nFindings:\n{merged}"}], FINDINGS_SCHEMA,
            role="draft")
        claims = (out.data or {}).get("claims") if out.data else None
        if not claims:
            claims = [c for cs in findings.values() for c in cs]
        return {"claims": claims, "fast_path": False}

    def _check(self, s: State) -> State:
        self._cancelled()
        claims = s["claims"]
        indexes = s.get("pending") or list(range(len(claims)))
        self._step("checker", "Checking every figure against the data")
        failures: dict[int, str] = {}
        passed: list[int] = []
        for i in indexes:
            c = claims[i]
            cited = cited_results(c.get("citations") or [], self.results)
            unknown = [x for x in (c.get("citations") or []) if x not in self.results]
            found = check_claim(c["text"], cited, names=self._names, question=s["question"])
            if unknown:
                failures[i] = f"cites {', '.join(unknown)}, which no tool returned"
            elif found:
                failures[i] = describe(found)
            elif cited:
                passed.append(i)
        failures.update(self._faithfulness(claims, passed))
        if not s.get("revisions") and claims:
            # Completeness, on the first pass only: the asked-for figure goes
            # back to the claim most likely to carry it (the first that cites
            # a result), with that claim's results in front of the reviser.
            gap = missing_figure(s["question"], [c["text"] for c in claims])
            if gap:
                target = next((i for i, c in enumerate(claims) if c.get("citations")), 0)
                failures[target] = (failures[target] + "; " if target in failures else "") + gap
        return {"failures": failures, "pending": []}

    def _revise(self, s: State) -> State:
        self._step("supervisor", "Correcting what could not be verified")
        claims = [dict(c) for c in s["claims"]]
        listing = "\n\n".join(
            f"[{i}] {claims[i]['text']}\nProblem: {problem}\nCited results:\n"
            + "\n".join(f"{r}: {compact(self.results[r])}" for r in claims[i].get("citations", [])
                        if r in self.results)
            for i, problem in s["failures"].items())
        out = self._structured([{"role": "system", "content": prompt("revise", as_of=self.tools.ctx.as_of)},
                                {"role": "user", "content": listing}], REVISION_SCHEMA, role="revise")
        changed = []
        for c in (out.data or {}).get("claims", []) if out.data else []:
            i = c.get("index")
            if isinstance(i, int) and i in s["failures"]:
                claims[i] = {"text": c.get("text", ""), "citations": c.get("citations") or [],
                             "revised_from": claims[i]["text"]}
                changed.append(i)
        return {"claims": claims, "revisions": 1, "pending": sorted(s["failures"]) or changed}

    # ---------------------------------------------------------------- agent

    def _run_agent(self, agent: str, s: State) -> list[dict[str, Any]]:
        tools = [spec for spec in SPECS if spec["name"] in AGENT_TOOLS[agent]]
        task = s["plan"]["instructions"].get(agent) or "Answer the part of the question that is yours."
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt(agent, as_of=self.tools.ctx.as_of)},
            {"role": "user", "content": f"Question: {s['question']}\n{self.account_line}\nYour task: {task}"}]
        for _ in range(MAX_TURNS):
            reply = self._complete(messages, tools, role=agent)
            if not reply.tool_calls:
                break
            messages.append({"role": "assistant", "content": reply.content,
                             "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in reply.tool_calls]})
            for call in reply.tool_calls:
                ref, result = self._tool(agent, call)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps({"ref": ref, "result": result}, default=str)})
        messages.append({"role": "user", "content": prompt("findings")})
        out = self._structured(messages, FINDINGS_SCHEMA, role=agent)
        if out.data is None:
            out = self._structured(messages, FINDINGS_SCHEMA, role=agent)   # one retry on a malformed output
        if out.data is None:
            logger.warning(f"assistant: {agent} returned no usable findings ({out.error})")
            return [{"text": f"The {AGENT_LABEL[agent].lower()} could not produce a usable answer.",
                     "citations": [], "dropped": True}]
        return [{"text": str(c.get("text", "")), "citations": [str(x) for x in c.get("citations") or []],
                 "agent": agent} for c in out.data.get("claims", [])]

    def _tool(self, agent: str, call: ToolCall) -> tuple[str, dict[str, Any]]:
        self._cancelled()
        with self._lock:
            ref = f"T{len(self.calls) + 1}"
            over = sum(1 for c in self.calls if not c.get("refused")) >= MAX_TOOL_CALLS
            record = {"ref": ref, "agent": agent, "name": call.name, "args": call.args}
            self.calls.append(record)
        if call.name not in AGENT_TOOLS[agent]:
            # The list each agent is offered is also enforced here: a model that
            # names a tool it was not given gets a refusal, not the tool.
            result = {"ok": False, "error": f"{call.name} is not one of your tools: "
                                            f"{', '.join(AGENT_TOOLS[agent])}.",
                      "snapshot_id": self.tools.ctx.snapshot_id, "as_of": self.tools.ctx.as_of}
            record.update(ok=False, refused=True, ms=0)
        elif over:
            result = {"ok": False, "error": f"Step limit reached ({MAX_TOOL_CALLS} tool calls a question). "
                                            "Answer with what you have.",
                      "snapshot_id": self.tools.ctx.snapshot_id, "as_of": self.tools.ctx.as_of}
            record.update(ok=False, refused=True, ms=0)
        else:
            self._step(agent, self._describe(call))
            handle = self.tracer.start("tool", call.name, agent=agent, ref=ref, args=call.args)
            started = time.monotonic()
            future = self._pool.submit(self.tools.call, call.name, call.args)
            try:
                result = future.result(timeout=TOOL_TIMEOUT_SECONDS)
            except FutureTimeout:
                future.cancel()
                result = {"ok": False, "error": f"{call.name} timed out after {TOOL_TIMEOUT_SECONDS} s.",
                          "snapshot_id": self.tools.ctx.snapshot_id, "as_of": self.tools.ctx.as_of}
            record.update(ok=bool(result.get("ok")), ms=round((time.monotonic() - started) * 1000),
                          error=result.get("error"))
            self.tracer.end(handle, ok=record["ok"], error=record["error"], ms=record["ms"],
                            rows=(result.get("data") or {}).get("count") if isinstance(result.get("data"), dict) else None)
        with self._lock:
            self.results[ref] = result
        return ref, result

    def _describe(self, call: ToolCall) -> str:
        a = call.args or {}
        if call.name == "find_accounts":
            if a.get("name"):
                return f"Looking up {a['name']}"
            parts = [k.replace("_", " ") if v is True else f"{k.replace('_', ' ')} {v}"
                     for k, v in a.items() if k not in ("limit", "sort") and v not in (None, False, "")]
            return "Finding accounts" + (f" ({', '.join(parts)})" if parts else "")
        if call.name in ("get_story", "get_plays"):
            builder = self.tools.ctx.builder
            c = builder.scorer.get_customer(int(a["account_id"])) if builder and str(a.get("account_id", "")).isdigit() else None
            who = c["name"] if c else f"account {a.get('account_id')}"
            return f"Reading {who}'s story" if call.name == "get_story" else f"Checking what worked for accounts like {who}"
        if call.name == "search_notes":
            return f"Searching notes for “{a.get('query', '')}”"
        return {"get_book_summary": "Totalling the book", "get_causes": "Reading why customers left"}.get(
            call.name, call.name)

    # ------------------------------------------------------------- checking

    def _faithfulness(self, claims: list[dict[str, Any]], indexes: list[int]) -> dict[int, str]:
        if not indexes:
            return {}
        listing = "\n\n".join(
            f"[{i}] {claims[i]['text']}\n" + "\n".join(
                f"{r}: {compact(self.results[r], 3000)}" for r in claims[i]["citations"] if r in self.results)
            for i in indexes)
        out = self._structured([{"role": "system", "content": prompt("faithfulness")},
                                {"role": "user", "content": listing}], VERDICT_SCHEMA, judge=True, role="judge")
        if out.data is None:
            logger.warning(f"assistant: faithfulness check unavailable ({out.error}); code checks stand")
            return {}
        return {v["index"]: f"unsupported: {v.get('reason', '')}" for v in out.data.get("verdicts", [])
                if isinstance(v.get("index"), int) and v["index"] in indexes and not v.get("supported", True)}

    # --------------------------------------------------------------- answer

    def _answer(self, s: State, seconds: float) -> dict[str, Any]:
        plan = s["plan"]
        limitation = (prediction_limitation(self.tools.ctx.builder.scorer.history_weeks())
                      if asks_for_prediction(s["question"]) and not self.predictions_available
                      and not plan["out_of_scope"] and self.tools.ctx.builder is not None else None)
        base = {"question": s["question"], "plan": plan, "fast_path": bool(s.get("fast_path")),
                "revisions": s.get("revisions", 0), "limitation": limitation,
                "tool_calls": self.calls, "model_calls": self.model_calls,
                "usage": {"input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens},
                "seconds": round(seconds, 2), "prompt_version": PROMPT_VERSION,
                "model": getattr(self.model, "name", "model"), "judge": getattr(self.judge, "name", "model")}
        if plan["out_of_scope"]:
            return {**base, "out_of_scope": True, "answer": [{"text": OUT_OF_SCOPE, "citations": []}],
                    "could_not_confirm": [], "fallback": None, "versions_note": None, "snapshot_ids": []}
        failures = s.get("failures") or {}
        kept, removed = [], []
        for i, c in enumerate(s.get("claims") or []):
            if i in failures:
                removed.append({"text": c.get("revised_from", c["text"]), "why": failures[i]})
            elif not c.get("dropped"):
                kept.append({"text": strip_refs(c["text"]), "citations": [
                    {"ref": r, "tool": next((x["name"] for x in self.calls if x["ref"] == r), None),
                     "args": next((x["args"] for x in self.calls if x["ref"] == r), None)}
                    for r in c.get("citations", [])]})
        has_facts = [c for c in kept if c["citations"]]
        fallback = None
        if not has_facts:
            fallback = [{"ref": ref, "tool": next(x["name"] for x in self.calls if x["ref"] == ref),
                         "data": r["data"]} for ref, r in self.results.items() if r.get("ok")]
        snapshots = sorted({str(r.get("snapshot_id")) for r in self.results.values() if r.get("snapshot_id")})
        passages = {str(p.get("snapshot_id")) for r in self.results.values() if r.get("ok")
                    for p in (r.get("data") or {}).get("passages", []) if isinstance(r.get("data"), dict)}
        versions = set(snapshots) | {p for p in passages if p}
        note = (f"The figures and the notes come from different syncs ({', '.join(sorted(versions))})."
                if len(versions) > 1 else None)
        return {**base, "out_of_scope": False, "answer": kept, "could_not_confirm": removed,
                "fallback": fallback, "versions_note": note, "snapshot_ids": sorted(versions)}

    # --------------------------------------------------------------- plumbing

    def _cancelled(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("the question was cancelled")

    def _step(self, agent: str, text: str) -> None:
        self.on_event({"type": "step", "agent": agent, "text": text})

    def _complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, role: str) -> Reply:
        self._cancelled()
        handle = self.tracer.start("generation", role, model=getattr(self.model, "name", "model"),
                                   tools=[t["name"] for t in tools])
        reply = self.model.complete(messages, tools)
        self.tracer.end(handle, usage=reply.usage, tool_calls=[c.name for c in reply.tool_calls])
        with self._lock:
            self.model_calls += 1
            self.usage.add(reply.usage)
        return reply

    def _structured(self, messages: list[dict[str, Any]], schema: dict[str, Any], *, judge: bool = False,
                    role: str = "structured") -> Structured:
        self._cancelled()
        model = self.judge if judge else self.model
        handle = self.tracer.start("generation", role, model=getattr(model, "name", "model"),
                                   schema=schema.get("title"))
        try:
            out = model.structured(messages, schema)
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 - a failed call is a missing output, handled by the caller
            logger.warning(f"assistant: structured call failed: {type(e).__name__}: {e}")
            out = Structured(data=None, error=f"{type(e).__name__}: {e}")
        self.tracer.end(handle, usage=out.usage, error=out.error)
        with self._lock:
            self.model_calls += 1
            self.usage.add(out.usage)
        return out
