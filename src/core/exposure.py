"""
What the risk in the book is worth, and what acting on it might recover.

The queue says who is at risk and the playbook says what has worked. Neither says
how much any of it matters, which is the first question anyone with a budget asks.
This turns the model's quarterly probability into money, so the queue can be
ordered by exposure rather than by likelihood alone -- a 4x lift on a $12k account
is a worse use of a CSM's week than a 2x lift on a $400k one.

Three honesty constraints shape the whole module, and each is enforced in code
rather than left to a footnote:

1. **The dollar figure is an expectation, not a forecast.** Summed over a book it
   is meaningful; for a single account it is a probability times a number, and
   that account will either renew or not.

2. **The absolute probability is low by a measured factor.** The backtest in 7.3
   put the calibrated understatement at 1.80x (ADR-0009), so every total is
   reported as a range from the point estimate to that bound. Reporting the point
   estimate alone would understate exposure by a factor we already know.

3. **The recoverable figure is a sensitivity, not a causal claim.**
   `success_stories.csv` records only accounts where a solution was applied and an
   outcome followed. Nothing recorded the accounts where the same play was tried
   and failed, so the observed gain is survivorship-biased upward. What is
   computed here is the model's own response to a feature moving, which is a
   different and much weaker statement than "doing this recovers that money".

The share of exposure held by each band is the figure to plan against: it is a
ratio, so it survives the level bias that the dollar totals inherit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Measured, not assumed: scripts/backtest_survival.py, 2026-08-31. Isotonic
# calibration leaves the hazard understated by this factor because the base rate
# rises across the observation window and a model fitted earlier cannot know it.
# See ADR-0009. Re-measure this when the model is refitted; do not carry it
# forward as a constant of nature.
CALIBRATION_UNDERSTATEMENT = 1.80

# success_stories.csv records adoption on 0-100; the warehouse uses 0-1. These
# have to be reconciled before a recorded gain can be applied to a feature, and
# getting it wrong is silent -- a 38-point gain applied to a 0-1 feature saturates
# every account and makes the whole book look rescuable.
ADOPTION_SCALE = 100.0

# Features the counterfactual moves. Adoption only, deliberately: the stories
# record adoption as a before and after level in one consistent unit, so the delta
# is well defined. engagement_increase is recorded as a relative percent with no
# baseline, which cannot be applied to a bounded 0-1 score without inventing one.
ADOPTION_FEATURES = ("feature_adoption_rate", "adoption_mean_12w")

# Below this the arithmetic is noise: a play with a negligible recorded gain
# produces a recoverable figure indistinguishable from rounding.
MIN_ADOPTION_GAIN = 0.01


@dataclass
class CustomerExposure:
    """One account's ARR weighted by its probability of leaving this quarter."""

    customer_id: str
    name: str
    segment: str
    arr: float
    probability: float
    lift: float
    band: str
    expected_loss: float
    expected_loss_upper: float
    mitigated_probability: Optional[float] = None
    recoverable: Optional[float] = None
    play: Optional[str] = None
    play_cases: Optional[int] = None

    def to_dict(self) -> dict:
        out = {
            "customer_id": self.customer_id,
            "name": self.name,
            "segment": self.segment,
            "arr": round(self.arr),
            "probability": round(self.probability, 4),
            "lift": round(self.lift, 2),
            "band": self.band,
            "expected_loss": round(self.expected_loss),
            "expected_loss_upper": round(self.expected_loss_upper),
        }
        if self.recoverable is not None:
            out.update({
                "mitigated_probability": round(self.mitigated_probability, 4),
                "recoverable": round(self.recoverable),
                "play": self.play,
                "play_cases": self.play_cases,
            })
        return out


@dataclass
class BandExposure:
    """Exposure held by one likelihood band."""

    band: str
    accounts: int
    arr: float
    expected_loss: float
    share_of_loss: float        # fraction of total expected loss, 0-1

    def to_dict(self) -> dict:
        return {
            "band": self.band,
            "accounts": self.accounts,
            "arr": round(self.arr),
            "expected_loss": round(self.expected_loss),
            "share_of_loss": round(self.share_of_loss, 3),
        }


@dataclass
class BookExposure:
    """The whole book: what is at stake this quarter and how it concentrates."""

    horizon_weeks: int
    accounts: int
    total_arr: float
    expected_loss: float
    expected_loss_upper: float
    recoverable: float
    by_band: List[BandExposure]
    top_accounts: List[CustomerExposure]
    concentration_top_10: float     # share of expected loss in the worst 10 accounts

    def to_dict(self) -> dict:
        return {
            "horizon_weeks": self.horizon_weeks,
            "accounts": self.accounts,
            "total_arr": round(self.total_arr),
            "expected_loss": round(self.expected_loss),
            "expected_loss_upper": round(self.expected_loss_upper),
            "expected_loss_pct_of_arr": round(
                100 * self.expected_loss / self.total_arr, 2
            ) if self.total_arr else 0.0,
            "recoverable": round(self.recoverable),
            "concentration_top_10": round(self.concentration_top_10, 3),
            "by_band": [b.to_dict() for b in self.by_band],
            "top_accounts": [c.to_dict() for c in self.top_accounts],
            "basis": (
                f"Expected loss is P(churn within {self.horizon_weeks} weeks) x ARR, "
                f"summed. The upper bound applies the {CALIBRATION_UNDERSTATEMENT}x "
                "calibration understatement measured in the walk-forward backtest "
                "(ADR-0009). Recoverable is the model's response to adoption moving "
                "by the median gain recorded on comparable accounts -- a sensitivity, "
                "not a causal effect, and biased upward because only successful "
                "interventions were recorded."
            ),
        }


BAND_ORDER = ("Very high", "High", "Moderate", "Low")


class ExposureModel:
    """Turns likelihood into money, and plays into an upper bound on recovery."""

    def __init__(self, survival, playbook=None):
        """
        Args:
            survival: a fitted ChurnSurvivalModel
            playbook: a PlaybookEngine, or None to skip the recoverable estimate
        """
        self.survival = survival
        self.playbook = playbook

    # ------------------------------------------------------------ counterfactual

    def _adoption_gains(self, drivers: Sequence[str], segments: Sequence[str]) -> tuple:
        """The adoption gain each account's best play recorded, on the 0-1 scale.

        Returns parallel arrays of gain, play name and case count, so the
        counterfactual can be scored for the whole book in one pass.
        """
        n = len(drivers)
        gains = np.zeros(n)
        names: List[Optional[str]] = [None] * n
        cases: List[Optional[int]] = [None] * n

        if self.playbook is None:
            return gains, names, cases

        # Plays depend only on (driver, segment), and a book of 200 accounts has a
        # handful of distinct pairs. Caching turns 200 lookups into about 15.
        cache: Dict[tuple, Optional[object]] = {}
        for i, (driver, segment) in enumerate(zip(drivers, segments)):
            key = (driver, segment)
            if key not in cache:
                found = self.playbook.plays_for(driver, segment=segment, limit=1)
                cache[key] = found[0] if found else None
            play = cache[key]
            if play is None:
                continue
            gain = play.median_adoption_gain / ADOPTION_SCALE
            if gain < MIN_ADOPTION_GAIN:
                continue
            gains[i] = gain
            names[i] = play.action
            cases[i] = play.cases

        return gains, names, cases

    def _mitigated(self, frame: pd.DataFrame, gains: np.ndarray, weeks: int) -> np.ndarray:
        """Re-score the book with adoption raised by each account's recorded gain.

        Adoption is capped at 1.0 -- an account already at full adoption cannot
        gain from an adoption play, and letting the feature exceed its observed
        range would extrapolate the model outside where it was fitted.
        """
        counterfactual = frame.copy()
        for column in ADOPTION_FEATURES:
            if column in counterfactual.columns:
                counterfactual[column] = np.minimum(
                    counterfactual[column].to_numpy(dtype=float) + gains, 1.0
                )
        return self.survival.churn_probabilities(counterfactual, weeks=weeks)

    # ------------------------------------------------------------------- public

    def exposures(
        self,
        frame: pd.DataFrame,
        scored: List[Dict],
        horizon_weeks: int = 13,
    ) -> List[CustomerExposure]:
        """Every account's exposure, unaggregated and unsorted.

        Lift and band are defined against the book average, so the whole book is
        always scored together even when the caller wants one account.

        Args:
            frame: one feature row per customer, as served to the survival model
            scored: CustomerHealthScorer.score_active_customers() output, which
                carries ARR, segment and the dominant risk driver
            horizon_weeks: the horizon the probability is taken over
        """
        by_id = {c["customer_id"]: c for c in scored}
        present = frame[frame["customer_id"].isin(by_id)].reset_index(drop=True)
        if present.empty:
            raise ValueError("no customer in the feature frame was scored")

        dropped = len(frame) - len(present)
        if dropped:
            # Churned accounts are in the warehouse but not in the scorer, which
            # only scores active ones. Worth logging so a real mismatch is visible.
            logger.info(f"Exposure: {dropped} feature rows had no active scorer entry")

        meta = [by_id[cid] for cid in present["customer_id"]]
        arr = np.array([m["arr"] for m in meta], dtype=float)

        ranked = self.survival.rank_book(present, weeks=horizon_weeks)
        probability = ranked["probability"].to_numpy(dtype=float)
        expected = probability * arr

        gains, play_names, play_cases = self._adoption_gains(
            [m["risk_reason"] for m in meta], [m["segment"] for m in meta]
        )
        if gains.any():
            mitigated_p = self._mitigated(present, gains, horizon_weeks)
            # A play that the model reads as raising risk recovers nothing. Clipping
            # at zero rather than letting it net off keeps the total honest.
            recoverable = np.maximum(expected - mitigated_p * arr, 0.0)
        else:
            mitigated_p = np.full(len(present), np.nan)
            recoverable = np.zeros(len(present))

        return [
            CustomerExposure(
                customer_id=present["customer_id"].iloc[i],
                name=meta[i]["name"],
                segment=meta[i]["segment"],
                arr=arr[i],
                probability=probability[i],
                lift=float(ranked["lift"].iloc[i]),
                band=str(ranked["band"].iloc[i]),
                expected_loss=float(expected[i]),
                expected_loss_upper=float(expected[i] * CALIBRATION_UNDERSTATEMENT),
                mitigated_probability=(
                    float(mitigated_p[i]) if play_names[i] else None
                ),
                recoverable=float(recoverable[i]) if play_names[i] else None,
                play=play_names[i],
                play_cases=play_cases[i],
            )
            for i in range(len(present))
        ]

    def for_book(
        self,
        frame: pd.DataFrame,
        scored: List[Dict],
        horizon_weeks: int = 13,
        top_n: int = 10,
    ) -> BookExposure:
        """Exposure across the book, aggregated by band and by account.

        Args:
            top_n: how many accounts to return in descending exposure
        """
        exposures = self.exposures(frame, scored, horizon_weeks=horizon_weeks)

        total_loss = sum(e.expected_loss for e in exposures)
        bands = []
        for name in BAND_ORDER:
            members = [e for e in exposures if e.band == name]
            if not members:
                continue
            loss = sum(e.expected_loss for e in members)
            bands.append(BandExposure(
                band=name,
                accounts=len(members),
                arr=sum(e.arr for e in members),
                expected_loss=loss,
                share_of_loss=loss / total_loss if total_loss else 0.0,
            ))

        ordered = sorted(exposures, key=lambda e: e.expected_loss, reverse=True)
        top_loss = sum(e.expected_loss for e in ordered[:top_n])

        return BookExposure(
            horizon_weeks=horizon_weeks,
            accounts=len(exposures),
            total_arr=sum(e.arr for e in exposures),
            expected_loss=total_loss,
            expected_loss_upper=total_loss * CALIBRATION_UNDERSTATEMENT,
            recoverable=sum(e.recoverable or 0.0 for e in exposures),
            by_band=bands,
            top_accounts=ordered[:top_n],
            concentration_top_10=top_loss / total_loss if total_loss else 0.0,
        )

    def for_customer(
        self,
        frame: pd.DataFrame,
        scored: List[Dict],
        customer_id: str,
        horizon_weeks: int = 13,
    ) -> Optional[CustomerExposure]:
        """One account's exposure.

        Computed from the book rather than in isolation, because lift and band are
        both defined against the book average and would otherwise be undefined.
        """
        for exposure in self.exposures(frame, scored, horizon_weeks=horizon_weeks):
            if exposure.customer_id == customer_id:
                return exposure
        return None
