"""
Routing rules and the prediction guard (ADR-0016 sections 1 and 2).

The supervisor model proposes which agents to use; these rules are what its
choice is checked against on the golden set, and what is used when the model's
plan is missing or malformed. Written down so the routing can be argued with.

* A named account, or the account on screen, and a question about it: the
  account analyst.
* "Which accounts", a filter, a total, a count, a ranking or a cause: the book
  analyst.
* What someone said, mentioned, asked or complained about: the notes researcher.

The prediction guard is code, not a prompt: a question asking who *will* churn
gets a fixed limitation sentence in front of whatever ranking the agents return.
"""

from __future__ import annotations

import re
from typing import Iterable

AGENTS = ("account", "book", "notes")

_BOOK = re.compile(
    r"\b(which|what) (accounts|customers|companies)\b|\b(list|rank|top|most|least|largest|biggest|how many|"
    r"total|sum|count|all (the )?accounts|segment|enterprise|commercial|smb|portfolio|book|across|renew(al|als|s|ing)? "
    r"(in|within|next)|why (did|do) (customers|accounts|they) (leave|churn)|cause|causes|lost to|competitors?|"
    r"arr|revenue|lose|lost|churned|churn rate|save plays?|worked best)\b",
    re.IGNORECASE)
_NOTES = re.compile(
    r"\b(said|say|says|saying|mention(ed|s)?|complain(ed|s|t|ts)?|told|asked|ask(ed)? about|note|notes|"
    r"case|cases|ticket|tickets|quote|feedback|call|meeting|email|champion|sponsor|evaluating)\b",
    re.IGNORECASE)
_ACCOUNT = re.compile(r"\b(this account|this customer|why is|what should we do|how risky|their risk)\b",
                      re.IGNORECASE)
_PREDICTION = re.compile(
    r"\b(will|going to|likely to|predict|forecast|expected to)\b[^.?!]*\b(churn|leave|cancel|lose)\b|"
    r"\b(churn|leave|cancel)\b[^.?!]*\bnext (quarter|month|year)\b",
    re.IGNORECASE)


def names_in(question: str, names: Iterable[str]) -> list[str]:
    """Account names the question mentions, longest first, by whole-name match
    (and by the name without a bracketed account number, which the connector
    appends to tell duplicates apart)."""
    q = question.lower()
    found = []
    for name in sorted(set(names), key=len, reverse=True):
        base = re.sub(r"\s*\[[^\]]+\]\s*$", "", name).strip().lower()
        if len(base) >= 4 and base in q and not any(base in f.lower() for f in found):
            found.append(name)
    return found


def route(question: str, *, named_accounts: list[str], account_on_screen: bool) -> list[str]:
    """The agents these rules would choose, in AGENTS order; may be empty."""
    chosen = set()
    if named_accounts or (account_on_screen and not _BOOK.search(question)) or _ACCOUNT.search(question):
        chosen.add("account")
    if _BOOK.search(question) and not (named_accounts and not re.search(r"\b(which|list|top|how many|total)\b",
                                                                       question, re.IGNORECASE)):
        chosen.add("book")
    if _NOTES.search(question):
        chosen.add("notes")
    return [a for a in AGENTS if a in chosen]


_ACTION = re.compile(
    r"^\s*(please\s+)?(send|email|e-mail|message|update|delete|remove|change|edit|assign|create|schedule|"
    r"write|post|book|cancel|close|mark|set|add)\b",
    re.IGNORECASE)


def asks_for_action(question: str) -> bool:
    """A request to do something rather than to know something: the assistant
    is read-only, so these are refused by code, whatever the plan says."""
    return bool(_ACTION.search(question))


def asks_for_prediction(question: str) -> bool:
    return bool(_PREDICTION.search(question))


def prediction_limitation(history_weeks: int) -> str:
    weeks = f"{history_weeks} week{'' if history_weeks == 1 else 's'}"
    return (f"This data can't predict who will churn: the org holds {weeks} of usage and no churn model "
            "runs on it. What follows is a ranking by today's risk score, not a prediction.")
