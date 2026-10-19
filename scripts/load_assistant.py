"""
Load test of the assistant (ADR-0016 section 8, 4.6.4), in process.

Fires N questions at once through the same concurrency limit the route uses
(``assistant.service``), with the real model. Measures what the limit does
(how many run, how many are refused with a retry hint) and the latency of the
ones that run while sharing the model with the others. It does not measure the
network to Render or Vercel: a staging load test needs a signed-in session,
which a script here cannot create.

    uv run --extra model python scripts/load_assistant.py --n 20
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "backend")]
logging.disable(logging.CRITICAL)

QUESTIONS = [
    "How many accounts are at risk right now?",
    "Why is Starlight Freight Co at risk?",
    "Which at-risk accounts renew in the next 90 days?",
    "Why did customers leave?",
    "Which save play has worked best across the book?",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Skipped.")
        return 0

    from assistant import service
    from assistant.evals import cost
    from assistant.graph import Assistant
    from assistant.model import LangChainModel, judge_model
    from assistant.tools import ToolContext, Tools
    from core.health_scoring import CustomerHealthScorer
    from core.plays import PlaybookEngine
    from core.story import StoryBuilder

    data = str(ROOT / "data" / "salesforce")
    builder = StoryBuilder(CustomerHealthScorer(data), PlaybookEngine(data))
    ctx = ToolContext(tenant="default", served_tenant="default", builder=builder, snapshot_id="load",
                      as_of=builder.scorer.as_of.date().isoformat())
    agent, judge = LangChainModel(), judge_model()
    results: list[dict] = []
    lock = threading.Lock()
    gate = threading.Barrier(args.n)

    def one(i: int) -> None:
        question = QUESTIONS[i % len(QUESTIONS)]
        gate.wait()                                   # all at once
        started = time.monotonic()
        if not service.try_acquire():
            with lock:
                results.append({"i": i, "outcome": "refused (retry hint)", "seconds": round(time.monotonic() - started, 3)})
            return
        try:
            answer = Assistant(agent, Tools(ctx), judge=judge).ask(question)
            row = {"i": i, "outcome": "answered", "seconds": answer["seconds"],
                   "claims": len(answer["answer"]), "cost": cost(answer["usage_by_model"])}
        except Exception as e:  # noqa: BLE001
            row = {"i": i, "outcome": f"error: {type(e).__name__}", "seconds": round(time.monotonic() - started, 2)}
        finally:
            service.release()
        with lock:
            results.append(row)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(args.n)]
    wall = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = round(time.monotonic() - wall, 1)

    answered = sorted(r["seconds"] for r in results if r["outcome"] == "answered")
    summary = {
        "fired_at_once": args.n, "limit": service.MAX_CONCURRENT,
        "answered": len(answered), "refused_with_retry_hint": sum(r["outcome"].startswith("refused") for r in results),
        "errors": [r["outcome"] for r in results if r["outcome"].startswith("error")],
        "answered_seconds": answered, "slowest_refusal_ms": round(max(
            (r["seconds"] for r in results if r["outcome"].startswith("refused")), default=0) * 1000, 1),
        "wall_seconds": wall, "cost": round(sum(r.get("cost", 0) for r in results), 4),
        "measured": time.strftime("%Y-%m-%d", time.gmtime()),
    }
    print(json.dumps(summary, indent=1))
    (ROOT / "metrics" / "assistant_load.json").write_text(json.dumps(summary, indent=1) + "\n")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
