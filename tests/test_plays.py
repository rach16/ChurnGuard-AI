"""
The playbook must not invent advice.

Every recommendation is a solution that was applied to comparable accounts with a
recorded outcome, so the properties worth guarding are about evidence rather than
output quality: enough cases behind each play, honest confidence labels, and
nothing returned for a driver with no matching cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.plays import MIN_CASES, DRIVER_TO_CHALLENGE, PlaybookEngine  # noqa: E402


@pytest.fixture(scope="module")
def engine() -> PlaybookEngine:
    return PlaybookEngine(str(ROOT / "data"))


def test_every_scorer_driver_is_mapped():
    """A driver with no mapping silently returns no advice, which looks like a bug."""
    from core.health_scoring import RISK_REASONS

    unmapped = set(RISK_REASONS.values()) - set(DRIVER_TO_CHALLENGE)
    assert not unmapped, f"drivers with no challenge mapping: {sorted(unmapped)}"


def test_every_driver_has_evidence(engine):
    """A mapping pointing at an empty category produces confident silence."""
    thin = {d: n for d, n in engine.coverage().items() if n < MIN_CASES}
    assert not thin, f"drivers backed by fewer than {MIN_CASES} cases: {thin}"


@pytest.mark.parametrize("segment", ["SMB", "Commercial", "Enterprise"])
def test_plays_never_fall_below_the_evidence_floor(engine, segment):
    """Surfacing a single case as a recommendation gives it false authority."""
    for driver in DRIVER_TO_CHALLENGE:
        for play in engine.plays_for(driver, segment):
            assert play.cases >= MIN_CASES, (
                f"{driver}/{segment}: '{play.action}' rests on {play.cases} cases"
            )


def test_confidence_reflects_sample_size(engine):
    """The label is a claim about evidence and must follow from the counts."""
    for driver in DRIVER_TO_CHALLENGE:
        for play in engine.plays_for(driver, "SMB"):
            if play.confidence == "strong":
                assert play.same_segment_cases >= 3
            elif play.confidence == "moderate":
                assert play.cases >= 6
            else:
                assert play.confidence == "limited"


def test_ranking_prefers_evidence_over_effect_size(engine):
    """A large gain from two cases is weaker guidance than a modest one from twelve."""
    plays = engine.plays_for("Support issues", "SMB")
    if len(plays) >= 2:
        keys = [(p.same_segment_cases, p.cases) for p in plays]
        assert keys == sorted(keys, reverse=True), (
            f"plays are not ordered by evidence: {keys}"
        )


def test_unknown_driver_returns_nothing(engine):
    """Better to return no advice than to guess at a category."""
    assert engine.plays_for("Something the scorer never emits", "SMB") == []


def test_outcomes_are_plausible(engine):
    """Adoption is measured in percentage points and cannot exceed 100."""
    for driver in DRIVER_TO_CHALLENGE:
        for play in engine.plays_for(driver, "Enterprise"):
            assert 0 < play.median_adoption_gain <= 100
            assert 0 <= play.median_support_reduction <= 100
