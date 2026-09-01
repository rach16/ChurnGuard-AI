"""
The alarm on retrieval quality.

There has been a scoreboard -- scripts/benchmark_retrieval.py -- but nothing
attached to it. Retrieval could degrade from 0.971 to 0.6 and every test would
still pass. This fails the build instead.

Runs keyword-only (semantic_weight=0.0), which is what makes it free: no
embeddings are computed, no vector store is populated, and no paid API is
called. That is not a compromise on this corpus -- pure BM25 ties the tuned
hybrid on the single-entity questions, which is why DEFAULT_SEMANTIC_WEIGHT is
0.25 rather than something larger.

What this does NOT cover: whether the generated answer is faithful to the
documents retrieved. That needs an LLM judge, costs money, and is phase 3.1.
This alarm is about finding the right documents, nothing more.

Thresholds are floors set below the measured values, not the values themselves.
BM25 over a fixed corpus is deterministic, so any drop is a real change rather
than noise -- the margin exists so that a legitimate corpus edit does not fail
the build for a rounding difference.
"""

from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

# Measured 2026-09-01 by scripts/benchmark_retrieval.py, keyword-only, k=5.
#
#   single  n=34  hit=0.971  recall=0.941  mrr=0.848
#   all     n=61  hit=0.836  recall=0.609  mrr=0.672
#
# "single" is questions naming one to three entities -- what retrieval is
# actually responsible for. "all" includes aggregate questions whose answer is a
# computation over the whole dataset and lives in no single document; the
# warehouse answers those, so a low number there is expected, not a defect.
FLOORS = {
    "single": {"hit_rate": 0.95, "recall": 0.90, "mrr": 0.80},
    "all": {"hit_rate": 0.80, "recall": 0.55, "mrr": 0.60},
}


@pytest.fixture(scope="module")
def keyword_retrieval(monkeypatch_session=None):
    """Corpus loaded once, no vectors indexed."""
    import os

    # Constructed, never called. The retriever builds a chat and an embedding
    # model up front, and both refuse to construct without a credential -- but
    # keyword-only retrieval never invokes either.
    os.environ.setdefault("OPENAI_API_KEY", "test-key-not-called")
    os.environ["QDRANT_URL"] = ":memory:"

    from core.rag_retrievers import ChurnRAGRetriever

    r = ChurnRAGRetriever(collection_name="ci_regression", qdrant_url=":memory:")
    r.load_and_process_documents(str(ROOT / "data"), index_vectors=False)
    return r


@pytest.fixture(scope="module")
def golden():
    from benchmark_retrieval import load_golden

    return load_golden()


def measure(retriever, golden, subset: str) -> dict:
    """Score with the benchmark's own functions, so the gate and the report
    can never disagree about what a number means."""
    from benchmark_retrieval import score

    return score(golden, partial(retriever.hybrid_retrieval, semantic_weight=0.0),
                 k=5, subset=subset)


def test_keyword_only_touches_no_vector_store(keyword_retrieval):
    """The property that keeps this free. If a vector store were populated here,
    771 documents would be embedded on every CI run."""
    assert keyword_retrieval.vector_store is None
    assert len(keyword_retrieval.documents) > 0


@pytest.mark.parametrize("subset", ["single", "all"])
def test_retrieval_has_not_regressed(keyword_retrieval, golden, subset):
    got = measure(keyword_retrieval, golden, subset)
    floors = FLOORS[subset]

    failures = [
        f"{metric}={got[metric]:.3f} below floor {floor:.2f}"
        for metric, floor in floors.items()
        if got[metric] < floor
    ]
    assert not failures, (
        f"retrieval regressed on the '{subset}' question set (n={got['n']}): "
        + "; ".join(failures)
        + ". Re-run scripts/benchmark_retrieval.py to see the full picture."
    )


def test_the_golden_set_has_not_shrunk(golden):
    """A gate that silently scores fewer questions is not a gate.

    Deleting rows from the golden set would raise every average and pass this
    file while measuring less, which is how an evaluation quietly stops meaning
    anything.
    """
    answerable = [g for g in golden if g["expected"]]
    assert len(golden) >= 65, f"golden set shrank to {len(golden)} questions"
    assert len(answerable) >= 61, f"only {len(answerable)} carry expected context"


def test_single_entity_questions_still_dominate_the_gate(golden):
    """The single-entity subset is the meaningful one. If it shrank, the strict
    floors above would apply to almost nothing."""
    single = [g for g in golden if g["expected"] and len(g["expected"]) <= 3]
    assert len(single) >= 34, f"single-entity questions dropped to {len(single)}"
