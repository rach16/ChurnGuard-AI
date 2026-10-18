"""
The assistant's tools, the versioned notes index and the privacy scrub
(ADR-0016, 4.6.1). No model is called: embeddings are a deterministic hash.

What these pin: the tenant and every account id are checked by code; totals
and rankings are fields of the result, over all matches; every result carries
its snapshot version; a failed index build leaves the previous one serving; a
rollback reuses a kept collection without re-embedding; one account's search
never returns another's notes; emails and phone numbers never reach a model.
"""

from __future__ import annotations

import hashlib
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from assistant.notes_index import IndexBuildFailed, NotesIndex, documents_from  # noqa: E402
from assistant.privacy import MAX_TEXT, scrub, scrub_text  # noqa: E402
from assistant.tools import MAX_ROWS, NAMES, SPECS, ToolContext, Tools  # noqa: E402
from core.health_scoring import CustomerHealthScorer  # noqa: E402
from core.plays import PlaybookEngine  # noqa: E402
from core.story import StoryBuilder  # noqa: E402

SALESFORCE = "data/salesforce"
_builders: dict[str, StoryBuilder] = {}


def builder(folder: str) -> StoryBuilder:
    if folder not in _builders:
        _builders[folder] = StoryBuilder(CustomerHealthScorer(folder), PlaybookEngine(folder))
    return _builders[folder]


class HashEmbedder:
    """Bag of words hashed into 64 dimensions; similar words, similar vectors.
    Counts its calls so a test can see a rollback did not re-embed."""

    DIM = 64

    def __init__(self, fail_after: int | None = None):
        self.documents_embedded = 0
        self.fail_after = fail_after

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.DIM
        v[0] = 0.1                                   # never the zero vector
        for w in text.lower().split():
            v[int(hashlib.sha1(w.encode()).hexdigest(), 16) % self.DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]

    def documents(self, texts: list[str]) -> list[list[float]]:
        if self.fail_after is not None and self.documents_embedded >= self.fail_after:
            raise RuntimeError("embedding provider timed out")
        self.documents_embedded += len(texts)
        return [self._vec(t) for t in texts]

    def query(self, text: str) -> list[float]:
        return self._vec(text)


def make_index(embedder: HashEmbedder | None = None) -> tuple[NotesIndex, HashEmbedder]:
    from qdrant_client import QdrantClient

    e = embedder or HashEmbedder()
    return NotesIndex(QdrantClient(location=":memory:"), e.documents, e.query, tenant="default"), e


def tools_for(folder: str = SALESFORCE, *, tenant: str = "default", served: str = "default",
              notes: NotesIndex | None = None, snapshot: str = "snap-1") -> Tools:
    b = builder(folder)
    return Tools(ToolContext(tenant=tenant, served_tenant=served, builder=b, snapshot_id=snapshot,
                             as_of=b.scorer.as_of.date().isoformat(), notes=notes))


# ---------------------------------------------------------------- privacy

def test_emails_phones_and_secrets_are_removed_and_facts_are_not():
    text = ("Spoke to dana.lee@acme.com, call back on (415) 555-0172 or +44 20 7946 0958. "
            "Renewal 2026-10-12, ARR $56,000, account ACC-30029, 14,977 calls left. "
            "token=abcdefghijklmnopqrstuvwxyz")
    out = scrub_text(text)
    assert "dana.lee@acme.com" not in out and "[email removed]" in out
    assert "555-0172" not in out and "7946" not in out and out.count("[phone removed]") == 2
    assert "abcdefghijklmnop" not in out
    for fact in ("2026-10-12", "$56,000", "ACC-30029", "14,977"):
        assert fact in out
    assert len(scrub_text("x" * 1000)) == MAX_TEXT
    assert scrub({"a": ["call 415-555-0172"], "n": 3}) == {"a": ["call [phone removed]"], "n": 3}


# ------------------------------------------------------------------ tools

def test_every_spec_is_a_tool_and_every_result_carries_its_version():
    assert set(NAMES) == {"find_accounts", "get_story", "get_plays", "get_book_summary", "get_causes",
                          "search_notes"}
    t = tools_for()
    for spec in SPECS:
        assert callable(getattr(t, spec["name"]))
        assert spec["parameters"]["type"] == "object" and spec["description"]
    for result in (t.call("get_book_summary"), t.call("get_story", {"account_id": -1}), t.call("nope")):
        assert result["snapshot_id"] == "snap-1" and result["as_of"] == builder(SALESFORCE).scorer.as_of.date().isoformat()


def test_find_accounts_totals_cover_every_match_not_only_the_rows_shown():
    t = tools_for()
    queue = builder(SALESFORCE).queue()
    r = t.call("find_accounts", {"at_risk_only": True, "limit": 5})["data"]
    at_risk = [q for q in queue if q["at_risk"]]
    assert r["count"] == 27 == len(at_risk)
    assert r["total_arr"] == pytest.approx(sum(q["arr"] for q in at_risk))
    assert r["shown"] == 5 and r["shown_arr"] == pytest.approx(sum(a["arr"] for a in r["accounts"]))
    assert r["sorted_by"] == "risk_score desc"
    assert [a["rank"] for a in r["accounts"]] == [1, 2, 3, 4, 5]
    scores = [a["risk_score"] for a in r["accounts"]]
    assert scores == sorted(scores, reverse=True)

    by_arr = t.call("find_accounts", {"sort": "arr", "limit": 3})["data"]
    assert by_arr["sorted_by"] == "arr desc" and by_arr["count"] == 132
    assert [a["arr"] for a in by_arr["accounts"]] == sorted((q["arr"] for q in queue), reverse=True)[:3]
    assert t.call("find_accounts", {"limit": 500})["data"]["shown"] == MAX_ROWS


def test_find_accounts_filters_and_resolves_a_name():
    t = tools_for()
    hit = t.call("find_accounts", {"name": "starlight freight"})["data"]
    assert hit["count"] == 1 and hit["accounts"][0]["name"] == "Starlight Freight Co"
    champion = t.call("find_accounts", {"signal": "champion"})["data"]
    assert champion["count"] > 0
    assert all(any("champion" in s.lower() for s in a["adverse_signals"]) for a in champion["accounts"])
    soon = t.call("find_accounts", {"renews_within_days": 90, "segment": "Enterprise"})["data"]
    assert all(a["segment"] == "Enterprise" and 0 <= a["renewal_days"] <= 90 for a in soon["accounts"])


def test_story_plays_summary_and_causes_are_the_sites_own():
    t = tools_for()
    b = builder(SALESFORCE)
    first = b.queue()[0]
    story = t.call("get_story", {"account_id": first["id"]})["data"]
    site = b.story(first["id"])
    assert story["headline"] == site["headline"] and story["risk"] == site["risk"]
    assert [s["text"] for s in story["signals"]] == [s["text"] for s in site["signals"]]
    plays = t.call("get_plays", {"account_id": first["id"]})["data"]["plays"]
    assert plays and {"action", "renewed", "decided"} <= set(plays[0])
    summary = t.call("get_book_summary")["data"]
    assert summary["active_accounts"] == 132 and summary["at_risk_accounts"] == 27
    assert summary["history_weeks"] == 1
    assert t.call("get_causes")["data"]["churned"]["accounts"] == 55


def test_the_tenant_and_every_account_id_are_checked_by_code():
    # A signed-in person of another tenant has no book, whatever they ask for.
    other = tools_for(tenant="globex", served="default")
    for name, args in (("get_book_summary", {}), ("find_accounts", {}), ("get_causes", {}),
                       ("get_story", {"account_id": builder(SALESFORCE).queue()[0]["id"]})):
        r = other.call(name, args)
        assert r["ok"] is False and "No data is served for your organization" in r["error"]

    # A real account id from another book is refused, not answered.
    synthetic_id = builder("data").queue()[0]["id"]
    salesforce_ids = {q["id"] for q in builder(SALESFORCE).queue()}
    assert synthetic_id not in salesforce_ids
    r = tools_for().call("get_story", {"account_id": synthetic_id})
    assert r["ok"] is False and "No active account" in r["error"]
    r = tools_for().call("get_plays", {"account_id": "drop table"})
    assert r["ok"] is False and "must be a number" in r["error"]


def test_a_bad_call_is_a_result_never_an_exception():
    t = tools_for()
    assert t.call("delete_everything")["ok"] is False
    assert "Bad arguments" in t.call("get_story", {"id": 1})["error"]
    assert t.call("search_notes", {"query": "pricing"})["error"] == "Notes search is not available on this deployment."


# ------------------------------------------------------------ notes index

def test_documents_are_every_note_and_case_of_the_served_snapshot():
    scorer = builder(SALESFORCE).scorer
    docs = documents_from(scorer)
    assert len([d for d in docs if d["kind"] == "note"]) == 278
    assert len([d for d in docs if d["kind"] == "case"]) == 239
    assert all(d["text"] and d["date"] and isinstance(d["account_id"], int) for d in docs)


def test_build_moves_the_alias_only_after_a_complete_build():
    index, e = make_index()
    docs = documents_from(builder(SALESFORCE).scorer)
    assert index.target() is None and index.search("pricing") == []
    first = index.build("snap-1", docs)
    assert first["action"] == "built" and index.target() == index.collection_for("snap-1")
    assert index.current_snapshot() == "snap-1"
    assert index.build("snap-1", docs)["action"] == "unchanged"
    assert e.documents_embedded == len(docs)                      # the second call embedded nothing


def test_a_failed_build_leaves_the_previous_index_serving():
    index, e = make_index()
    docs = documents_from(builder(SALESFORCE).scorer)
    index.build("snap-1", docs)
    e.fail_after = e.documents_embedded + 100                     # fails part-way through the next build
    with pytest.raises(IndexBuildFailed, match="timed out"):
        index.build("snap-2", docs)
    assert index.current_snapshot() == "snap-1"
    names = {c.name for c in index.client.get_collections().collections}
    assert index.collection_for("snap-2") not in names            # the partial build is gone
    assert index.search("pricing pushback")                       # still answering from snap-1


def test_a_rollback_reuses_the_kept_collection_and_old_ones_are_pruned():
    index, e = make_index()
    docs = documents_from(builder(SALESFORCE).scorer)
    index.build("snap-1", docs)
    index.build("snap-2", docs)
    embedded = e.documents_embedded
    back = index.build("snap-1", docs)                            # roll back
    assert back["action"] == "switched" and e.documents_embedded == embedded
    assert index.current_snapshot() == "snap-1"
    index.build("snap-3", docs)
    names = {c.name for c in index.client.get_collections().collections}
    assert names == {index.collection_for("snap-3"), index.collection_for("snap-1")}


def test_one_accounts_search_never_returns_another_accounts_notes():
    index, _ = make_index()
    scorer = builder(SALESFORCE).scorer
    index.build("snap-1", documents_from(scorer))
    starlight = next(c for c in scorer.score_active_customers() if c["name"] == "Starlight Freight Co")
    hits = index.search("champion changed roles renewal", customer_id=starlight["customer_id"], k=8)
    assert hits and all(h.customer_id == starlight["customer_id"] for h in hits)
    assert any("Champion changed roles" in h.text for h in hits)
    book_wide = index.search("evaluating competitor", k=8)
    assert len({h.customer_id for h in book_wide}) > 1
    assert all(h.snapshot_id == "snap-1" for h in book_wide)


def test_the_search_tool_scopes_cites_and_scrubs():
    index, _ = make_index()
    scorer = builder(SALESFORCE).scorer
    index.build("snap-1", documents_from(scorer))
    t = tools_for(notes=index)
    starlight = t.call("find_accounts", {"name": "Starlight Freight"})["data"]["accounts"][0]
    r = t.call("search_notes", {"query": "champion", "account_id": starlight["account_id"]})
    assert r["ok"] and r["data"]["passages"]
    for p in r["data"]["passages"]:
        assert p["account_id"] == starlight["account_id"] and p["date"] and p["source_id"] and p["snapshot_id"] == "snap-1"
    assert tools_for(notes=index, tenant="globex").call("search_notes", {"query": "champion"})["ok"] is False
