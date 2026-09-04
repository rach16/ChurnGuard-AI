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

Writes its scores to metrics/retrieval_benchmark.csv so /evaluation-results can
serve them. Printing to stdout and nothing else was the original defect: the
Evaluation page told the reader to run this script, and running it changed that
page not at all, because nothing on disk changed.

Usage:
    python3 scripts/benchmark_retrieval.py
    python3 scripts/benchmark_retrieval.py --k 10 --methods naive,hybrid
    python3 scripts/benchmark_retrieval.py --out /dev/null   # print only
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.ERROR)

GOLDEN = ROOT / "golden-masters" / "churn_golden_master.csv"
DEFAULT_OUT = ROOT / "metrics" / "retrieval_benchmark.csv"

# Column order is the contract with /evaluation-results. The two cohorts are kept
# apart rather than averaged: single-entity is what retrieval is responsible for,
# and folding the aggregate questions in understates it.
FIELDNAMES = [
    "method",
    "single_hit_rate", "single_recall", "single_mrr",
    "all_hit_rate", "all_recall", "all_mrr",
    "n_single", "n_all", "k", "generated_at",
]


def write_results(results: dict, path: Path, k: int) -> None:
    """Write one row per method, rates kept as 0-1 rather than percentages.

    generated_at travels with the scores because a baseline's age is part of
    reading it: a number measured against a corpus two rebuilds ago is not a
    current claim, and a file with no date cannot tell you that.
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for name, cohorts in results.items():
            single, every = cohorts["single"], cohorts["all"]
            writer.writerow({
                "method": name,
                "single_hit_rate": f"{single['hit_rate']:.4f}",
                "single_recall": f"{single['recall']:.4f}",
                "single_mrr": f"{single['mrr']:.4f}",
                "all_hit_rate": f"{every['hit_rate']:.4f}",
                "all_recall": f"{every['recall']:.4f}",
                "all_mrr": f"{every['mrr']:.4f}",
                "n_single": single["n"],
                "n_all": every["n"],
                "k": k,
                "generated_at": stamp,
            })


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
    # The live collection, not a throwaway one. 'churn_corpus_bench' is always
    # empty on a fresh machine, so every run re-embedded all 771 documents --
    # about a cent and roughly a minute, while the page promised the run cost
    # nothing and took seconds. Pointing at the populated collection binds to
    # the existing index instead and leaves only the query embeddings to pay
    # for. Pass --collection explicitly to score a different index.
    ap.add_argument("--collection", default="churn_corpus")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="CSV to write; the Evaluation page reads this file")
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
        write_results(results, args.out, args.k)
        print(f"wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    else:
        # No method scored, so there is nothing to record. Overwriting a good
        # baseline with an empty file would be worse than leaving it alone.
        print("\nno method produced scores; nothing written", file=sys.stderr)
        return 1

    print("\nsingle-entity = questions naming 1-3 entities; what retrieval is responsible for")
    print("hit_rate      = share of questions where any expected document was retrieved")
    print("recall        = mean fraction of expected documents retrieved")
    print("MRR           = mean reciprocal rank of the first correct document")
    return 0


if __name__ == "__main__":
    sys.exit(main())
