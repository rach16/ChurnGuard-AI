"""
The account story, the queue and the book's causes (ADR-0015, 8.5).

One story per account, composed here so the pane and the page show the same
thing: a headline sentence, the signals with their evidence and date, what to
do, and the weight table the score is built from. Thresholds are the scorer's
own, imported, so a story never disagrees with the score it explains. Nothing
here is a model: a date subtraction, a few thresholds, and a vocabulary over
the CSM notes (``core.signals``). The score itself is untouched.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.health_scoring import (
    RISK_REASONS, RISK_WEIGHTS, TICKET_WINDOW_DAYS, TREND_WINDOW_WEEKS,
    CustomerHealthScorer,
)
from core.signals import classify

# Thresholds not already the scorer's. Named here so the page can quote them.
CONTACT_STALE_DAYS = 60       # no interaction for this long is a signal
NOTE_WINDOW_DAYS = 180        # a risk note older than this is history, not a signal
RENEWAL_SOON_DAYS = 90        # a renewal inside this window is called out
TICKETS_ADVERSE = 3           # tickets in the 30-day window that make support a signal
MIN_TREND_WEEKS = 3           # below this the slope is zero by construction
ENGAGEMENT_LOW, ENGAGEMENT_HIGH = 0.3, 0.6     # the scorer's _band thresholds
ADOPTION_LOW, ADOPTION_HIGH = 0.4, 0.7
CSAT_LOW = 3.0

ORDER = {"adverse": 0, "neutral": 1, "good": 2}


@dataclass(frozen=True)
class Signal:
    key: str                 # seats | adoption | trend | contact | tickets | csat | note | competitor | renewal
    kind: str                # adverse | neutral | good
    text: str                # the full line
    brief: str               # the headline fragment
    source: str              # usage | CSM note | activity | support | deals | contract
    date: Optional[str] = None


def money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M".replace(".00M", "M")
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


class StoryBuilder:
    def __init__(self, scorer: CustomerHealthScorer, playbook=None):
        self.scorer = scorer
        self.playbook = playbook
        self._queue: Optional[List[Dict[str, Any]]] = None
        self._signals: Dict[str, List[Signal]] = {}

    # ---------------------------------------------------------------- signals

    def signals_for(self, c: Dict[str, Any]) -> List[Signal]:
        """Every signal the record supports for one scored customer, adverse first."""
        cid = c["customer_id"]
        if cid in self._signals:
            return self._signals[cid]
        s = self.scorer
        out: List[Signal] = []
        row = s.customers[s.customers["customer_id"] == cid].iloc[0]

        # Renewal, from the contract.
        end = pd.to_datetime(row.get("contract_end_date"), errors="coerce")
        if pd.notna(end):
            days = int((end - s.as_of).days)
            soon = 0 <= days <= RENEWAL_SOON_DAYS and c["risk_level"] != "Low"
            out.append(Signal("renewal", "adverse" if soon else "neutral",
                              f"Renews in {days} days" if days >= 0 else f"Renewal date passed {-days} days ago",
                              f"renews in {days} days" if days >= 0 else "renewal date passed",
                              "contract", end.date().isoformat()))

        # Usage: seats active, adoption, trend.
        snaps = s.snapshots[s.snapshots["customer_id"] == cid].sort_values("week_start")
        if not snaps.empty:
            latest = snaps.iloc[-1]
            seats = row.get("seats")
            active = latest.get("active_users")
            engagement = float(latest["engagement_score"])
            kind = "adverse" if engagement < ENGAGEMENT_LOW else "neutral" if engagement < ENGAGEMENT_HIGH else "good"
            if pd.notna(seats) and pd.notna(active) and float(seats) > 0:
                text = f"{int(active)} of {int(seats)} seats active last week"
            else:
                text = f"Engagement score {engagement:.2f} last week"
            out.append(Signal("seats", kind, text, text[0].lower() + text[1:], "usage",
                              latest["week_start"].date().isoformat()))
            adoption = float(latest["feature_adoption_rate"])
            kind = "adverse" if adoption < ADOPTION_LOW else "neutral" if adoption < ADOPTION_HIGH else "good"
            text = f"Feature adoption {adoption:.0%}"
            out.append(Signal("adoption", kind, text, text[0].lower() + text[1:], "usage",
                              latest["week_start"].date().isoformat()))
            weeks = int(snaps["week_start"].nunique())
            if weeks >= MIN_TREND_WEEKS:
                window = snaps.tail(TREND_WINDOW_WEEKS)
                slope = float(np.polyfit(np.arange(len(window), dtype=float),
                                         window["engagement_score"].astype(float), 1)[0])
                if slope < -0.002:
                    out.append(Signal("trend", "adverse", f"Usage declining over {len(window)} weeks",
                                      "usage declining", "usage"))
                elif slope > 0.002:
                    out.append(Signal("trend", "good", f"Usage rising over {len(window)} weeks",
                                      "usage rising", "usage"))
            else:
                out.append(Signal("trend", "neutral", f"Only {_count(weeks, 'week')} of usage: no trend yet",
                                  "no usage trend yet", "usage"))

        # Contact.
        inter = s.interactions[s.interactions["customer_id"] == cid]
        if not inter.empty:
            last = inter["interaction_date"].max()
            days = int((s.as_of - last).days)
            text = f"Last contact {days} days ago"
            out.append(Signal("contact", "adverse" if days > CONTACT_STALE_DAYS else "good",
                              text, text[0].lower() + text[1:], "activity", last.date().isoformat()))
        else:
            out.append(Signal("contact", "adverse", "No contact on record", "no contact on record", "activity"))

        # Tickets and satisfaction.
        tickets = s.tickets[s.tickets["customer_id"] == cid]
        recent = tickets[tickets["created_date"] >= s.as_of - timedelta(days=TICKET_WINDOW_DAYS)]
        high = int((recent["severity"].astype(str).str.lower() == "high").sum()) if not recent.empty else 0
        n = int(len(recent))
        if n == 0:
            out.append(Signal("tickets", "good", f"No tickets in the last {TICKET_WINDOW_DAYS} days",
                              "no open tickets", "support"))
        else:
            kind = "adverse" if n >= TICKETS_ADVERSE or high else "neutral"
            text = f"{_count(n, 'ticket')} in the last {TICKET_WINDOW_DAYS} days" + (f", {high} high severity" if high else "")
            out.append(Signal("tickets", kind, text, text[0].lower() + text[1:], "support",
                              recent["created_date"].max().date().isoformat()))
        rated = tickets["csat_score"].dropna() if not tickets.empty else pd.Series(dtype=float)
        if len(rated):
            mean = float(rated.mean())
            text = f"Satisfaction {mean:.1f} of 5 across {_count(len(rated), 'rated ticket')}"
            out.append(Signal("csat", "adverse" if mean < CSAT_LOW else "good", text,
                              f"satisfaction {mean:.1f} of 5", "support"))

        # CSM notes, newest first; risk notes inside the window are signals,
        # the newest positive note is one good signal.
        competitor: Optional[str] = None
        seen_good = False
        for _, r in inter.sort_values("interaction_date", ascending=False).iterrows():
            sig = classify(str(r["content"]), r["interaction_date"].date().isoformat())
            if sig is None:
                continue
            if sig.polarity == "adverse":
                if (s.as_of - r["interaction_date"]).days > NOTE_WINDOW_DAYS:
                    continue
                if sig.competitor and competitor is None:
                    competitor = sig.competitor
                out.append(Signal("note", "adverse", sig.label, sig.label[0].lower() + sig.label[1:],
                                  "CSM note", sig.date))
            elif not seen_good:
                seen_good = True
                out.append(Signal("note", "good", sig.label, sig.label[0].lower() + sig.label[1:],
                                  "CSM note", sig.date))

        # Competitor: the account's own field, or a note.
        named = row.get("competitor")
        named = named if isinstance(named, str) and named.strip() else competitor
        if named:
            out.append(Signal("competitor", "adverse", f"Competitor named: {named}", f"{named} named", "deals"))
        else:
            out.append(Signal("competitor", "good", "No competitor recorded", "no competitor recorded", "deals"))

        out.sort(key=lambda x: ORDER[x.kind])
        self._signals[cid] = out
        return out

    # ------------------------------------------------------------------ story

    def headline(self, c: Dict[str, Any], signals: List[Signal]) -> str:
        renewal = next((x for x in signals if x.key == "renewal"), None)
        adverse = [x for x in signals if x.kind == "adverse" and x.key != "renewal"]
        good = [x for x in signals if x.kind == "good"]
        picked = adverse[:3] if len(adverse) >= 3 else adverse[:2] + good[:1]
        opening = f"{c['name']} " + (f"{renewal.text[0].lower() + renewal.text[1:]}." if renewal else "is an active customer.")
        return (f"{opening} {money(float(c['arr']))} at risk. {c['risk_level']} risk"
                + (": " + ", ".join(x.brief for x in picked) if picked else "") + ".")

    def story(self, customer_id: int) -> Optional[Dict[str, Any]]:
        c = self.scorer.get_customer(customer_id)
        if c is None:
            return None
        signals = self.signals_for(c)
        renewal = next((x for x in signals if x.key == "renewal"), None)
        at_risk = {x["customer_id"] for x in self.scorer.top_of_book(self.scorer.score_active_customers())}
        plays = (self.playbook.plays_for(c["risk_reason"], c["segment"]) if self.playbook is not None else [])
        factors = [{"factor": RISK_REASONS[k], "weight": RISK_WEIGHTS[k], "adverse_fraction": v,
                    "contribution": round(RISK_WEIGHTS[k] * v * 100, 1)}
                   for k, v in sorted(c["risk_factors"].items(), key=lambda kv: -RISK_WEIGHTS[kv[0]] * kv[1])]
        return {
            "id": c["id"], "customer_id": c["customer_id"], "name": c["name"],
            "segment": c["segment"], "industry": c["industry"], "arr": c["arr"],
            "tenure_years": c["tenure_years"], "crm_created_date": c.get("crm_created_date"),
            "headline": self.headline(c, signals),
            "risk": {"score": c["risk_score"], "band": c["risk_level"],
                     "at_risk": c["customer_id"] in at_risk, "top_reason": c["risk_reason"]},
            "renewal": ({"date": renewal.date, "days": int(renewal.text.split()[2]) if renewal.text.startswith("Renews") else None}
                        if renewal else None),
            "signals": [asdict(x) for x in signals],
            "plays": [p.to_dict() for p in plays],
            "how_built": {"factors": factors, "as_of": self.scorer.as_of.date().isoformat(),
                          "history_weeks": self.scorer.history_weeks(),
                          "at_risk_rule": "the riskiest 20% of the active book"},
        }

    # ------------------------------------------------------------------ queue

    def queue(self) -> List[Dict[str, Any]]:
        """One row per active account, riskiest first, with the renewal and the
        evidence phrase instead of the scorer's label."""
        if self._queue is not None:
            return self._queue
        scored = self.scorer.score_active_customers()
        at_risk = {x["customer_id"] for x in self.scorer.top_of_book(scored)}
        rows = []
        for c in scored:
            signals = self.signals_for(c)
            renewal = next((x for x in signals if x.key == "renewal"), None)
            lead = next((x for x in signals if x.kind == "adverse" and x.key != "renewal"), None) \
                or next((x for x in signals if x.kind == "good"), None)
            rows.append({
                "id": c["id"], "customer_id": c["customer_id"], "name": c["name"],
                "segment": c["segment"], "arr": c["arr"], "risk_score": c["risk_score"],
                "risk_level": c["risk_level"], "at_risk": c["customer_id"] in at_risk,
                "renewal_days": (int(renewal.text.split()[2]) if renewal and renewal.text.startswith("Renews") else None),
                "reason": lead.text if lead else None,
                "reason_kind": lead.kind if lead else None,
                "reason_date": lead.date if lead else None,
            })
        self._queue = rows
        return rows

    # ----------------------------------------------------------------- causes

    def causes(self) -> Dict[str, Any]:
        """Why customers leave: the churned accounts by cause, the active book by
        its first adverse signal, renewals due, and what has worked."""
        cust = self.scorer.customers
        churned = cust[cust["is_churned"] == 1]
        by_cause = []
        for cat, g in churned.groupby(churned["churn_category"].fillna("Not recorded")):
            by_cause.append({"cause": cat, "accounts": int(len(g)), "arr_lost": float(g["arr"].sum())})
        by_cause.sort(key=lambda x: (-x["accounts"], -x["arr_lost"]))
        comp = churned["competitor"].dropna().astype(str).str.strip()
        comp = comp[comp != ""]
        competitors = [{"name": k, "accounts": int(v)} for k, v in comp.value_counts().items()]

        queue = self.queue()
        by_signal: Dict[str, Dict[str, Any]] = {}
        for row in queue:
            sigs = self._signals[row["customer_id"]]
            lead = next((x for x in sigs if x.kind == "adverse" and x.key != "renewal"), None)
            label = _signal_group(lead) if lead else "No adverse signal"
            b = by_signal.setdefault(label, {"signal": label, "accounts": 0, "arr": 0.0})
            b["accounts"] += 1
            b["arr"] += float(row["arr"])
        now = sorted(by_signal.values(), key=lambda x: -x["arr"])
        due = [r for r in queue if r["renewal_days"] is not None and 0 <= r["renewal_days"] <= RENEWAL_SOON_DAYS]

        plays: List[Dict[str, Any]] = []
        if self.playbook is not None and getattr(self.playbook, "mode", "") == "outcomes":
            frame = self.playbook.interventions
            for action, g in frame.groupby("play"):
                decided = g[g["outcome"].isin(["renewed", "churned"])]
                plays.append({"action": action, "renewed": int((decided["outcome"] == "renewed").sum()),
                              "decided": int(len(decided)), "pending": int((g["outcome"] == "pending").sum())})
            plays.sort(key=lambda p: (-(p["renewed"] / p["decided"] if p["decided"] else 0), -p["decided"]))
        return {
            "as_of": self.scorer.as_of.date().isoformat(),
            "churned": {"accounts": int(len(churned)), "arr_lost": float(churned["arr"].sum()),
                        "by_cause": by_cause, "competitors": competitors},
            "active": {"accounts": len(queue), "arr": float(sum(r["arr"] for r in queue)),
                       "by_signal": now,
                       "renewing_soon": {"days": RENEWAL_SOON_DAYS, "accounts": len(due),
                                         "arr": float(sum(r["arr"] for r in due))}},
            "plays": plays,
        }


def _signal_group(x: Signal) -> str:
    return {
        "seats": "Low seat use", "adoption": "Low feature adoption", "trend": "Usage declining",
        "contact": "No recent contact", "tickets": "Support load", "csat": "Low satisfaction",
        "competitor": "Competitor named",
    }.get(x.key, x.text if x.key != "note" else _note_group(x.text))


def _note_group(label: str) -> str:
    low = label.lower()
    if "champion" in low or "sponsor" in low:
        return "Champion or sponsor left"
    if "evaluating" in low or "competitor" in low:
        return "Competitor named"
    if "pricing" in low or "discount" in low:
        return "Pricing pressure"
    if "not renewing" in low or "cancellation" in low:
        return "Said they may not renew"
    if "support" in low or "escalation" in low:
        return "Support load"
    return label
