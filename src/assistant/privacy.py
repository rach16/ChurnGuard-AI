"""
What is removed before anything reaches a model (ADR-0016 section 7).

No contact PII is read from Salesforce at all (ADR-0012 section 7), but CSM
notes and case descriptions are free text a person typed, so an email address
or a phone number can be in them. Every tool result passes through ``scrub``
before it is handed to a model: emails and phone numbers are masked, the
secret patterns the log filter already knows (tokens, keys, passwords) are
redacted, and long text is cut so one note cannot fill the context window.
"""

from __future__ import annotations

import re
from typing import Any

from integrations.redaction import redact as redact_secrets

MAX_TEXT = 400          # characters of any one string a model sees

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# International or North American shapes: +44 20 7946 0958, (415) 555-0172,
# 415-555-0172, 415.555.0172. Dates (2026-10-12), money ($56,000) and ids
# (ACC-30029) do not match: a phone needs a 3-3-4 or longer grouped run.
PHONE = re.compile(
    r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)\s?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}(?![\w-])")


def scrub_text(text: str) -> str:
    text = EMAIL.sub("[email removed]", text)
    text = PHONE.sub("[phone removed]", text)
    text = redact_secrets(text)
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - 1].rstrip() + "…"
    return text


def scrub(value: Any) -> Any:
    """Every string anywhere in a JSON-shaped value, scrubbed."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value
