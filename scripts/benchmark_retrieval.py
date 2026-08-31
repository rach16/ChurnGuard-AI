"""
Measure retrieval quality against the golden set's expected_context.

RAGAS scores retrieval by asking an LLM whether the retrieved text supports the
answer. That costs thousands of judge calls and reports a model's opinion. The
golden set already records which customer_ids actually answer each question, so
retrieval quality here is a set comparison: did the right document come back?

That is ground truth rather than an opinion, it costs only the query embeddings,
and it runs in seconds -- so it can gate CI in a way a RAGAS run never could.

Aggregate questions ("which segment has the highest churn rate") are reported
separately. Their answer is a computation over the whole dataset, present in no
single document, so no retriever can hit them and averaging them in understates
retrieval quality rather than measuring it.

Usage:
    python3 scripts/benchmark_retrieval.py
    python3 scripts/benchmark_retrieval.py --k 10 --methods naive,hybrid
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.ERROR)

GOLDEN = ROOT / "golden-masters" / "churn_golden_master.csv"


def load_golden() -> list[dict]:
    with open(GOLDEN, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["expected"] = {t for t in r["expected_context"].split(",") if t}
    return rows


def retrieved_ids(docs) -> set[str]:
    """Every identifier a retrieved document can be matched on."""
    ids = set()
    for d in docs:
        for key in ("customer_id", "doc_id"):
            if value := d.metadata.get(key):
                ids.add(value)
    return ids


def score(golden: list[dict], retrieve, k: int, subset: str = "all") -> dict:
    """Hit rate, recall and MRR over questions carrying expected context.

    subset='single' restricts to questions naming one or two entities. Those are the
    questions retrieval is genuinely responsible for. Questions listing many
    contributing customers ("total ARR lost to churn") score partial credit here, but
    a partial set cannot answer them -- the answer is an aggregate over all of them.
    Reporting both keeps the distinction visible instead of averaging it away.
    """
    answerable = [g for g in golden if g["expected"]]
    if subset == "single":
        answerable = [g for g in answerable if len(g["expected"]) <= 3]

    hits = 0
    recall_sum = 0.0
    rr_sum = 0.0

    for g in answerable:
        try:
            docs = retrieve(query=g["question"], k=k)
        except Exception as e:
            print(f"    ! retrieval failed: {type(e).__name__}: {e}", file=sys.stderr)
            continue

        got = retrieved_ids(docs)
        overlap = g["expected"] & got

        hits += bool(overlap)
        recall_sum += len(overlap) / len(g["expected"])

        # Reciprocal rank of the first correct document.
        for rank, d in enumerate(docs, start=1):
            if retrieved_ids([d]) & g["expected"]:
                rr_sum += 1.0 / rank
                break

    n = len(answerable)
    return {
        "n": n,
        "hit_rate": hits / n if n else 0.0,
        "recall": recall_sum / n if n else 0.0,
        "mrr": rr_sum / n if n else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark retrieval against expected_context")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--methods", default="naive,parent_document,hybrid")
    ap.add_argument("--collection", default="churn_corpus_bench")
    args = ap.parse_args()

    from core.rag_retrievers import ChurnRAGRetriever

    golden = load_golden()
    answerable = [g for g in golden if g["expected"]]
    aggregate = [g for g in golden if not g["expected"]]

    print(f"golden set: {len(golden)} questions "
          f"({len(answerable)} with expected context, {len(aggregate)} aggregate)")
    print(f"aggregate questions are excluded -- no retriever can answer them\n")

    r = ChurnRAGRetriever(collection_name=args.collection)
    n_docs = r.load_and_process_documents(data_folder=str(ROOT / "data"))
    print(f"indexed {n_docs} documents, k={args.k}\n")

    available = {
        "naive": r.naive_retrieval,
        "parent_document": r.parent_document_retrieval,
        "multi_query": r.multi_query_retrieval,
    }
    if hasattr(r, "hybrid_retrieval"):
        available["hybrid"] = r.hybrid_retrieval

    header = (f"{'method':20} | {'single-entity (n=?)':^26} | {'all answerable':^26}")
    print(header)
    print(f"{'':20} | {'hit':>8}{'recall':>9}{'MRR':>9} | {'hit':>8}{'recall':>9}{'MRR':>9}")
    print("-" * len(header))

    results = {}
    for name in args.methods.split(","):
        name = name.strip()
        if name not in available:
            print(f"{name:20} | {'not implemented':^26} |")
            continue
        single = score(golden, available[name], args.k, subset="single")
        every = score(golden, available[name], args.k, subset="all")
        results[name] = {"single": single, "all": every}
        print(f"{name:20} | {single['hit_rate']:>8.3f}{single['recall']:>9.3f}{single['mrr']:>9.3f}"
              f" | {every['hit_rate']:>8.3f}{every['recall']:>9.3f}{every['mrr']:>9.3f}")

    if results:
        n_single = next(iter(results.values()))["single"]["n"]
        n_all = next(iter(results.values()))["all"]["n"]
        print(f"\nsingle-entity n={n_single}, all answerable n={n_all}")

    print("\nsingle-entity = questions naming 1-3 entities; what retrieval is responsible for")
    print("hit_rate      = share of questions where any expected document was retrieved")
    print("recall        = mean fraction of expected documents retrieved")
    print("MRR           = mean reciprocal rank of the first correct document")
    return 0


if __name__ == "__main__":
    sys.exit(main())
