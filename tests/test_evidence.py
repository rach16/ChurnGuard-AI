"""
Evidence must come from the customer it is about.

This is the guarantee the whole explanation layer rests on, and until now it
rested on nothing but the shape of the code. The original Q&A path failed
exactly here -- asked about one company it returned five others -- so the
regression is real, not hypothetical.

Also covers /evaluation-results, whose deliberate 404 was being swallowed by a
bare `except Exception` and re-emitted as a 500.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from langchain_core.documents import Document

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.evidence import CustomerEvidence, DRIVER_TERMS  # noqa: E402


def doc(customer_id: str, source_type: str, text: str, doc_id: str) -> Document:
    return Document(
        page_content=text,
        metadata={"customer_id": customer_id, "source_type": source_type, "doc_id": doc_id},
    )


@pytest.fixture
def index() -> CustomerEvidence:
    return CustomerEvidence([
        doc("CUST-001", "churn_analysis",
            "Engagement has been declining steadily for six weeks and usage is "
            "well below the level this account sustained last year.", "A-1"),
        doc("CUST-001", "support_history",
            "Three support tickets were escalated this month and the resolution "
            "time on each exceeded the agreed target.", "A-2"),
        doc("CUST-002", "churn_analysis",
            "Engagement has been declining sharply and adoption of the core "
            "feature set never progressed past initial setup.", "B-1"),
    ])


def test_evidence_comes_only_from_the_named_customer(index):
    """The guarantee. A cross-account result is the failure this replaced."""
    for e in index.for_customer("CUST-001", "Low engagement", limit=5):
        assert e.doc_id.startswith("A-"), f"{e.doc_id} belongs to another account"


def test_a_similar_passage_on_another_account_is_not_returned(index):
    """CUST-002's text is near-identical, which is what a vector search would rank.

    Selection is by key, so similarity cannot pull it in.
    """
    ids = {e.doc_id for e in index.for_customer("CUST-001", "Low engagement", limit=5)}
    assert "B-1" not in ids


def test_unknown_customer_returns_nothing_rather_than_anything(index):
    assert index.for_customer("CUST-999", "Low engagement") == []


def test_an_unmapped_driver_returns_nothing(index):
    """No terms means no ranking basis. Returning arbitrary passages would be worse."""
    assert index.for_customer("CUST-001", "Mercury in retrograde") == []


def test_results_are_one_per_document(index):
    """Three results should be three pieces of evidence, not one quoted thrice."""
    results = index.for_customer("CUST-001", "Support issues", limit=3)
    assert len({e.doc_id for e in results}) == len(results)


def test_ranking_puts_the_matching_driver_first(index):
    top = index.for_customer("CUST-001", "Support issues", limit=1)
    assert top and top[0].doc_id == "A-2"

    top = index.for_customer("CUST-001", "Low engagement", limit=1)
    assert top and top[0].doc_id == "A-1"


def test_every_driver_the_scorer_emits_has_terms():
    """A driver with no mapping silently yields no evidence at all."""
    from core.health_scoring import RISK_REASONS
    missing = [r for r in RISK_REASONS.values() if r not in DRIVER_TERMS]
    assert not missing, f"drivers with no evidence terms: {missing}"


def test_results_are_deterministic(index):
    a = [e.doc_id for e in index.for_customer("CUST-001", "Low engagement", limit=3)]
    b = [e.doc_id for e in index.for_customer("CUST-001", "Low engagement", limit=3)]
    assert a == b


# ------------------------------------------------------- evaluation-results

def test_missing_baseline_is_404_not_500(monkeypatch, tmp_path):
    """A bare `except Exception` was catching the deliberate 404 and re-raising
    it as a 500 with the 404 text in the body."""
    sys.path.insert(0, str(ROOT / "src" / "backend"))
    from fastapi.testclient import TestClient

    import api  # noqa: E402

    monkeypatch.setattr(api, "ROOT", tmp_path)      # no metrics/ directory
    monkeypatch.delenv("API_KEYS", raising=False)

    r = TestClient(api.app).get("/evaluation-results")
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
    assert "3.1" in r.json()["detail"], "the 404 should say how to produce a baseline"


def test_small_corpora_still_return_evidence():
    """BM25's IDF is zero when a term appears in one of two passages, so every
    score collapses and the customer silently gets nothing. Found by the ranking
    test above; this pins the fallback."""
    tiny = CustomerEvidence([
        doc("CUST-A", "churn_analysis",
            "Support tickets have risen sharply this quarter and several remain "
            "unresolved past the agreed resolution target.", "T-1"),
        doc("CUST-A", "customer_profile",
            "The account renewed last year on a standard commercial agreement "
            "covering the core platform and two integrations.", "T-2"),
    ])
    results = tiny.for_customer("CUST-A", "Support issues", limit=2)
    assert results, "two passages must still yield evidence"
    assert results[0].doc_id == "T-1", "the passage about support must rank first"
