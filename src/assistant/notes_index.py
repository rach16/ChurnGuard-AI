"""
The notes-and-cases index, versioned per snapshot (ADR-0016 section 4).

One Qdrant collection per served snapshot (``notes_<tenant>_<snapshot>``), read
through one alias (``notes_<tenant>``). A build fills the new collection in
full, checks its point count against the documents it was given, and only then
moves the alias, in a single call Qdrant applies atomically. Queries during a
build read the previous collection; a failed build deletes its partial
collection and leaves the alias where it was. Rolling back to a snapshot whose
collection is still kept moves the alias without re-embedding. The alias
target and the one before it are kept; older collections are deleted.

Every build is full, so a note deleted in Salesforce is gone from the index at
the next promotion. Search is hybrid: vector similarity from Qdrant and BM25
over the same documents, fused by reciprocal rank, and filtered to one customer
whenever the question is about one account, so another account's notes cannot
be retrieved for it.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger("churnguard.assistant.notes")

Embed = Callable[[list[str]], list[list[float]]]
RRF_K = 60
BATCH = 64
_TOKEN = re.compile(r"[a-z0-9]+")


class IndexBuildFailed(Exception):
    """The new collection could not be built or verified; the alias did not move."""


@dataclass(frozen=True)
class Passage:
    account_id: int
    customer_id: str
    account: str
    kind: str                 # note | case
    date: str
    text: str
    source_id: str
    snapshot_id: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def account_id_of(customer_id: str) -> Optional[int]:
    """The integer id the site uses, as the scorer derives it."""
    try:
        return int(str(customer_id).split("-")[1])
    except (IndexError, ValueError):
        return None


def documents_from(scorer) -> list[dict[str, Any]]:
    """One document per CSM note and per case, from the tables the scorer is
    serving, so the index and the figures come from the same snapshot."""
    names = dict(zip(scorer.customers["customer_id"], scorer.customers["company_name"]))
    docs: list[dict[str, Any]] = []
    for _, r in scorer.interactions.iterrows():
        aid = account_id_of(r["customer_id"])
        text = str(r.get("content") or "").strip()
        if aid is None or not text:
            continue
        docs.append({"account_id": aid, "customer_id": r["customer_id"],
                     "account": names.get(r["customer_id"], r["customer_id"]), "kind": "note",
                     "date": pd.Timestamp(r["interaction_date"]).date().isoformat(),
                     "text": text, "source_id": f"note-{len(docs)}"})
    for _, r in scorer.tickets.iterrows():
        aid = account_id_of(r["customer_id"])
        parts = [str(r.get(c) or "").strip() for c in ("issue_type", "description", "resolution_notes")]
        text = ". ".join(p for p in parts if p and p.lower() != "nan")
        if aid is None or not text:
            continue
        docs.append({"account_id": aid, "customer_id": r["customer_id"],
                     "account": names.get(r["customer_id"], r["customer_id"]), "kind": "case",
                     "date": pd.Timestamp(r["created_date"]).date().isoformat(),
                     "text": text, "source_id": str(r.get("ticket_id") or f"case-{len(docs)}")})
    return docs


class NotesIndex:
    def __init__(self, client, embed_documents: Embed, embed_query: Callable[[str], list[float]],
                 *, tenant: str = "default"):
        self.client = client
        self.embed_documents = embed_documents
        self.embed_query = embed_query
        self.tenant = _safe(tenant) or "default"
        self.alias = f"notes_{self.tenant}"
        self._prefix = f"notes_{self.tenant}_"
        self._bm25: dict[str, tuple[Any, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- naming

    def collection_for(self, snapshot_id: str) -> str:
        return f"{self._prefix}{_safe(snapshot_id)[:40]}"

    def target(self) -> Optional[str]:
        """The collection the alias points at, or None before the first build."""
        for a in self.client.get_aliases().aliases:
            if a.alias_name == self.alias:
                return a.collection_name
        return None

    def current_snapshot(self) -> Optional[str]:
        target = self.target()
        if target is None:
            return None
        info = self._meta(target)
        return info.get("snapshot_id") if info else None

    def _meta(self, collection: str) -> Optional[dict[str, Any]]:
        points, _ = self.client.scroll(collection, limit=1, with_payload=True, with_vectors=False)
        return dict(points[0].payload) if points else {}

    def _exists(self, collection: str) -> bool:
        return any(c.name == collection for c in self.client.get_collections().collections)

    # ---------------------------------------------------------------- build

    def build(self, snapshot_id: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
        """Make ``snapshot_id`` the served index. Returns what happened:
        ``unchanged`` (already served), ``switched`` (kept collection reused, as
        on a rollback) or ``built``. Raises IndexBuildFailed with the alias
        unmoved."""
        from qdrant_client import models

        with self._lock:
            collection = self.collection_for(snapshot_id)
            previous = self.target()
            if previous == collection:
                return {"action": "unchanged", "collection": collection, "documents": len(docs)}
            if self._exists(collection) and self.client.count(collection, exact=True).count == len(docs):
                self._swap(collection, previous)
                logger.info(f"notes index: alias {self.alias} → {collection} (kept, no re-embedding)")
                return {"action": "switched", "collection": collection, "documents": len(docs)}
            try:
                if self._exists(collection):
                    self.client.delete_collection(collection)
                dim = len(self.embed_query("dimension probe"))
                self.client.create_collection(
                    collection, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))
                self.client.create_payload_index(collection, "customer_id",
                                                 field_schema=models.PayloadSchemaType.KEYWORD)
                for start in range(0, len(docs), BATCH):
                    chunk = docs[start:start + BATCH]
                    vectors = self.embed_documents([d["text"] for d in chunk])
                    self.client.upsert(collection, points=[
                        models.PointStruct(id=start + i, vector=v,
                                           payload={**d, "snapshot_id": snapshot_id})
                        for i, (d, v) in enumerate(zip(chunk, vectors))])
                counted = self.client.count(collection, exact=True).count
                if counted != len(docs):
                    raise IndexBuildFailed(f"built {counted} points for {len(docs)} documents")
            except Exception as e:  # noqa: BLE001 - the alias must not move on any failure
                if self._exists(collection):
                    self.client.delete_collection(collection)
                if isinstance(e, IndexBuildFailed):
                    raise
                raise IndexBuildFailed(f"{type(e).__name__}: {e}") from e
            self._swap(collection, previous)
            logger.info(f"notes index: built {collection} with {len(docs)} documents; alias moved"
                        + (f" from {previous}" if previous else ""))
            return {"action": "built", "collection": collection, "documents": len(docs)}

    def _swap(self, collection: str, previous: Optional[str]) -> None:
        from qdrant_client import models

        ops: list[Any] = []
        if previous is not None:
            ops.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=self.alias)))
        ops.append(models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=collection, alias_name=self.alias)))
        self.client.update_collection_aliases(change_aliases_operations=ops)
        keep = {collection, previous}
        for c in self.client.get_collections().collections:
            if c.name.startswith(self._prefix) and c.name not in keep:
                self.client.delete_collection(c.name)
                self._bm25.pop(c.name, None)

    # ---------------------------------------------------------------- search

    def _keyword_index(self, collection: str):
        """BM25 over the collection's documents, built once per collection."""
        if collection not in self._bm25:
            from rank_bm25 import BM25Okapi

            docs: list[dict[str, Any]] = []
            offset = None
            while True:
                points, offset = self.client.scroll(collection, limit=256, offset=offset,
                                                    with_payload=True, with_vectors=False)
                docs.extend(dict(p.payload) for p in points)
                if offset is None:
                    break
            self._bm25[collection] = (BM25Okapi([_tokens(d["text"]) for d in docs]) if docs else None, docs)
        return self._bm25[collection]

    def search(self, query: str, *, customer_id: Optional[str] = None, k: int = 5) -> list[Passage]:
        from qdrant_client import models

        collection = self.target()
        if collection is None:
            return []
        flt = (models.Filter(must=[models.FieldCondition(
            key="customer_id", match=models.MatchValue(value=customer_id))]) if customer_id else None)
        hits = self.client.query_points(collection, query=self.embed_query(query), query_filter=flt,
                                        limit=k * 4, with_payload=True).points
        vector_rank = {h.payload["source_id"]: (i, dict(h.payload)) for i, h in enumerate(hits)}

        bm25, docs = self._keyword_index(collection)
        keyword_rank: dict[str, tuple[int, dict[str, Any]]] = {}
        if bm25 is not None:
            scores = bm25.get_scores(_tokens(query))
            ranked = sorted((i for i in range(len(docs))
                             if not customer_id or docs[i]["customer_id"] == customer_id),
                            key=lambda i: -scores[i])
            for rank, i in enumerate(r for r in ranked if scores[r] > 0):
                if rank >= k * 4:
                    break
                keyword_rank[docs[i]["source_id"]] = (rank, docs[i])

        fused: dict[str, tuple[float, dict[str, Any]]] = {}
        for ranks in (vector_rank, keyword_rank):
            for sid, (rank, payload) in ranks.items():
                score, _ = fused.get(sid, (0.0, payload))
                fused[sid] = (score + 1.0 / (RRF_K + rank + 1), payload)
        top = sorted(fused.values(), key=lambda x: -x[0])[:k]
        return [Passage(account_id=int(p["account_id"]), customer_id=p["customer_id"], account=p["account"],
                        kind=p["kind"], date=p["date"], text=p["text"], source_id=p["source_id"],
                        snapshot_id=p["snapshot_id"], score=round(s, 4)) for s, p in top]
