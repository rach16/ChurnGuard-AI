"""
The account story, the queue and the book's causes (ADR-0015, 8.5.1), on both
sources.

What these pin: a story is sentences and dated signals over real fields, adverse
first; the queue's reason is evidence, not the scorer's label repeated 109 times;
the causes come from the accounts that churned; nothing invented reaches a
response; and the story never disagrees with the score it explains.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from core.health_scoring import CustomerHealthScorer  # noqa: E402
from core.plays import PlaybookEngine  # noqa: E402
from core.signals import classify, subject_of  # noqa: E402
from core.story import RENEWAL_SOON_DAYS, StoryBuilder, money  # noqa: E402
from tests.test_salesforce_site import INVENTED, SALESFORCE, _keys, api_on  # noqa: E402, F401

__all__ = ["api_on"]

_builders: dict[str, StoryBuilder] = {}


def builder_for(folder: str) -> StoryBuilder:
    if folder not in _builders:
        _builders[folder] = StoryBuilder(CustomerHealthScorer(folder), PlaybookEngine(folder))
    return _builders[folder]


# ----------------------------------------------------------------- notes

def test_notes_become_dated_signals_with_a_polarity():
    risk = classify("Champion changed roles Logged by CSM: champion changed roles.", "2026-08-04")
    assert risk.polarity == "adverse" and risk.label == "Champion changed roles" and risk.date == "2026-08-04"
    comp = classify("Evaluating Vantage Analytics Logged by CSM: evaluating vantage analytics.", "2026-05-01")
    assert comp.polarity == "adverse" and comp.competitor == "Vantage Analytics" and comp.label == "Evaluating Vantage Analytics"
    good = classify("QBR completed", "2026-09-15")
    assert good.polarity == "good" and good.label == "QBR completed"
    assert classify("Save play: Held executive business review Challenge: Pricing Concerns", "2026-01-01") is None
    assert classify("Lunch", "2026-01-01") is None
    assert subject_of("Expansion discussion Logged by CSM: expansion discussion.") == "Expansion discussion"


def test_money_reads_like_the_site():
    assert money(56000) == "$56K" and money(1090000) == "$1.09M" and money(995000) == "$995K" and money(1_000_000) == "$1M"


# ----------------------------------------------------------------- story

@pytest.mark.parametrize("folder", ["data", SALESFORCE])
def test_a_story_is_a_sentence_and_dated_signals_adverse_first(folder):
    b = builder_for(folder)
    first = b.scorer.score_active_customers()[0]
    story = b.story(first["id"])
    assert story["headline"].startswith(first["name"]) and story["headline"].endswith(".")
    assert money(first["arr"]) in story["headline"] and first["risk_level"] in story["headline"]
    kinds = [s["kind"] for s in story["signals"]]
    assert kinds == sorted(kinds, key={"adverse": 0, "neutral": 1, "good": 2}.get)
    assert all(s["source"] and s["text"] for s in story["signals"])
    # The score and the reason are the scorer's, untouched.
    assert story["risk"] == {"score": first["risk_score"], "band": first["risk_level"],
                             "at_risk": story["risk"]["at_risk"], "top_reason": first["risk_reason"]}
    weights = sum(f["weight"] for f in story["how_built"]["factors"])
    assert abs(weights - 1.0) < 1e-9
    assert abs(sum(f["contribution"] for f in story["how_built"]["factors"]) - first["risk_score"]) < 0.3
    assert not (_keys(story) & INVENTED)


def test_the_salesforce_story_names_the_evidence_not_the_label():
    b = builder_for(SALESFORCE)
    starlight = next(c for c in b.scorer.score_active_customers() if c["name"] == "Starlight Freight Co")
    story = b.story(starlight["id"])
    texts = [s["text"] for s in story["signals"]]
    assert any(t.startswith("2 of ") and "seats active" in t for t in texts)
    champion = next(s for s in story["signals"] if s["text"] == "Champion changed roles")
    assert champion["kind"] == "adverse" and champion["source"] == "CSM note" and champion["date"] == "2026-08-04"
    assert "Only 1 week of usage" in " ".join(texts)          # honest about thin data
    assert story["renewal"]["days"] > 0 and story["renewal"]["date"] == "2027-09-27"
    assert "renews in" in story["headline"] and "seats active" in story["headline"]
    assert story["plays"] and story["plays"][0]["renewed"] == 6 and story["plays"][0]["decided"] == 7
    assert story["how_built"]["history_weeks"] == 1


def test_a_renewal_inside_the_window_is_adverse_only_above_low():
    b = builder_for("data")
    rows = b.queue()
    soon = [r for r in rows if r["renewal_days"] is not None and r["renewal_days"] <= RENEWAL_SOON_DAYS]
    assert soon
    for r in soon[:10]:
        renewal = next(s for s in b.story(r["id"])["signals"] if s["key"] == "renewal")
        assert renewal["kind"] == ("neutral" if r["risk_level"] == "Low" else "adverse")


def test_an_unknown_customer_has_no_story():
    assert builder_for(SALESFORCE).story(999999) is None


# ----------------------------------------------------------------- queue

@pytest.mark.parametrize("folder", ["data", SALESFORCE])
def test_the_queue_is_every_active_account_riskiest_first_with_evidence(folder):
    b = builder_for(folder)
    rows = b.queue()
    scored = b.scorer.score_active_customers()
    assert [r["id"] for r in rows] == [c["id"] for c in scored]
    assert sum(r["at_risk"] for r in rows) == len(b.scorer.top_of_book(scored))
    assert all(r["reason"] for r in rows)
    # 109 of 132 rows said "Low engagement" before; the evidence phrase varies.
    assert len({r["reason"] for r in rows}) > 10
    assert not any(r["reason"] in ("Low engagement", "Feature gaps") for r in rows)
    assert sum(1 for r in rows if r["renewal_days"] is not None) > len(rows) * 0.9


# ---------------------------------------------------------------- causes

def test_causes_come_from_the_accounts_that_churned():
    c = builder_for(SALESFORCE).causes()
    assert c["churned"]["accounts"] == 55
    by = {x["cause"]: x["accounts"] for x in c["churned"]["by_cause"]}
    assert by["Competition"] == 15 and by["Champion Left"] == 12 and by["Poor Support"] == 7
    assert c["churned"]["by_cause"][0]["cause"] == "Competition"
    assert all(x["arr_lost"] > 0 for x in c["churned"]["by_cause"])
    assert c["churned"]["competitors"][0] == {"name": "Clarity CRM", "accounts": 5}
    assert c["active"]["accounts"] == 132
    assert sum(x["accounts"] for x in c["active"]["by_signal"]) == 132
    assert c["active"]["renewing_soon"]["days"] == RENEWAL_SOON_DAYS
    assert 0 < c["active"]["renewing_soon"]["accounts"] < 132
    best = c["plays"][0]
    assert best["action"] == "Created internal champions program" and (best["renewed"], best["decided"]) == (6, 7)
    assert sum(p["decided"] + p["pending"] for p in c["plays"]) == 48


def test_causes_on_the_synthetic_set_have_no_play_outcomes():
    c = builder_for("data").causes()
    assert c["churned"]["accounts"] == 71 and c["plays"] == []


# ---------------------------------------------------------------- routes

def test_story_queue_and_causes_routes(api_on):
    api = api_on(SALESFORCE)
    first = api.state.health_scorer.score_active_customers()[0]["id"]
    story = asyncio.run(api.get_customer_story(first))
    assert story["headline"] and story["salesforce_url"] is None      # no connection in a test
    assert not (_keys(story) & INVENTED)
    queue = asyncio.run(api.get_book_queue())
    assert queue["total"] == 132 and queue["at_risk"] == 27 and queue["arr_at_risk"] > 0
    assert queue["accounts"][0]["id"] == first
    causes = asyncio.run(api.get_book_causes())
    assert causes["churned"]["accounts"] == 55
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.get_customer_story(999999))
    assert e.value.status_code == 404
    # The builder follows the scorer: a reload gives a fresh one.
    before = api.state.story
    api._init_health_scorer()
    asyncio.run(api.get_book_queue())
    assert api.state.story is not before
