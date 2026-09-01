"""
Exposure turns a probability into money, so its arithmetic has to be exact and
its caveats have to survive refactoring.

Uses a stub survival model rather than the fitted one: the point is the money
arithmetic and the unit reconciliation, not the fit, and a stub makes every
expected value hand-checkable. The fitted model is exercised end to end by
scripts/report_exposure.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.exposure import (  # noqa: E402
    ADOPTION_SCALE,
    CALIBRATION_UNDERSTATEMENT,
    ExposureModel,
)


class StubSurvival:
    """Hazard rises with adoption *falling*, so a play must reduce it."""

    def churn_probabilities(self, rows: pd.DataFrame, weeks: int = 13) -> np.ndarray:
        return np.clip(1.0 - rows["feature_adoption_rate"].to_numpy(dtype=float), 0.001, 0.999)

    def rank_book(self, rows: pd.DataFrame, weeks: int = 13) -> pd.DataFrame:
        p = self.churn_probabilities(rows, weeks)
        lift = p / (float(np.mean(p)) or 1e-4)
        from model.survival import ChurnSurvivalModel
        return pd.DataFrame(
            {"probability": p, "lift": lift, "band": [ChurnSurvivalModel.band(x) for x in lift]},
            index=rows.index,
        )


class StubPlay:
    def __init__(self, gain: float, action: str = "Ran an adoption workshop", cases: int = 7):
        self.median_adoption_gain = gain     # percentage points, 0-100
        self.action = action
        self.cases = cases


class StubPlaybook:
    def __init__(self, gain: float = 38.5):
        self.gain = gain

    def plays_for(self, risk_reason, segment=None, limit=1):
        return [StubPlay(self.gain)]


@pytest.fixture
def frame() -> pd.DataFrame:
    return pd.DataFrame({
        "customer_id": ["CUST-001", "CUST-002", "CUST-003"],
        "feature_adoption_rate": [0.20, 0.50, 0.99],
        "adoption_mean_12w": [0.20, 0.50, 0.99],
    })


@pytest.fixture
def scored() -> list:
    return [
        {"customer_id": "CUST-001", "name": "Alpha", "segment": "Enterprise",
         "arr": 400_000.0, "risk_reason": "Low engagement"},
        {"customer_id": "CUST-002", "name": "Beta", "segment": "SMB",
         "arr": 100_000.0, "risk_reason": "Low engagement"},
        {"customer_id": "CUST-003", "name": "Gamma", "segment": "SMB",
         "arr": 50_000.0, "risk_reason": "Low engagement"},
    ]


def test_expected_loss_is_probability_times_arr(frame, scored):
    book = ExposureModel(StubSurvival()).for_book(frame, scored)
    # Stub hazard is 1 - adoption, so these are exact.
    assert book.expected_loss == pytest.approx(0.80 * 400_000 + 0.50 * 100_000 + 0.01 * 50_000)


def test_upper_bound_applies_the_measured_understatement(frame, scored):
    book = ExposureModel(StubSurvival()).for_book(frame, scored)
    assert book.expected_loss_upper == pytest.approx(
        book.expected_loss * CALIBRATION_UNDERSTATEMENT
    )
    assert CALIBRATION_UNDERSTATEMENT > 1.0, "the model underpredicts; the bound must be above"


def test_band_shares_sum_to_one(frame, scored):
    book = ExposureModel(StubSurvival()).for_book(frame, scored)
    assert sum(b.share_of_loss for b in book.by_band) == pytest.approx(1.0)
    assert sum(b.accounts for b in book.by_band) == book.accounts


def test_adoption_gain_is_rescaled_not_applied_raw(frame, scored):
    """A 38.5-point gain is +0.385 on a 0-1 feature, not +38.5.

    Applied raw it saturates every account and the whole book looks rescuable,
    silently and with no error.
    """
    model = ExposureModel(StubSurvival(), StubPlaybook(gain=38.5))
    gains, _, _ = model._adoption_gains(["Low engagement"], ["SMB"])
    assert gains[0] == pytest.approx(38.5 / ADOPTION_SCALE)

    beta = next(e for e in model.exposures(frame, scored) if e.name == "Beta")
    # 0.50 + 0.385 = 0.885 adoption -> hazard 0.115 under the stub.
    assert beta.mitigated_probability == pytest.approx(0.115)


def test_adoption_is_capped_at_full(frame, scored):
    """Gamma is at 0.99; a 38.5-point play cannot take it to 1.375."""
    model = ExposureModel(StubSurvival(), StubPlaybook(gain=38.5))
    gamma = next(e for e in model.exposures(frame, scored) if e.name == "Gamma")
    assert gamma.mitigated_probability == pytest.approx(0.001)   # clipped floor of the stub
    assert gamma.recoverable <= gamma.expected_loss


def test_recoverable_never_exceeds_exposure(frame, scored):
    model = ExposureModel(StubSurvival(), StubPlaybook())
    for e in model.exposures(frame, scored):
        assert 0.0 <= (e.recoverable or 0.0) <= e.expected_loss + 1e-6


def test_a_play_that_raises_risk_recovers_nothing(frame, scored):
    """Clipping at zero, so a counterproductive play cannot net off someone else's."""

    class Backwards(StubSurvival):
        def churn_probabilities(self, rows, weeks=13):
            return np.clip(rows["feature_adoption_rate"].to_numpy(dtype=float), 0.001, 0.999)

    model = ExposureModel(Backwards(), StubPlaybook())
    assert all((e.recoverable or 0.0) == 0.0 for e in model.exposures(frame, scored))


def test_no_playbook_means_no_recovery_claim(frame, scored):
    book = ExposureModel(StubSurvival(), playbook=None).for_book(frame, scored)
    assert book.recoverable == 0.0
    assert all(e.play is None and e.recoverable is None for e in book.top_accounts)
    # A dict with no recovery keys is how the API says "not estimated" rather
    # than reporting a confident zero.
    assert "recoverable" not in book.top_accounts[0].to_dict()


def test_negligible_gain_is_not_reported_as_a_play(frame, scored):
    model = ExposureModel(StubSurvival(), StubPlaybook(gain=0.4))   # 0.004 on 0-1
    assert all(e.play is None for e in model.exposures(frame, scored))


def test_for_customer_matches_the_book(frame, scored):
    model = ExposureModel(StubSurvival(), StubPlaybook())
    one = model.for_customer(frame, scored, "CUST-002")
    inside = next(e for e in model.exposures(frame, scored) if e.customer_id == "CUST-002")
    assert one.expected_loss == inside.expected_loss
    assert one.lift == inside.lift, "lift is defined against the book, not the account"


def test_unknown_customer_is_none_not_an_error(frame, scored):
    assert ExposureModel(StubSurvival()).for_customer(frame, scored, "CUST-999") is None


def test_unscored_rows_are_dropped_not_counted(frame, scored):
    """Churned accounts sit in the warehouse but are not scored. Counting them
    at full ARR would inflate the book."""
    extra = pd.concat([frame, pd.DataFrame({
        "customer_id": ["CUST-DEAD"], "feature_adoption_rate": [0.1],
        "adoption_mean_12w": [0.1],
    })], ignore_index=True)
    book = ExposureModel(StubSurvival()).for_book(extra, scored)
    assert book.accounts == 3


def test_empty_overlap_is_an_error_not_a_zero(frame):
    """A silent $0 book would read as good news."""
    with pytest.raises(ValueError):
        ExposureModel(StubSurvival()).for_book(frame, [])


def test_basis_states_the_survivorship_bias(frame, scored):
    basis = ExposureModel(StubSurvival(), StubPlaybook()).for_book(frame, scored).to_dict()["basis"]
    assert "sensitivity" in basis or "not a causal" in basis
    assert "successful interventions were recorded" in basis
