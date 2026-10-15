"""
Recommended plays for an at-risk account.

The queue answers "who is at risk". This answers "what has worked on accounts
like this before", which is the question a customer success lead actually has.

Deliberately computed rather than generated. A play is a solution that was applied
to comparable accounts with a recorded outcome, so every recommendation carries the
number of cases behind it and the measured result. That means it costs nothing,
works with the LLM stack disabled, is identical on every request, and -- most
importantly -- can be argued with. An LLM asked to suggest a retention tactic would
produce fluent advice with no evidence attached, which is the failure this project
has spent its whole history removing.

Evidence lives in data/success_stories.csv: 60 accounts that faced a challenge, had
a solution applied, and recorded adoption, engagement and support movement after.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# The scorer names a risk driver; the case studies name a challenge category. The
# vocabularies are different and the mapping is a judgement, so it lives here in
# one obvious place rather than being inferred by string similarity.
#
# Ordered: the first category with enough evidence wins, and the rest are fallbacks.
DRIVER_TO_CHALLENGE: Dict[str, List[str]] = {
    "Low engagement": ["Adoption Challenges", "Product Fit"],
    "Declining usage": ["Adoption Challenges", "Competition"],
    "Feature gaps": ["Product Fit", "Adoption Challenges"],
    "Support issues": ["Support Issues"],
    "Poor onboarding": ["Adoption Challenges", "Support Issues"],
}

# Below this, "what worked" is an anecdote rather than a pattern. Surfacing a single
# case as a recommendation is how advice acquires false authority.
MIN_CASES = 3


@dataclass
class Play:
    """One recommended action, with the evidence behind it."""

    action: str
    challenge: str
    cases: int
    same_segment_cases: int
    median_adoption_gain: float          # percentage points
    median_support_reduction: float      # percent
    examples: List[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        """How much weight the evidence supports. Sample size, not model output."""
        if self.same_segment_cases >= 3:
            return "strong"
        if self.cases >= 6:
            return "moderate"
        return "limited"

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "challenge": self.challenge,
            "cases": self.cases,
            "same_segment_cases": self.same_segment_cases,
            "median_adoption_gain": round(self.median_adoption_gain, 1),
            "median_support_reduction": round(self.median_support_reduction, 1),
            "confidence": self.confidence,
            "examples": self.examples,
        }


@dataclass
class OutcomePlay:
    """A save play and what happened at the renewal after it, failures included.

    Built from recorded interventions (a CSM's save play, then the renewal deal),
    not from success write-ups, so a play that usually fails shows as one.
    """

    action: str
    challenge: str
    scope: str                 # "this challenge" or "any challenge"
    renewed: int
    decided: int               # renewed + churned; pending renewals are not counted
    pending: int
    examples: List[str] = field(default_factory=list)

    @property
    def save_rate(self) -> float:
        return self.renewed / self.decided if self.decided else 0.0

    @property
    def save_rate_floor(self) -> float:
        """Lower end of a 90% Wilson interval on the save rate.

        Ranking on the raw rate puts 3 of 3 above 9 of 11; ranking on case count put a
        play that renewed 1 of 6 at the top of "what has worked". The interval's floor
        rewards a high rate and penalises a thin record, in one number.
        """
        n, z = self.decided, 1.645
        if not n:
            return 0.0
        rate = self.renewed / n
        centre = rate + z * z / (2 * n)
        margin = z * ((rate * (1 - rate) + z * z / (4 * n)) / n) ** 0.5
        return max(0.0, (centre - margin) / (1 + z * z / n))

    @property
    def confidence(self) -> str:
        if self.decided >= 15:
            return "strong"
        if self.decided >= 8:
            return "moderate"
        return "limited"

    def to_dict(self) -> dict:
        return {
            "kind": "outcome",
            "action": self.action,
            "challenge": self.challenge,
            "scope": self.scope,
            "renewed": self.renewed,
            "decided": self.decided,
            "pending": self.pending,
            "save_rate": round(self.save_rate, 3),
            "save_rate_floor": round(self.save_rate_floor, 3),
            "cases": self.decided,
            "confidence": self.confidence,
            "examples": self.examples,
        }


class PlaybookEngine:
    """Matches an at-risk account to solutions that worked on comparable accounts."""

    def __init__(self, data_folder: str = "data"):
        path = Path(data_folder) / "success_stories.csv"
        self.stories = pd.read_csv(path)

        # A source that records save plays and the renewal after each (Salesforce,
        # via the connector's interventions table) is the better evidence: it counts
        # the plays that failed. When it exists, it is used instead of write-ups.
        interventions = Path(data_folder) / "interventions.csv"
        self.interventions = (pd.read_csv(interventions, keep_default_na=False)
                              if interventions.exists() else pd.DataFrame())
        decided = (self.interventions["outcome"].isin(["renewed", "churned"]).sum()
                   if not self.interventions.empty else 0)
        self.mode = "outcomes" if decided else "stories"
        logger.info(f"Playbook loaded: {len(self.stories)} resolved cases, "
                    f"{len(self.interventions)} recorded save plays ({self.mode})")

    def plays_for(
        self,
        risk_reason: str,
        segment: Optional[str] = None,
        limit: int = 3,
    ) -> List[Play]:
        """
        Rank solutions for this driver by evidence.

        Args:
            risk_reason: The scorer's dominant driver, e.g. "Low engagement"
            segment: Prefer cases from the same segment when there are enough
            limit: Maximum plays to return
        """
        categories = DRIVER_TO_CHALLENGE.get(risk_reason, [])
        if not categories:
            return []
        if self.mode == "outcomes":
            return self._outcome_plays(categories, limit)

        matched = self.stories[self.stories["challenge_category"].isin(categories)]
        if matched.empty:
            return []

        plays: List[Play] = []
        for (action, challenge), group in matched.groupby(["solution", "challenge_category"]):
            if len(group) < MIN_CASES:
                continue

            same_segment = group[group["segment"] == segment] if segment else group.iloc[0:0]

            # Prefer same-segment outcomes when the sample supports it. An Enterprise
            # account's realistic gain is not an SMB's, and reporting the pooled
            # median as though it were would overstate the case.
            outcome = same_segment if len(same_segment) >= 3 else group

            plays.append(
                Play(
                    action=action,
                    challenge=challenge,
                    cases=len(group),
                    same_segment_cases=len(same_segment),
                    median_adoption_gain=float(
                        (outcome["adoption_after"] - outcome["adoption_before"]).median()
                    ),
                    median_support_reduction=float(outcome["support_reduction"].median()),
                    examples=group["company_name"].head(3).tolist(),
                )
            )

        # Rank by evidence first, effect second. A large measured gain from two cases
        # is weaker guidance than a modest one from twelve.
        plays.sort(
            key=lambda p: (p.same_segment_cases, p.cases, p.median_adoption_gain),
            reverse=True,
        )
        return plays[:limit]

    def _outcome_plays(self, categories: List[str], limit: int) -> List["OutcomePlay"]:
        """Plays ranked by decided renewals, on this challenge where the sample allows.

        Falls back to the play's record on any challenge, and says so, when fewer
        than MIN_CASES renewals have been decided on this one. Never reports a play
        on fewer than MIN_CASES decided renewals at all.
        """
        frame = self.interventions
        plays: List[OutcomePlay] = []
        for action, group in frame.groupby("play"):
            on_challenge = group[group["challenge_category"].isin(categories)]
            scope, rows = ("this challenge", on_challenge)
            decided = rows[rows["outcome"].isin(["renewed", "churned"])]
            if len(decided) < MIN_CASES:
                scope, rows = ("any challenge", group)
                decided = rows[rows["outcome"].isin(["renewed", "churned"])]
            if len(decided) < MIN_CASES:
                continue
            plays.append(OutcomePlay(
                action=action,
                challenge=", ".join(categories) if scope == "this challenge" else "any",
                scope=scope,
                renewed=int((decided["outcome"] == "renewed").sum()),
                decided=len(decided),
                pending=int((rows["outcome"] == "pending").sum()),
                examples=decided["company_name"].head(3).tolist(),
            ))
        # Best supported save rate first; see save_rate_floor.
        plays.sort(key=lambda p: (p.save_rate_floor, p.decided), reverse=True)
        return plays[:limit]

    def coverage(self) -> Dict[str, int]:
        """How many resolved cases back each driver. Exposed so the gaps are visible."""
        return {
            driver: int(
                self.stories["challenge_category"].isin(categories).sum()
            )
            for driver, categories in DRIVER_TO_CHALLENGE.items()
        }
