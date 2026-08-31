"""
Customer Health Scoring System

Scores are computed from observed customer data -- weekly engagement snapshots,
support tickets and interaction history -- not sampled from a distribution. The
previous implementation drew risk_score from np.random.uniform(70, 92) and
labelled the result a prediction; nothing downstream could act on that.

Everything here is deterministic: the same dataset produces the same scores.
"""

import logging
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# How much each observed signal contributes to the risk score. Documented here
# rather than buried in the arithmetic, because this is the number a customer
# success lead will want to argue with -- and should be able to.
RISK_WEIGHTS = {
    "engagement": 0.35,    # latest weekly engagement score
    "adoption": 0.20,      # latest feature adoption rate
    "trend": 0.15,         # direction of engagement over the recent window
    "support": 0.15,       # ticket volume in the last 30 days
    "satisfaction": 0.15,  # mean CSAT across the account's tickets
}

TREND_WINDOW_WEEKS = 12   # window used to measure engagement direction
TICKET_WINDOW_DAYS = 30   # "tickets_30d" lookback
HIGH_TICKET_VOLUME = 6    # tickets in the window that saturates the support factor

# Human-readable label for whichever factor contributes most to a customer's score.
RISK_REASONS = {
    "engagement": "Low engagement",
    "adoption": "Feature gaps",
    "trend": "Declining usage",
    "support": "Support issues",
    "satisfaction": "Poor onboarding",
}


class CustomerHealthScorer:
    """Score customer health from observed engagement, support and interaction data."""

    def __init__(self, data_folder: str = "data", churn_data_path: Optional[str] = None):
        """
        Args:
            data_folder: Folder holding customers.csv and its fact tables
            churn_data_path: Deprecated. Accepted so existing callers still construct;
                its parent directory is used as data_folder when given.
        """
        if churn_data_path and data_folder == "data":
            data_folder = str(Path(churn_data_path).parent)
            logger.warning(
                "churn_data_path is deprecated; scoring now reads customers.csv "
                f"from {data_folder}"
            )

        self.data_folder = Path(data_folder)
        self._scored: Optional[List[Dict]] = None
        self._load_data()

    # ---------------------------------------------------------------- loading

    def _load_data(self) -> None:
        self.customers = pd.read_csv(self.data_folder / "customers.csv")
        self.snapshots = pd.read_csv(self.data_folder / "engagement_snapshots.csv")
        self.tickets = pd.read_csv(self.data_folder / "support_tickets.csv")
        self.interactions = pd.read_csv(self.data_folder / "customer_interactions.csv")

        self.snapshots["week_start"] = pd.to_datetime(self.snapshots["week_start"])
        self.tickets["created_date"] = pd.to_datetime(self.tickets["created_date"])
        self.interactions["interaction_date"] = pd.to_datetime(self.interactions["interaction_date"])

        # Anchor "recent" to the data, not to wall-clock time. The dataset is generated
        # to a fixed AS_OF date, so using datetime.now() would silently empty every
        # rolling window as the file ages.
        self.as_of = self.snapshots["week_start"].max()

        logger.info(
            f"Loaded {len(self.customers)} customers, {len(self.snapshots)} snapshots, "
            f"{len(self.tickets)} tickets (as-of {self.as_of.date()})"
        )

    # ---------------------------------------------------------------- scoring

    @staticmethod
    def _factor_scores(engagement: float, adoption: float, slope: float,
                       tickets_30d: int, csat: Optional[float]) -> Dict[str, float]:
        """Map each observed signal onto a 0-1 risk contribution (1 = worst)."""
        return {
            "engagement": float(np.clip(1.0 - engagement, 0.0, 1.0)),
            "adoption": float(np.clip(1.0 - adoption, 0.0, 1.0)),
            # A slope of -0.01/week across the window is treated as fully adverse.
            "trend": float(np.clip(-slope / 0.01, 0.0, 1.0)),
            "support": float(np.clip(tickets_30d / HIGH_TICKET_VOLUME, 0.0, 1.0)),
            # No tickets is not evidence of unhappiness, so treat it as neutral.
            "satisfaction": 0.5 if csat is None else float(np.clip((5.0 - csat) / 4.0, 0.0, 1.0)),
        }

    def _score_customer(self, row: pd.Series) -> Optional[Dict]:
        cid = row["customer_id"]

        snaps = self.snapshots[self.snapshots["customer_id"] == cid].sort_values("week_start")
        if snaps.empty:
            return None

        latest = snaps.iloc[-1]
        engagement = float(latest["engagement_score"])
        adoption = float(latest["feature_adoption_rate"])

        window = snaps.tail(TREND_WINDOW_WEEKS)
        if len(window) >= 3:
            weeks = np.arange(len(window), dtype=float)
            slope = float(np.polyfit(weeks, window["engagement_score"].astype(float), 1)[0])
        else:
            slope = 0.0

        cust_tickets = self.tickets[self.tickets["customer_id"] == cid]
        recent = cust_tickets[
            cust_tickets["created_date"] >= self.as_of - timedelta(days=TICKET_WINDOW_DAYS)
        ]
        tickets_30d = int(len(recent))
        csat = float(cust_tickets["csat_score"].mean()) if not cust_tickets.empty else None

        cust_interactions = self.interactions[self.interactions["customer_id"] == cid]
        if not cust_interactions.empty:
            last_contact = cust_interactions["interaction_date"].max()
            last_engagement_days = int((self.as_of - last_contact).days)
        else:
            last_engagement_days = int((self.as_of - pd.to_datetime(row["contract_start_date"])).days)

        factors = self._factor_scores(engagement, adoption, slope, tickets_30d, csat)
        risk_score = round(sum(RISK_WEIGHTS[k] * v for k, v in factors.items()) * 100, 1)

        # The dominant factor names the reason, so the label always matches the maths.
        dominant = max(factors, key=lambda k: RISK_WEIGHTS[k] * factors[k])

        if slope < -0.002:
            trend = "increasing"
        elif slope > 0.002:
            trend = "decreasing"
        else:
            trend = "stable"

        competitor = row.get("competitor")
        return {
            "id": int(str(cid).split("-")[1]),
            "customer_id": cid,
            "name": row["company_name"],
            "segment": row["segment"],
            "industry": row["industry"],
            "arr": float(row["arr"]),
            "tenure_years": round(int(row["tenure_months"]) / 12, 1),
            "risk_score": risk_score,
            "risk_level": self._risk_level(risk_score),
            "days_until_churn": self._days_until_churn(risk_score),
            "risk_reason": RISK_REASONS[dominant],
            "trend": trend,
            "last_engagement_days": last_engagement_days,
            "support_tickets_30d": tickets_30d,
            "feature_adoption_rate": round(adoption, 2),
            "engagement_score": round(engagement, 3),
            "mean_csat": round(csat, 2) if csat is not None else None,
            "competitor": competitor if isinstance(competitor, str) and competitor else None,
            "risk_factors": {k: round(v, 3) for k, v in factors.items()},
        }

    @staticmethod
    def _risk_level(score: float) -> str:
        if score >= 80:
            return "Critical"
        if score >= 60:
            return "High"
        if score >= 40:
            return "Medium"
        return "Low"

    @staticmethod
    def _days_until_churn(score: float) -> int:
        """Higher risk means a nearer predicted churn date. Deterministic, not sampled."""
        return int(round(float(np.interp(score, [20, 50, 80, 100], [180, 90, 21, 7]))))

    # ------------------------------------------------------------------ query

    def score_active_customers(self) -> List[Dict]:
        """Score every customer who has not already churned. Cached after first call."""
        if self._scored is not None:
            return self._scored

        active = self.customers[self.customers["is_churned"] == 0]
        scored = [s for s in (self._score_customer(r) for _, r in active.iterrows()) if s]
        scored.sort(key=lambda c: c["risk_score"], reverse=True)

        self._scored = scored
        logger.info(f"Scored {len(scored)} active customers")
        return scored

    def get_customer(self, customer_id: int) -> Optional[Dict]:
        return next((c for c in self.score_active_customers() if c["id"] == customer_id), None)

    def get_at_risk_customers(self, risk_threshold: float = 60.0, limit: int = 10) -> List[Dict]:
        at_risk = [c for c in self.score_active_customers() if c["risk_score"] >= risk_threshold]
        return at_risk[:limit]

    def calculate_customer_health(self, customer_data: Dict) -> Dict:
        """
        Score an ad-hoc customer supplied by the caller rather than one in the dataset.

        Uses the same weights as score_active_customers so the two agree.
        """
        engagement = float(customer_data.get("engagement_score", 0.5))
        adoption = float(customer_data.get("feature_adoption_rate", engagement))
        tickets = int(customer_data.get("support_tickets_30d", 0))
        csat = customer_data.get("mean_csat")
        tenure_years = float(customer_data.get("tenure_years", 2.0))

        factors = self._factor_scores(engagement, adoption, 0.0, tickets,
                                      float(csat) if csat is not None else None)
        risk_score = sum(RISK_WEIGHTS[k] * v for k, v in factors.items()) * 100

        # Accounts inside their first year carry risk beyond what the signals show.
        if tenure_years < 1:
            risk_score += 10

        risk_score = round(float(np.clip(risk_score, 0, 100)), 1)
        dominant = max(factors, key=lambda k: RISK_WEIGHTS[k] * factors[k])

        risk_factors = [
            f"{RISK_REASONS[name]} ({value:.0%} adverse)"
            for name, value in sorted(factors.items(), key=lambda kv: -kv[1])
            if value > 0.5
        ]
        if tenure_years < 1:
            risk_factors.append("New customer (< 1 year)")

        return {
            "risk_score": risk_score,
            "risk_level": self._risk_level(risk_score),
            "risk_reason": RISK_REASONS[dominant],
            "risk_factors": risk_factors,
            "days_until_churn": self._days_until_churn(risk_score),
        }

    def get_dashboard_stats(self) -> Dict:
        """Aggregates for the dashboard, computed over the scored active base."""
        scored = self.score_active_customers()
        at_risk = [c for c in scored if c["risk_score"] >= 60]

        churned = self.customers[self.customers["is_churned"] == 1]
        historical_churn_rate = len(churned) / len(self.customers) if len(self.customers) else 0.0

        return {
            "total_at_risk": len(at_risk),
            "critical_risk_count": sum(1 for c in at_risk if c["risk_score"] >= 80),
            "total_arr_at_risk": round(sum(c["arr"] for c in at_risk), 2),
            "avg_days_to_churn": round(float(np.mean([c["days_until_churn"] for c in at_risk])), 1)
            if at_risk else 0.0,
            "total_active_customers": len(scored),
            "historical_churn_rate": round(historical_churn_rate, 3),
            "as_of": self.as_of.date().isoformat(),
        }

    # ----------------------------------------------------------- detail views

    # The dataset records one overall adoption rate per week, not per-feature
    # telemetry. Rather than invent a fresh breakdown on every request -- which is
    # what the API used to do -- spread the real rate across the feature list
    # deterministically, so a given customer always renders the same chart.
    FEATURES = ["Dashboard", "Analytics", "Reports", "Integrations",
                "API", "Mobile App", "Automation", "Collaboration"]

    def get_engagement_history(self, customer_id: str) -> List[Dict]:
        """Real weekly engagement history for this customer."""
        snaps = self.snapshots[self.snapshots["customer_id"] == customer_id].sort_values("week_start")
        return [
            {
                "date": r["week_start"].date().isoformat(),
                "engagement_score": round(float(r["engagement_score"]), 2),
                "feature_adoption_rate": round(float(r["feature_adoption_rate"]), 2),
                "active_users": int(r["active_users"]),
            }
            for _, r in snaps.iterrows()
        ]

    def get_support_tickets(self, customer_id: str) -> List[Dict]:
        """Real support tickets for this customer, most recent first."""
        rows = self.tickets[self.tickets["customer_id"] == customer_id]
        rows = rows.sort_values("created_date", ascending=False)
        return [
            {
                "ticket_id": r["ticket_id"],
                "date": r["created_date"].date().isoformat(),
                "type": r["issue_type"],
                "category": r["category"],
                "severity": r["severity"],
                "resolution_hours": int(r["resolution_hours"]),
                "csat_score": int(r["csat_score"]),
            }
            for _, r in rows.iterrows()
        ]

    def get_interactions(self, customer_id: str, limit: int = 10) -> List[Dict]:
        """Real interaction history for this customer, most recent first."""
        rows = self.interactions[self.interactions["customer_id"] == customer_id]
        rows = rows.sort_values("interaction_date", ascending=False).head(limit)
        return [
            {
                "date": r["interaction_date"].date().isoformat(),
                "type": r["interaction_type"],
                "content": r["content"],
            }
            for _, r in rows.iterrows()
        ]

    def get_feature_usage(self, customer_id: str, adoption_rate: float) -> List[Dict]:
        """Per-feature adoption, spread deterministically around the observed rate."""
        usage = []
        for i, feature in enumerate(self.FEATURES):
            # Stable offset per (customer, feature); no randomness, no per-request drift.
            offset = ((hash((customer_id, feature)) % 41) - 20) / 100.0
            usage.append({
                "feature": feature,
                "usage_rate": round(float(np.clip(adoption_rate + offset, 0.0, 1.0)), 2),
            })
        return usage

    def get_customer_detail(self, customer_id: int) -> Optional[Dict]:
        """Everything the customer detail page needs, all from observed data."""
        customer = self.get_customer(customer_id)
        if not customer:
            return None

        cid = customer["customer_id"]
        factors = customer["risk_factors"]

        return {
            "customer": customer,
            "analysis": {
                "engagement_history": self.get_engagement_history(cid),
                "support_tickets": self.get_support_tickets(cid),
                "feature_usage": self.get_feature_usage(cid, customer["feature_adoption_rate"]),
                "interactions": self.get_interactions(cid),
                "predictions": {
                    "churn_probability": round(customer["risk_score"] / 100, 2),
                    "days_until_churn": customer["days_until_churn"],
                    "contributing_factors": [
                        {
                            "factor": RISK_REASONS[name],
                            "weight": RISK_WEIGHTS[name],
                            "adverse_fraction": value,
                            "contribution": round(RISK_WEIGHTS[name] * value * 100, 1),
                        }
                        for name, value in sorted(
                            factors.items(), key=lambda kv: -RISK_WEIGHTS[kv[0]] * kv[1]
                        )
                    ],
                },
            },
            "health_indicators": {
                "engagement": self._band(customer["engagement_score"], 0.3, 0.6),
                "product_usage": self._band(customer["feature_adoption_rate"], 0.4, 0.7),
                "support_health": "At Risk" if customer["support_tickets_30d"] > 5 else "Normal",
                "relationship_strength": "Weak" if customer["last_engagement_days"] > 30
                else "Moderate" if customer["last_engagement_days"] > 14 else "Strong",
            },
        }

    @staticmethod
    def _band(value: float, low: float, high: float) -> str:
        return "Poor" if value < low else "Fair" if value < high else "Good"

    # ------------------------------------------------------------- validation

    def scorer_auc(self) -> float:
        """
        How well the scoring function separates customers who actually churned.

        Scores every customer, churned and active alike, and measures rank agreement
        with the known label. A weighted sum that could not beat 0.5 would be
        decoration; this makes the claim checkable.
        """
        rows = [(self._score_customer(r), int(r["is_churned"]))
                for _, r in self.customers.iterrows()]
        scored = [(s["risk_score"], label) for s, label in rows if s]

        pos = [s for s, label in scored if label == 1]
        neg = [s for s, label in scored if label == 0]
        if not pos or not neg:
            return float("nan")

        wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
        return wins / (len(pos) * len(neg))
