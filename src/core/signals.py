"""
CSM notes as dated signals (ADR-0015 section 3).

A note's subject is matched against a small vocabulary: what a CSM writes when
an account is in trouble ("Usage drop flagged", "Exec sponsor left company",
"Evaluating Vantage Analytics") and what they write when it is well ("QBR
completed", "Expansion discussion"). A match is a signal with a polarity, a
short label and the date; a note that matches nothing is not a signal and
stays in the timeline. Rules over text, no model: the 4.7 extraction can
replace this with the same output shape.

The vocabulary matches the subjects the owner's seed script writes into the
org, and the site says the org is seeded (ADR-0007).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Patterns are matched against the note's subject (the text before
# "Logged by" / "Challenge:"), lower-cased. First match wins.
_RISK: list[tuple[str, str]] = [
    (r"not renewing", "Said they are not renewing"),
    (r"cancellation", "Asked about cancellation terms"),
    (r"usage drop", "Usage drop flagged by the CSM"),
    (r"pricing pushback", "Pricing pushback at renewal"),
    (r"requested discount|discount at renewal", "Asked for a discount at renewal"),
    (r"complaint about support|support response", "Complained about support response time"),
    (r"escalation", "Escalation call"),
    (r"missed qbr", "Missed the QBR"),
    (r"exec sponsor left|sponsor left", "Executive sponsor left the company"),
    (r"champion changed roles|champion left", "Champion changed roles"),
    (r"evaluating a competitor|mentioned evaluating", "Mentioned evaluating a competitor"),
]
_COMPETITOR = re.compile(r"evaluating (?!a competitor)([A-Z][\w&.\- ]+?)(?:\.|$| logged)", re.IGNORECASE)
_POSITIVE: list[tuple[str, str]] = [
    (r"qbr completed", "QBR completed"),
    (r"positive feedback", "Positive feedback"),
    (r"expansion discussion", "Expansion discussed"),
    (r"feature roadmap|roadmap", "Feature roadmap shared"),
    (r"onboarding check-in", "Onboarding check-in"),
    (r"training session", "Training session held"),
    (r"renewal planning", "Renewal planning call held"),
    (r"quarterly check-in", "Quarterly check-in held"),
]


@dataclass(frozen=True)
class NoteSignal:
    polarity: str            # "adverse" | "good"
    label: str
    date: str                # ISO date
    competitor: Optional[str] = None


def subject_of(content: str) -> str:
    """The subject a CSM typed, before the connector's appended detail."""
    return re.split(r"\s+Logged by|\s+Challenge:", content or "", maxsplit=1)[0].strip()


def classify(content: str, date: str) -> Optional[NoteSignal]:
    subject = subject_of(content)
    low = subject.lower()
    if low.startswith("save play"):
        return None                                   # the playbook reads these
    m = _COMPETITOR.search(subject)
    if m:
        name = m.group(1).strip()
        return NoteSignal("adverse", f"Evaluating {name}", date, competitor=name)
    for pattern, label in _RISK:
        if re.search(pattern, low):
            return NoteSignal("adverse", label, date)
    for pattern, label in _POSITIVE:
        if re.search(pattern, low):
            return NoteSignal("good", label, date)
    return None
