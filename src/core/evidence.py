"""
Evidence for one customer's risk.

The model says how likely an account is to churn. This says what in that account's
record supports it — quoted, attributed, and scoped to that customer alone.

Deliberately not a vector search. Each customer has around five documents, so the
retrieval problem is not "find the right customer" but "find the right passage
within a customer we already know". Selecting by key and ranking passages with
BM25 is exact, free and identical on every request, and it makes returning another
customer's story structurally impossible — which was the original failure of the
general Q&A path.

This is the architecture's boundary in practice: the warehouse and the model own
every number, the corpus owns the narrative, and nothing here produces a score.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# Terms that make a passage relevant to a given risk driver. The scorer names the
# driver; the corpus uses ordinary prose, so the bridge is explicit rather than
# left to embedding similarity.
DRIVER_TERMS: Dict[str, List[str]] = {
    "Low engagement": ["engagement", "adoption", "usage", "active", "login", "declining"],
    "Declining usage": ["declining", "decrease", "dropped", "engagement", "usage", "plateau"],
    "Feature gaps": ["feature", "missing", "functionality", "integration", "adoption", "request"],
    "Support issues": ["support", "ticket", "issue", "bug", "escalation", "resolution", "csat"],
    "Poor onboarding": ["onboarding", "training", "documentation", "setup", "satisfaction", "csat"],
}

# Which document types can carry evidence, and how they are described to a reader.
SOURCE_LABELS = {
    "churn_analysis": "Risk analysis",
    "support_history": "Support history",
    "interaction_history": "Interaction history",
    "customer_profile": "Account profile",
    "success_story": "Success story",
}

MAX_PASSAGE_CHARS = 320


@dataclass
class Evidence:
    """One quoted passage, with where it came from."""

    text: str
    source: str
    doc_id: str
    score: float

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "source": self.source,
            "doc_id": self.doc_id,
            "relevance": round(self.score, 3),
        }


class CustomerEvidence:
    """Finds passages in a customer's own record that bear on their risk driver."""

    def __init__(self, documents: List[Document]):
        # Index by customer so lookup is exact rather than approximate.
        self.by_customer: Dict[str, List[Document]] = {}
        for doc in documents:
            cid = doc.metadata.get("customer_id")
            if cid:
                self.by_customer.setdefault(cid, []).append(doc)
        logger.info(f"Evidence index: {len(self.by_customer)} customers")

    @staticmethod
    def _passages(doc: Document) -> List[str]:
        """Split a document into passages a person would actually read.

        Blank-line separated blocks, because these documents are written as prose
        with headed sections. Sentence splitting would cut mid-argument.
        """
        blocks = [b.strip() for b in re.split(r"\n\s*\n", doc.page_content) if b.strip()]
        out = []
        for b in blocks:
            collapsed = re.sub(r"\s+", " ", b)
            if len(collapsed) < 40:          # headings and single fields carry no argument
                continue
            out.append(collapsed[:MAX_PASSAGE_CHARS])
        return out

    def for_customer(
        self,
        customer_id: str,
        risk_reason: str,
        limit: int = 3,
    ) -> List[Evidence]:
        """Passages from this customer's record most relevant to their risk driver."""
        docs = self.by_customer.get(customer_id, [])
        if not docs:
            return []

        terms = DRIVER_TERMS.get(risk_reason, [])
        if not terms:
            return []

        from rank_bm25 import BM25Okapi

        candidates: List[tuple[str, Document]] = []
        for doc in docs:
            if doc.metadata.get("source_type") not in SOURCE_LABELS:
                continue
            for passage in self._passages(doc):
                candidates.append((passage, doc))

        if not candidates:
            return []

        tokenised = [re.findall(r"[a-z0-9]+", p.lower()) for p, _ in candidates]
        scores = BM25Okapi(tokenised).get_scores(terms)

        # BM25's IDF collapses on a tiny corpus. With two passages, a term in one
        # of them scores log((2-1+0.5)/(1+0.5)) = log(1) = 0, so every score is
        # zero and the caller below reads that as "nothing relevant" and returns
        # an empty list. A customer with few documents would silently get no
        # evidence at all, which is the failure mode this module exists to avoid.
        #
        # IDF is meaningless at this corpus size anyway -- it measures how
        # surprising a term is across documents, and three documents cannot say.
        # Fall back to how many distinct driver terms each passage actually
        # contains, which is what IDF was standing in for.
        if not any(score > 0 for score in scores):
            wanted = set(terms)
            scores = [float(len(wanted & set(tokens))) for tokens in tokenised]

        ranked = sorted(zip(scores, range(len(candidates))), key=lambda x: -x[0])

        results: List[Evidence] = []
        seen_docs: set[str] = set()
        for score, idx in ranked:
            if score <= 0:
                break
            passage, doc = candidates[idx]
            doc_id = doc.metadata.get("doc_id", "")
            # One passage per document, so three results are three separate pieces
            # of evidence rather than one document quoted three ways.
            if doc_id in seen_docs:
                continue
            seen_docs.add(doc_id)
            results.append(
                Evidence(
                    text=passage,
                    source=SOURCE_LABELS.get(doc.metadata.get("source_type", ""), "Record"),
                    doc_id=doc_id,
                    score=float(score),
                )
            )
            if len(results) >= limit:
                break

        return results
