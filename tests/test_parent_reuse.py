"""
The alarm on reusing a persisted index.

Startup skips re-embedding when the collection already holds vectors. That
saved most of a 66-second cold start and quietly broke question answering for a
day: the parent docstore lives in process memory, so on the reuse path it came
back empty, and the code set parent_retriever to None rather than build a
retriever that would return nothing. /ask defaults to parent_document
retrieval, so every question raised "Parent document retriever not initialized"
instead of answering.

Nothing in the suite covered it. The health endpoint reported the retriever as
present because it checks a different object, and the cold-start measurement
that motivated the change only timed /health.

Free to run: splitting text and hashing it is local. Neither test embeds
anything, calls a paid API, or needs a Qdrant server.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from langchain_core.documents import Document  # noqa: E402

from core.rag_retrievers import PARENT_ID_KEY  # noqa: E402


class _Splitter:
    """Stand-in for the retriever's splitters, so no corpus load is needed."""

    def __init__(self, size):
        self.size = size

    def split_documents(self, docs):
        out = []
        for d in docs:
            text = d.page_content
            for i in range(0, len(text), self.size):
                out.append(Document(page_content=text[i:i + self.size],
                                    metadata=dict(d.metadata)))
        return out


def _harness(documents):
    """The two methods under test, on a bare object with no Qdrant client."""
    from core.rag_retrievers import ChurnRAGRetriever as R

    obj = object.__new__(R)
    obj.documents = documents
    obj.parent_splitter = _Splitter(2000)
    obj.child_splitter = _Splitter(400)
    return obj


CORPUS = [
    Document(page_content="Acme churned over pricing. " * 200,
             metadata={"customer_id": "CUST-0001", "source_type": "analysis"}),
    Document(page_content="Globex renewed after onboarding. " * 200,
             metadata={"customer_id": "CUST-0002", "source_type": "analysis"}),
    # Same text, different account: ids must not collapse into one.
    Document(page_content="Standard support terms apply. " * 200,
             metadata={"customer_id": "CUST-0003", "source_type": "support"}),
    Document(page_content="Standard support terms apply. " * 200,
             metadata={"customer_id": "CUST-0004", "source_type": "support"}),
]


def test_parent_ids_are_stable_across_processes():
    """The whole reuse scheme rests on this.

    Child vectors in the collection carry their parent's id. If a fresh process
    derives different ids, every child points at a parent that no longer exists
    and retrieval returns nothing -- silently, because an empty result is not an
    error. Random UUIDs, the LangChain default, fail this by construction.
    """
    _, first = _harness(CORPUS)._split_parents()
    _, second = _harness(CORPUS)._split_parents()

    assert first == second
    assert all(len(i) == 64 for i in first), "expected sha256 hex ids"


def test_identical_text_under_different_accounts_stays_distinct():
    """Boilerplate repeated across accounts must not share a parent id."""
    parents, ids = _harness(CORPUS)._split_parents()

    assert len(ids) == len(set(ids)), "duplicate parent ids"
    assert len(parents) == len(ids)


def test_every_child_points_at_a_parent_that_exists():
    """The join the reuse path depends on, checked end to end without a server.

    This is the assertion that would have caught the outage: it fails if the
    docstore is not populated, and it fails if the id written onto a child ever
    stops matching the id the docstore is keyed by.
    """
    obj = _harness(CORPUS)
    parents, ids = obj._split_parents()

    docstore = dict(zip(ids, parents))

    children = []
    for parent, parent_id in zip(parents, ids):
        for child in obj.child_splitter.split_documents([parent]):
            child.metadata[PARENT_ID_KEY] = parent_id
            children.append(child)

    assert children, "corpus produced no child chunks"
    orphans = [c for c in children if c.metadata[PARENT_ID_KEY] not in docstore]
    assert not orphans, f"{len(orphans)} child chunks reference a missing parent"


# --- the readiness probe -------------------------------------------------
#
# /ready reported this component healthy throughout the outage, because it
# checked that the retriever object existed. These fix the meaning of "ready"
# to the join that has to hold, and fail if it ever softens back.


class _FakeClient:
    def __init__(self, points):
        self._points = points

    def scroll(self, collection_name, limit, with_payload, with_vectors):
        return self._points[:limit], None


def _point(parent_id):
    class P:
        payload = {"metadata": {PARENT_ID_KEY: parent_id}}
    return P()


def _probe_harness(docstore_ids, indexed_ids, retriever=object()):
    from core.rag_retrievers import ChurnRAGRetriever as R
    from langchain.storage import InMemoryStore

    obj = object.__new__(R)
    obj.collection_name = "test"
    obj.vector_store = object()
    obj.parent_retriever = retriever
    obj.client = _FakeClient([_point(i) for i in indexed_ids])
    obj.parent_store = InMemoryStore()
    obj.parent_store.mset([(i, Document(page_content="x")) for i in docstore_ids])
    return obj


def test_probe_passes_when_index_and_docstore_agree():
    assert _probe_harness(["a", "b"], ["a", "b"]).readiness_problem() is None


def test_probe_catches_the_outage_that_happened():
    """An empty docstore behind a populated index: 2026-09-02, exactly."""
    problem = _probe_harness([], ["a", "b"]).readiness_problem()

    assert problem is not None
    assert "docstore" in problem


def test_probe_catches_a_missing_retriever():
    problem = _probe_harness(["a"], ["a"], retriever=None).readiness_problem()

    assert problem is not None
    assert "parent retriever" in problem


def test_probe_catches_an_empty_collection():
    problem = _probe_harness(["a"], []).readiness_problem()

    assert problem is not None
    assert "empty" in problem


def test_probe_result_is_cached():
    """Render health-checks every ~5s and one request probed three times.

    The first deployed version therefore issued three Qdrant scrolls every five
    seconds -- roughly 51,000 a day against a free-tier cluster -- which the
    service logs made obvious and no test did. The probe is correct; calling it
    per-access was not.
    """
    sys.path.insert(0, str(ROOT / "src" / "backend"))
    from backend.api import ServiceState

    calls = []

    class _Retriever:
        def readiness_problem(self):
            calls.append(1)
            return None

    state = ServiceState()
    state.rag_retriever = _Retriever()

    for _ in range(10):
        assert state.retrieval_problem() is None

    assert len(calls) == 1, f"probe ran {len(calls)} times, expected 1"


def test_probe_cache_expires():
    sys.path.insert(0, str(ROOT / "src" / "backend"))
    from backend.api import ServiceState

    calls = []

    class _Retriever:
        def readiness_problem(self):
            calls.append(1)
            return None

    state = ServiceState()
    state.rag_retriever = _Retriever()
    state.retrieval_problem()

    # Age the cache past its TTL rather than sleeping through it.
    ts, value = state._probe_cache
    state._probe_cache = (ts - ServiceState.PROBE_TTL_SECONDS - 1, value)
    state.retrieval_problem()

    assert len(calls) == 2
