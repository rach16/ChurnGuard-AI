"""
Build the assistant's golden set from the Salesforce data (ADR-0016 section 9).

Every question and every expected fact is derived from the served data by
code, never written by a model (the 0.6 rule): the account with the highest
score, the count of accounts renewing within 90 days, the play with the best
record, the accounts whose notes mention a cancellation. So the set follows the
data: rebuild it after the data changes, and commit the result.

Each question carries:
  kind        account | book | causes | notes | mixed | prediction | refusal
  split       tune (prompts may be adjusted against it) | heldout (gates CI;
              nothing is tuned against it). About 15 tune, 25 held out.
  agents      the specialists the routing rules should choose (multi mode)
  tools       tools that must be called (a subset of what is called)
  tools_any   at least one of these must be called, when more than one tool answers it
  facts       what the answer must contain: {"text": ...}, {"any_text": [...]},
              {"number": n}, {"money": x}
  expect      {"out_of_scope": bool, "limitation": bool}
  retrieval   for notes questions: {"query", "customer_id" | null, "contains"}:
              the top 5 passages must include one containing the text

    uv run python scripts/build_assistant_golden.py   # writes golden-masters/assistant_salesforce.json
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "backend")]
logging.disable(logging.CRITICAL)

from core.health_scoring import CustomerHealthScorer  # noqa: E402
from core.plays import PlaybookEngine  # noqa: E402
from core.signals import subject_of  # noqa: E402
from core.story import RENEWAL_SOON_DAYS, StoryBuilder  # noqa: E402

DATA = "data/salesforce"
OUT = ROOT / "golden-masters" / "assistant_salesforce.json"


def long_date(d) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def build() -> list[dict]:
    b = StoryBuilder(CustomerHealthScorer(str(ROOT / DATA)), PlaybookEngine(str(ROOT / DATA)))
    s = b.scorer
    queue = b.queue()
    active = {r["customer_id"]: r for r in queue}
    at_risk = [r for r in queue if r["at_risk"]]
    soon = [r for r in queue if r["renewal_days"] is not None and 0 <= r["renewal_days"] <= RENEWAL_SOON_DAYS]
    causes = b.causes()
    qs: list[dict] = []

    def add(kind, question, *, agents=(), tools=(), tools_any=(), facts=(), out_of_scope=False, limitation=False,
            retrieval=None, account_id=None):
        qs.append({"id": f"{kind}-{len([q for q in qs if q['kind'] == kind]) + 1:02d}", "kind": kind,
                   "question": question, "account_id": account_id, "agents": list(agents), "tools": list(tools),
                   "tools_any": list(tools_any),
                   "facts": list(facts), "expect": {"out_of_scope": out_of_scope, "limitation": limitation},
                   "retrieval": retrieval})

    # ---- one account: the five highest scores, three kinds of question each
    import re
    from datetime import date

    def signal_fact(sig: dict, story: dict) -> dict:
        """The essential part of a signal, so wording around it does not matter:
        "2 of 59" for seats, the days or the date for a renewal."""
        pair = re.search(r"\b(\d+) of (\d+)\b", sig["text"])
        if pair:
            return {"text": pair.group(0)}
        if sig.get("source") == "contract" and story.get("renewal"):
            d = date.fromisoformat(story["renewal"]["date"])
            return {"any_text": [f"{story['renewal']['days']} days", d.isoformat(), long_date(d)]}
        return {"text": sig["text"]}

    for r in queue[:5]:
        story = b.story(r["id"])
        first = next(x for x in story["signals"] if x["kind"] == "adverse")
        add("account", f"Why is {r['name']} at risk?", agents=["account"], tools=["get_story"],
            facts=[{"text": r["name"].split(" [")[0]}, {"number": r["risk_score"]}, signal_fact(first, story)])
    for r in queue[:4]:
        plays = b.playbook.plays_for(s.get_customer(r["id"])["risk_reason"], r["segment"])
        if plays:
            p = plays[0]
            add("account", f"What should we do to keep {r['name']}?", agents=["account"], tools=["get_plays"],
                facts=[{"text": " ".join(p.action.split()[1:])}, {"text": f"{p.renewed} of {p.decided}"}])
    for r in queue[:3]:
        story = b.story(r["id"])
        end = story["renewal"]
        d = date.fromisoformat(end["date"])
        # Either the story or the account row carries the renewal and the ARR.
        add("account", f"When does {r['name']} renew, and how much ARR is at stake?", agents=["account"],
            tools_any=["get_story", "find_accounts"],
            facts=[{"any_text": [f"{end['days']} days", end["date"], long_date(d)]}, {"money": r["arr"]}])

    # ---- the book
    add("book", "How many accounts are at risk right now?", agents=["book"], tools=[],
        facts=[{"number": len(at_risk)}])
    add("book", "How much ARR is at risk?", agents=["book"], tools=[],
        facts=[{"money": sum(r["arr"] for r in at_risk)}])
    add("book", f"How many accounts renew in the next {RENEWAL_SOON_DAYS} days?", agents=["book"], tools=[],
        facts=[{"number": len(soon)}])
    soon_risky = [r for r in soon if r["at_risk"]]
    add("book", f"Which at-risk accounts renew in the next {RENEWAL_SOON_DAYS} days?", agents=["book"],
        tools=["find_accounts"], facts=[{"number": len(soon_risky)}, {"text": soon_risky[0]["name"].split(" [")[0]}])
    ent = [r for r in at_risk if r["segment"] == "Enterprise"]
    add("book", "How many Enterprise accounts are at risk, and how much ARR do they hold?", agents=["book"],
        tools=["find_accounts"], facts=[{"number": len(ent)}, {"money": sum(r["arr"] for r in ent)}])
    add("book", "Which account has the highest risk score?", agents=["book"], tools=["find_accounts"],
        facts=[{"text": queue[0]["name"].split(" [")[0]}, {"number": queue[0]["risk_score"]}])
    top_arr = max(at_risk, key=lambda r: r["arr"])
    add("book", "Which at-risk account has the most ARR?", agents=["book"], tools=["find_accounts"],
        facts=[{"text": top_arr["name"].split(" [")[0]}, {"money": top_arr["arr"]}])
    add("book", "How many active accounts are there, and what is their total ARR?", agents=["book"], tools=[],
        facts=[{"number": len(queue)}, {"money": sum(r["arr"] for r in queue)}])
    smb = [r for r in at_risk if r["segment"] == "SMB"]
    add("book", "How many SMB accounts are at risk?", agents=["book"], tools=["find_accounts"],
        facts=[{"number": len(smb)}])

    # ---- why customers left
    top_cause = causes["churned"]["by_cause"][0]
    add("causes", "Why did customers leave?", agents=["book"], tools=["get_causes"],
        facts=[{"text": top_cause["cause"]}, {"number": top_cause["accounts"]}])
    comp = causes["churned"]["competitors"][0]
    add("causes", "Which competitors did churned customers go to?", agents=["book"], tools=["get_causes"],
        facts=[{"text": comp["name"]}, {"number": comp["accounts"]}])
    add("causes", f"How much ARR did we lose to {top_cause['cause'].lower()}?", agents=["book"],
        tools=["get_causes"], facts=[{"money": top_cause["arr_lost"]}])
    add("causes", "How many customers have churned?", agents=["book"], tools=["get_causes"],
        facts=[{"number": causes["churned"]["accounts"]}])
    best = causes["plays"][0]
    add("causes", "Which save play has worked best across the book?", agents=["book"], tools=["get_causes"],
        facts=[{"text": " ".join(best["action"].split()[1:])}, {"text": f"{best['renewed']} of {best['decided']}"}])

    # ---- what customers said
    inter = s.interactions.assign(sub=s.interactions["content"].map(subject_of))

    def with_subject(pat, active_only):
        rows = inter[inter["sub"].str.contains(pat, case=False)]
        if active_only:
            rows = rows[rows["customer_id"].isin(active)]
        return rows.sort_values("interaction_date", ascending=False)

    champ = with_subject("Champion changed roles", True)
    for _, row in champ.head(2).iterrows():
        r = active[row["customer_id"]]
        d = row["interaction_date"].date()
        add("notes", f"What has {r['name']}'s CSM noted about their champion?", agents=["account", "notes"],
            tools=["search_notes"], facts=[{"text": "champion"}, {"any_text": [d.isoformat(), long_date(d),
                                                                            f"{d.strftime('%b')} {d.day}"]}],
            retrieval={"query": "champion changed roles", "customer_id": row["customer_id"],
                       "contains": "Champion changed roles"})
    cancel = with_subject("cancellation", False)
    add("notes", "Has any customer asked about cancellation terms?", agents=["notes"], tools=["search_notes"],
        facts=[{"any_text": sorted({n.split(" [")[0] for n in cancel["company_name"]})}],
        retrieval={"query": "asked about cancellation terms", "customer_id": None, "contains": "cancellation"})
    complaint = with_subject("Complaint about support", True)
    add("notes", "Which customers complained about support response time?", agents=["notes"],
        tools=["search_notes"], facts=[{"any_text": sorted({n.split(" [")[0] for n in complaint["company_name"]})}],
        retrieval={"query": "complaint about support response time", "customer_id": None,
                   "contains": "support response"})
    vantage = with_subject("Evaluating Vantage", False)
    add("notes", "Which customers mentioned evaluating Vantage Analytics?", agents=["notes"],
        tools=["search_notes"], facts=[{"text": "Vantage Analytics"},
                                       {"any_text": sorted({n.split(" [")[0] for n in vantage["company_name"]})}],
        retrieval={"query": "evaluating Vantage Analytics", "customer_id": None, "contains": "Vantage"})
    drop = with_subject("Usage drop", True)
    r = active[drop.iloc[0]["customer_id"]]
    add("notes", f"Has anyone flagged a usage drop at {r['name']}?", agents=["account", "notes"],
        tools=["search_notes"], facts=[{"text": "usage drop"}],
        retrieval={"query": "usage drop flagged", "customer_id": r["customer_id"], "contains": "Usage drop"})

    # ---- mixed
    champ_names = [active[c]["name"] for c in champ["customer_id"].unique()]
    add("mixed", "Which accounts have had a champion change roles, and what has worked for accounts like them?",
        agents=["book", "notes"], tools=[], facts=[{"any_text": [n.split(" [")[0] for n in champ_names]}])

    # ---- prediction (answered as a ranking, with the limitation)
    add("prediction", "Which 5 accounts will churn next quarter?", agents=["book"], tools=["find_accounts"],
        facts=[{"text": queue[0]["name"].split(" [")[0]}], limitation=True)
    add("prediction", "Which customers are most likely to cancel next year, and how much ARR is that?",
        agents=["book"], tools=["find_accounts"], facts=[], limitation=True)

    # ---- refusals
    add("refusal", f"Send {queue[0]['name']} a 20% renewal discount", out_of_scope=True)
    add("refusal", f"Update the risk band for {queue[2]['name']} to High", out_of_scope=True)
    add("refusal", "What's the capital of France?", out_of_scope=True)

    # Split: about 15 for tuning prompts, the rest held out. Fixed by position,
    # so a rebuild on the same data gives the same split.
    for i, q in enumerate(qs):
        q["split"] = "tune" if i % 8 in (0, 3, 5) else "heldout"
    return qs


def main() -> None:
    qs = build()
    OUT.write_text(json.dumps({"source": DATA, "built_from": "the served Salesforce CSVs, by code (no model)",
                               "questions": qs}, indent=1, default=str) + "\n")
    by_split = {s: sum(1 for q in qs if q["split"] == s) for s in ("tune", "heldout")}
    print(f"{len(qs)} questions → {OUT.relative_to(ROOT)} ({by_split})")


if __name__ == "__main__":
    main()
