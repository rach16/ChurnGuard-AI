"""
Run the assistant against its golden and red-team sets (ADR-0016 section 9).

Asks the real model, on the served Salesforce data, with an in-memory notes
index built with the real embedding model; scores with ``assistant.evals``.

    uv run --extra model python scripts/eval_assistant.py                       # held-out + red team
    uv run --extra model python scripts/eval_assistant.py --split tune          # the set prompts may be tuned on
    uv run --extra model python scripts/eval_assistant.py --mode single         # one agent, every tool
    uv run --extra model python scripts/eval_assistant.py --gate metrics/assistant_eval_baseline.json

Writes ``metrics/assistant_eval_<split>_<mode>.json`` (metrics and every
question's result). With ``--gate`` it exits 1 when the run falls below the
baseline (``assistant.evals.gate``). Needs OPENAI_API_KEY; without it, exits 0
and says it skipped, like the database tests.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "backend")]
logging.disable(logging.CRITICAL)

GOLDEN = ROOT / "golden-masters" / "assistant_salesforce.json"
REDTEAM = ROOT / "golden-masters" / "assistant_redteam.json"
DATA = ROOT / "data" / "salesforce"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=("heldout", "tune", "all"), default="heldout")
    ap.add_argument("--mode", choices=("multi", "single"), default="multi")
    ap.add_argument("--redteam", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--gate", type=Path, help="baseline metrics to compare against; exit 1 on a regression")
    ap.add_argument("--write-baseline", type=Path, help="write this run's metrics as the baseline")
    ap.add_argument("--only", help="comma-separated question ids")
    args = ap.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set: the assistant eval needs the real model. Skipped.")
        return 0

    from qdrant_client import QdrantClient

    from assistant.evals import aggregate, redteam_check, score_question
    from assistant.graph import Assistant
    from assistant.model import LangChainModel, judge_model
    from assistant.notes_index import NotesIndex, account_id_of, documents_from
    from assistant.tools import ToolContext, Tools
    from core.health_scoring import CustomerHealthScorer
    from core.llm import embedding_model
    from core.plays import PlaybookEngine
    from core.story import StoryBuilder

    started = time.time()
    builder = StoryBuilder(CustomerHealthScorer(str(DATA)), PlaybookEngine(str(DATA)))
    as_of = builder.scorer.as_of.date().isoformat()
    names = [r["name"] for r in builder.queue()]
    emb = embedding_model()

    def index_with(extra: list[dict]) -> NotesIndex:
        idx = NotesIndex(QdrantClient(location=":memory:"), emb.embed_documents, emb.embed_query)
        idx.build("eval-snapshot", documents_from(builder.scorer) + extra)
        return idx

    clean = index_with([])
    red = json.loads(REDTEAM.read_text())
    by_name = {r["name"]: r for r in builder.queue()}
    planted = [{"account_id": account_id_of(by_name[n["account"]]["customer_id"]),
                "customer_id": by_name[n["account"]]["customer_id"], "account": n["account"], "kind": n["kind"],
                "date": n["date"], "text": n["text"], "source_id": f"planted-{i}"}
               for i, n in enumerate(red["planted_notes"])]
    injected = index_with(planted) if args.redteam else None
    agent_model, judge = LangChainModel(), judge_model()

    def context(tenant: str = "default", notes=None) -> ToolContext:
        return ToolContext(tenant=tenant, served_tenant="default", builder=builder, snapshot_id="eval-snapshot",
                           as_of=as_of, notes=notes if notes is not None else clean)

    def ask(question: str, ctx: ToolContext, account_id=None) -> tuple[dict, dict]:
        tools = Tools(ctx)
        assistant = Assistant(agent_model, tools, judge=judge, mode=args.mode)
        answer = assistant.ask(question, account_id)
        return answer, assistant.results

    golden = json.loads(GOLDEN.read_text())["questions"]
    if args.split != "all":
        golden = [q for q in golden if q["split"] == args.split]
    if args.only:
        wanted = set(args.only.split(","))
        golden = [q for q in golden if q["id"] in wanted]

    def run_golden(q: dict) -> dict:
        try:
            answer, results = ask(q["question"], context(), q.get("account_id"))
        except Exception as e:  # noqa: BLE001 - one failed question is a result, not the end of the run
            return {"id": q["id"], "kind": q["kind"], "split": q["split"], "error": f"{type(e).__name__}: {e}",
                    "tools_ok": False, "routing_ok": None, "facts": [{"fact": f, "hit": False} for f in q["facts"]],
                    "refusal_ok": False, "limitation_ok": False if q["expect"].get("limitation") else None,
                    "wrong_numbers": 0, "drafted": 0, "removed": 0, "seconds": None, "cost": 0.0,
                    "tools_called": [], "agents": [], "plan_source": None, "missed": q["facts"]}
        row = score_question(q, answer, names=names, cited_lookup=results)
        row["answer"] = [c["text"] for c in answer.get("answer", [])]
        row["could_not_confirm"] = answer.get("could_not_confirm", [])
        return row

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(run_golden, golden))

    # Retrieval recall at 5, model-free: is the expected passage in the top 5?
    retrieval = []
    for q in golden:
        r = q.get("retrieval")
        if r:
            hits = clean.search(r["query"], customer_id=r["customer_id"], k=5)
            retrieval.append(any(r["contains"].lower() in h.text.lower() for h in hits))

    red_rows = []
    if args.redteam:
        def run_red(case: dict) -> dict:
            ctx = (context("globex") if case.get("setup") == "other_tenant"
                   else context(notes=injected) if case.get("setup") == "planted_notes" else context())
            try:
                answer, _ = ask(case["question"], ctx)
            except Exception as e:  # noqa: BLE001
                return {"id": case["id"], "passed": False, "problems": [f"{type(e).__name__}: {e}"]}
            out = redteam_check(case, answer)
            out["answer"] = [c["text"] for c in answer.get("answer", [])]
            out["seconds"], out["out_of_scope"] = answer.get("seconds"), answer.get("out_of_scope")
            return out

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            red_rows = list(ex.map(run_red, red["cases"]))

    metrics = aggregate(rows, retrieval=retrieval, redteam=red_rows if args.redteam else None)
    metrics.update({"split": args.split, "mode": args.mode, "model": agent_model.name, "judge": judge.name,
                    "wall_seconds": round(time.time() - started, 1),
                    "measured": time.strftime("%Y-%m-%d", time.gmtime())})
    out = ROOT / "metrics" / f"assistant_eval_{args.split}_{args.mode}.json"
    out.write_text(json.dumps({"metrics": metrics, "questions": rows, "redteam": red_rows}, indent=1,
                              default=str) + "\n")
    print(json.dumps(metrics, indent=1))
    print(f"→ {out.relative_to(ROOT)}")
    misses = [(r["id"], r.get("missed"), r.get("error")) for r in rows if r.get("missed") or r.get("error")]
    for m in misses:
        print("  missed:", m)
    for c in red_rows:
        if not c["passed"]:
            print("  red team failed:", c["id"], c["problems"])

    if args.write_baseline:
        args.write_baseline.write_text(json.dumps(metrics, indent=1) + "\n")
        print(f"baseline → {args.write_baseline}")
    if args.gate:
        from assistant.evals import gate

        failures = gate(metrics, json.loads(args.gate.read_text()))
        for f in failures:
            print("GATE:", f)
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
