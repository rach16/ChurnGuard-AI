"""
Discrete-time survival model for customer churn.

Predicts *when* a customer will churn, as a date with an interval, rather than
whether they will. The distinction matters because 64.5% of customers are
right-censored -- still active as of the last observation -- and a classifier must
label them "will not churn", asserting something the data does not say. See
ADR-0008 and docs/ARCHITECTURE.md.

The method:

  1. Fit a binary classifier to `event_in_next_period` over customer-weeks. Its
     output is the *hazard*: P(churn in the next period | survived until now).
  2. Project a customer forward period by period, advancing tenure, to get a
     hazard for each future period.
  3. Chain them into a survival curve: S(t) = prod(1 - h(k)) for k = 1..t.
  4. Read dates off the curve. S(t) = 0.5 is the median survival time -- the
     point estimate. S(t) = 0.75 and S(t) = 0.25 bound the interval.

The classifier stays ordinary and inspectable; the survival framing is what
handles censoring correctly.

Calibration is not optional here. A ranking model can order customers correctly
while getting every date wrong, and a date is what this predicts, so probabilities
must mean what they say. That rules out resampling to fix class imbalance -- it
improves apparent discrimination and destroys the probabilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# One period is four weeks; `event_in_next_period` in train_survival is defined
# over the same window. Changing this requires changing the label.
PERIOD_WEEKS = 4

# Features the model is fitted on.
#
# engagement_slope_4w is excluded: it scored AUC 0.503 against the hazard label in
# 7.1, which is chance. A four-week slope over a noisy weekly series is mostly
# noise; the twelve-week version survives.
FEATURES: tuple[str, ...] = (
    "engagement_score",
    "engagement_mean_4w",
    "engagement_mean_12w",
    "engagement_slope_12w",
    "engagement_vs_own_baseline",
    "engagement_range_12w",
    "feature_adoption_rate",
    "adoption_mean_12w",
    "active_users",
    "sessions",
    "tickets_30d",
    "tickets_90d",
    "csat_mean_90d",
    "severe_tickets_to_date",
    "days_since_last_interaction",
    # tenure_weeks varies within a customer, so it is a genuine signal rather
    # than an identity. arr and seats do not: they are constant per customer and
    # near-unique (193 and 96 distinct values across 200 customers). With ~100
    # rows per customer the model memorises "the account worth $92,400 churns"
    # and scores 0.9997 in-sample against 0.666 held out. Segment carries the
    # size tier without the fingerprint.
    "tenure_weeks",
)

CATEGORICAL: tuple[str, ...] = ("segment", "industry")

LABEL = "event_in_next_period"

# days_since_last_interaction uses 9999 to mean "no interaction on record". Left
# as a number it implies 27 years of silence and distorts every split that touches
# it -- flagged in 7.1 as AUC 0.569 despite a large mean gap. Convert to NaN and
# carry the fact separately, so the model can use "unknown" as information.
NO_CONTACT_SENTINEL = 9999

# Isotonic regression maps an entire input region to exactly zero when no event
# was observed in it. With roughly 280 events across the training window that is
# thin evidence, not proof of impossibility, and a hazard of exactly 0 makes the
# survival curve flat forever and any ratio against it undefined. Floor it.
MIN_HAZARD = 1e-4


@dataclass
class SurvivalPrediction:
    """A predicted churn date with the interval around it."""

    customer_id: str
    as_of: date
    hazard: float                       # probability of churn in the next period
    predicted_date: Optional[date]      # S(t) = 0.5
    earliest_date: Optional[date]       # S(t) = 0.75
    latest_date: Optional[date]         # S(t) = 0.25
    survival_curve: list[float] = field(repr=False, default_factory=list)

    @property
    def horizon(self) -> str:
        """Quarter bucket, for display only. The model predicts a date."""
        if self.predicted_date is None:
            return "beyond horizon"
        weeks = (self.predicted_date - self.as_of).days / 7
        if weeks <= 13:
            return "this quarter"
        if weeks <= 26:
            return "next quarter"
        return "future quarter"


class ChurnSurvivalModel:
    """Discrete-time survival model over customer-week observations."""

    def __init__(self, max_periods: int = 26, random_state: int = 42):
        """
        Args:
            max_periods: How far forward to project. 26 four-week periods is two
                years; beyond that the curve is extrapolation, not prediction.
            random_state: Fixed so training is reproducible.
        """
        self.max_periods = max_periods
        self.random_state = random_state
        self.pipeline = None
        self.calibrator = None
        self.feature_names_: list[str] = []

    # ------------------------------------------------------------------ setup

    @staticmethod
    def prepare(df: pd.DataFrame) -> pd.DataFrame:
        """Clean the raw training frame. Applied identically at train and predict."""
        out = df.copy()

        if "days_since_last_interaction" in out:
            never = out["days_since_last_interaction"] >= NO_CONTACT_SENTINEL
            out["never_contacted"] = never.astype(int)
            out.loc[never, "days_since_last_interaction"] = np.nan

        return out

    def _build_pipeline(self):
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder

        # HistGradientBoosting handles NaN natively, which matters because
        # csat_mean_90d is genuinely absent for customers who never raised a
        # ticket. Imputing it would invent a satisfaction score.
        return Pipeline([
            ("encode", ColumnTransformer(
                [("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), list(CATEGORICAL))],
                remainder="passthrough",
                verbose_feature_names_out=False,
            )),
            ("clf", HistGradientBoostingClassifier(
                # Deliberately small. There are only ~280 events across 15,711
                # rows, and rows are not independent -- one customer contributes
                # ~100 near-identical observations -- so capacity buys
                # memorisation rather than generalisation.
                max_iter=120,
                learning_rate=0.05,
                max_leaf_nodes=8,
                min_samples_leaf=120,
                l2_regularization=1.0,
                early_stopping=True,
                validation_fraction=0.15,
                random_state=self.random_state,
                # No class_weight. Reweighting improves apparent discrimination
                # and destroys calibration, and calibrated hazards are the whole
                # output here.
            )),
        ])

    # --------------------------------------------------------------- training

    def fit(self, train: pd.DataFrame, calibrate: bool = False) -> "ChurnSurvivalModel":
        """Fit the hazard model on customer-week rows.

        Args:
            train: customer-week observations with the hazard label
            calibrate: fit an isotonic calibrator on a held-out tail of the training
                window. The raw model ranks well and underpredicts -- 1.7% against an
                observed 4.65% in 7.2 -- and an underpredicted hazard makes the
                survival curve decay too slowly, which is what puts the dates months
                late. Isotonic rather than Platt because the miscalibration is not a
                monotone squeeze of a sigmoid; it is a level shift that varies across
                the range.

                The calibration slice is split by time, not at random, for the same
                reason the backtest is: a customer either side of a random split
                leaks its own future.
        """
        data = self.prepare(train)
        columns = list(CATEGORICAL) + [c for c in FEATURES if c in data.columns]
        if "never_contacted" in data:
            columns.append("never_contacted")

        if calibrate and "week_start" in data.columns:
            cutoff = data["week_start"].quantile(0.75)
            fit_part = data[data["week_start"] <= cutoff]
            cal_part = data[data["week_start"] > cutoff]
            # A calibrator needs both classes present or it cannot learn a mapping.
            if len(cal_part) < 200 or cal_part[LABEL].nunique() < 2:
                calibrate = False
        else:
            calibrate = False
            fit_part, cal_part = data, None

        X, y = (fit_part if calibrate else data)[columns], (fit_part if calibrate else data)[LABEL]
        self.feature_names_ = columns

        self.pipeline = self._build_pipeline()
        self.pipeline.fit(X, y)

        self.calibrator = None
        if calibrate:
            from sklearn.isotonic import IsotonicRegression

            raw = self.pipeline.predict_proba(cal_part[columns])[:, 1]
            self.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            self.calibrator.fit(raw, cal_part[LABEL].to_numpy())
            logger.info(
                f"Calibrated on {len(cal_part):,} held-out rows "
                f"(raw mean {raw.mean():.4f} -> observed {cal_part[LABEL].mean():.4f})"
            )

        logger.info(
            f"Fitted on {len(X):,} customer-weeks, {int(y.sum())} events "
            f"({y.mean():.2%} base rate), {len(columns)} features"
        )
        return self

    # -------------------------------------------------------------- inference

    def hazard(self, rows: pd.DataFrame) -> np.ndarray:
        """P(churn within the next period) for each row."""
        if self.pipeline is None:
            raise RuntimeError("Model is not fitted")
        raw = self.pipeline.predict_proba(self.prepare(rows)[self.feature_names_])[:, 1]
        if getattr(self, "calibrator", None) is not None:
            return np.clip(self.calibrator.predict(raw), MIN_HAZARD, 1.0)
        return raw

    def survival_curve(self, row: pd.Series) -> np.ndarray:
        """Project one customer forward, returning S(t) for t = 1..max_periods.

        Features are held constant except tenure, which advances each period. The
        reading is "if nothing about this account changes", which is the right
        basis for an intervention decision -- the point is to show what happens
        absent action.

        Extrapolating engagement along its observed twelve-week trend was tried
        and made predictions worse: median absolute error on held-out churners
        went from 196 to 238 days. The slope is around -0.003 per week, so over a
        four-week period the drift is smaller than the noise it introduces. The
        date error comes from hazard calibration, not from frozen features.
        """
        frame = pd.DataFrame([row] * self.max_periods).reset_index(drop=True)
        if "tenure_weeks" in frame:
            frame["tenure_weeks"] = frame["tenure_weeks"] + np.arange(self.max_periods) * PERIOD_WEEKS

        hazards = self.hazard(frame)
        return np.cumprod(1.0 - hazards)

    def churn_probability(self, row: pd.Series, weeks: int = 13) -> float:
        """P(churn within `weeks`), from the chained hazards.

        This is what the model actually supports. The median of the survival curve
        is structurally far out whenever the hazard is small -- at 0.04 per period
        it does not cross 0.5 for 482 days -- so a predicted date is unusable here
        however well the model ranks. A probability over a fixed window is the same
        information without the statistic that breaks. See ADR-0009.
        """
        periods = max(1, int(np.ceil(weeks / PERIOD_WEEKS)))
        curve = self.survival_curve(row)
        return float(1.0 - curve[min(periods, len(curve)) - 1])

    def churn_probabilities(self, rows: pd.DataFrame, weeks: int = 13) -> np.ndarray:
        """Vectorised churn_probability.

        Holds features constant while advancing tenure, so this compounds one
        hazard rather than re-predicting per period. Equivalent for a flat
        projection and far cheaper across a whole book.
        """
        periods = max(1, int(np.ceil(weeks / PERIOD_WEEKS)))
        h = self.hazard(rows)
        return 1.0 - (1.0 - h) ** periods

    @staticmethod
    def band(lift: float) -> str:
        """Likelihood band from a lift against the book average.

        The calibrated probability still underpredicts the absolute level by
        roughly two, because the hazard rate rises across the observation window
        and a model fitted on an earlier period cannot know that. A lift is
        invariant to a multiplicative level error -- both sides shift together --
        so a band derived from it survives the bias that a printed percentage
        would not.
        """
        if lift >= 3.0:
            return "Very high"
        if lift >= 1.75:
            return "High"
        if lift >= 1.0:
            return "Moderate"
        return "Low"

    def rank_book(self, rows: pd.DataFrame, weeks: int = 13) -> pd.DataFrame:
        """Probability, lift against the book average, and band, for every row."""
        p = self.churn_probabilities(rows, weeks=weeks)
        reference = float(np.mean(p)) or MIN_HAZARD
        lift = p / reference
        return pd.DataFrame({
            "probability": p,
            "lift": lift,
            "band": [self.band(x) for x in lift],
        }, index=rows.index)

    @staticmethod
    def _crossing(curve: np.ndarray, threshold: float) -> Optional[int]:
        """First period where survival drops below `threshold`, 1-indexed."""
        hit = np.argmax(curve < threshold)
        if curve[hit] >= threshold:      # argmax returns 0 when nothing matches
            return None
        return int(hit) + 1

    def predict(self, row: pd.Series, as_of: date) -> SurvivalPrediction:
        """Predicted churn date and interval for one customer."""
        curve = self.survival_curve(row)

        def to_date(periods: Optional[int]) -> Optional[date]:
            if periods is None:
                return None
            return as_of + timedelta(weeks=periods * PERIOD_WEEKS)

        return SurvivalPrediction(
            customer_id=str(row.get("customer_id", "")),
            as_of=as_of,
            hazard=float(self.hazard(pd.DataFrame([row]))[0]),
            predicted_date=to_date(self._crossing(curve, 0.50)),
            earliest_date=to_date(self._crossing(curve, 0.75)),
            latest_date=to_date(self._crossing(curve, 0.25)),
            survival_curve=[float(x) for x in curve],
        )

    def predict_many(self, rows: pd.DataFrame, as_of: date) -> list[SurvivalPrediction]:
        return [self.predict(r, as_of) for _, r in rows.iterrows()]

    # ------------------------------------------------------------- evaluation

    def score(self, test: pd.DataFrame) -> dict:
        """Discrimination and calibration on held-out rows.

        Both are reported because they fail independently: a model can rank
        customers correctly (good AUC) while its probabilities are systematically
        wrong (bad Brier), and a wrong probability is a wrong date.
        """
        from sklearn.metrics import brier_score_loss, roc_auc_score

        y = test[LABEL].to_numpy()
        p = self.hazard(test)

        if len(np.unique(y)) < 2:
            return {"n": len(y), "events": int(y.sum()), "auc": float("nan"),
                    "brier": float("nan"), "base_rate": float(y.mean())}

        return {
            "n": len(y),
            "events": int(y.sum()),
            "base_rate": float(y.mean()),
            "auc": float(roc_auc_score(y, p)),
            "brier": float(brier_score_loss(y, p)),
            "mean_predicted": float(p.mean()),
        }

    # ----------------------------------------------------------- persistence

    def save(self, path: Path) -> None:
        import joblib
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"pipeline": self.pipeline, "features": self.feature_names_,
                     "max_periods": self.max_periods, "calibrator": self.calibrator}, path)
        logger.info(f"Saved model to {path}")

    @classmethod
    def load(cls, path: Path) -> "ChurnSurvivalModel":
        import joblib
        blob = joblib.load(path)
        model = cls(max_periods=blob["max_periods"])
        model.pipeline = blob["pipeline"]
        model.feature_names_ = blob["features"]
        model.calibrator = blob.get("calibrator")
        return model


def load_training_data(duckdb_path: Path) -> pd.DataFrame:
    """Read the training table built by dbt."""
    import duckdb

    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        return con.execute("select * from main_gold.train_survival").df()
    finally:
        con.close()
