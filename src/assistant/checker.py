"""
The answer checker's code half (ADR-0016 section 6).

A draft is a list of claims, each citing the tool results it relies on by ref
(``T1``, ``T2``). For every claim this extracts each number, money amount,
percentage, date and account name, and requires each to appear **in the tool
results that claim cites**, not anywhere in the answer's results. A number that
is not literally in a cited result fails, which is what forbids model
arithmetic: a total must come from a tool's ``total_arr``, never from adding
rows. Names that a cited ranking orders must appear in that order.

Display precision is respected: ``$56K`` matches 56,000 and 55,800, ``$1.09M``
matches 1,092,500, ``86%`` matches a save rate of 0.857. Numbers the question
itself contains are allowed ("the top 10").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional

MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}

_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?([KkMmBb])?\b")
_PERCENT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?%")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})\b",
                        re.IGNORECASE)
_PLAIN = re.compile(r"(?<![\w.$-])(\d[\d,]*(?:\.\d+)?)(?![\w%])")
_REF = re.compile(r"\bT\d+\b")
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}
_LIMITATION = re.compile(
    r"\b(not available|unavailable|could not|couldn't|can't|cannot|unable to|no (information|data|record|result)s?|"
    r"not (found|recorded|in the data|in the results|shown|known)|did not find|didn't find|not enough)\b",
    re.IGNORECASE)
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
               "hundred": 100, "dozen": 12})
# A number written as a word is a number: "Fifteen accounts mentioned..." passed
# the digit check on the first real run (2026-10-18). "one" and "a" as articles
# are left alone; from two upwards a word is read as a count.
_NUMBER_WORD = re.compile(r"\b(" + "|".join(w for w in _WORDS if _WORDS[w] >= 2) + r")\b", re.IGNORECASE)
# A heuristic score turned into a forecast. Code, not the judge: gpt-4o-mini's
# own faithfulness pass let "most likely to churn next quarter" through on the
# first real run (2026-10-18).
_PREDICTION = re.compile(
    r"\b(will|would|going to|(most |more |least )?likely to|expected to|set to|about to)\s+(churn|leave|cancel)\b|"
    r"\bnext (quarter|month|year)\b|\bchurn (date|probability)\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class Failure:
    kind: str          # number | money | percent | date | name | order | citation
    value: str
    detail: str


@dataclass
class Pool:
    """Everything the cited results state: numbers, dates and text."""
    numbers: list[float] = field(default_factory=list)
    dates: set[tuple[int, int]] = field(default_factory=set)         # (month, day)
    full_dates: set[date] = field(default_factory=set)
    text: str = ""
    rankings: list[list[str]] = field(default_factory=list)


def _walk(value: Any, pool: Pool, texts: list[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        pool.numbers.append(float(value))
    elif isinstance(value, str):
        texts.append(value)
        for m in _MONEY.finditer(value):
            pool.numbers.append(_money(m))
        for m in _PERCENT.finditer(value):
            pool.numbers.append(float(m.group(1)))
        for m in _PLAIN.finditer(value):
            pool.numbers.append(float(m.group(1).replace(",", "")))
        for m in _ISO.finditer(value):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            pool.dates.add((mo, d))
            try:
                pool.full_dates.add(date(y, mo, d))
            except ValueError:
                pass
            pool.numbers.extend([float(y), float(mo), float(d)])
    elif isinstance(value, dict):
        accounts = value.get("accounts")
        if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict) and "rank" in accounts[0]:
            pool.rankings.append([a.get("name", "") for a in sorted(accounts, key=lambda a: a["rank"])])
        for v in value.values():
            _walk(v, pool, texts)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk(v, pool, texts)


def pool_of(results: Iterable[dict[str, Any]]) -> Pool:
    pool = Pool()
    texts: list[str] = []
    for r in results:
        _walk(r.get("data"), pool, texts)
    pool.text = "\n".join(texts).lower()
    return pool


def _money(m: re.Match) -> float:
    value = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return value * _SCALE.get(unit, 1.0)


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def _close(shown: float, decimals: int, candidates: Iterable[float], *, scale: float = 1.0) -> bool:
    """Is ``shown`` (with ``decimals`` places, in units of ``scale``) a rounding
    of any candidate? Half a unit of the last shown place either way."""
    half = 0.5 * 10 ** (-decimals) * scale
    return any(abs(c - shown * scale) <= half + 1e-9 for c in candidates)


def _strip(text: str, names: Iterable[str]) -> str:
    """Remove what must not be read as numbers: names, dates, refs."""
    for n in sorted(names, key=len, reverse=True):
        text = re.sub(re.escape(n), " ", text, flags=re.IGNORECASE)
    text = _ISO.sub(" ", text)
    text = _MONTH_DAY.sub(" ", text)
    text = _REF.sub(" ", text)
    text = _MONEY.sub(" ", text)
    text = _PERCENT.sub(" ", text)
    return text


def _base(name: str) -> str:
    return re.sub(r"\s*\[[^\]]+\]\s*$", "", name).strip()


def check_claim(text: str, cited: list[dict[str, Any]], *, names: Iterable[str],
                question: str = "") -> list[Failure]:
    """Every failure in one claim against the results it cites. Empty means it passes."""
    names = list(names)
    failures: list[Failure] = []
    pool = pool_of(r for r in cited if r.get("ok"))
    allowed = [float(t.replace(",", "")) for t in _PLAIN.findall(question)]
    has_facts = bool(_MONEY.search(text) or _PERCENT.search(text) or _ISO.search(text)
                     or _MONTH_DAY.search(text) or _PLAIN.search(_strip(text, names))
                     or _NUMBER_WORD.search(_strip(text, names)))

    for m in _PREDICTION.finditer(text):
        failures.append(Failure("prediction", m.group(0),
                                "a risk score ranks accounts; it does not predict churn. Say 'highest risk score'"))

    mentioned = [n for n in names if re.search(re.escape(_base(n)), text, re.IGNORECASE)]
    mentioned = [n for n in mentioned if not any(n != o and _base(n).lower() in _base(o).lower()
                                                 for o in mentioned)]
    if not cited or not any(r.get("ok") for r in cited):
        if has_facts or mentioned:
            failures.append(Failure("citation", "", "states facts but cites no successful tool result"))
        elif not _LIMITATION.search(text):
            # "No other competitors were mentioned" asserts an absence the data
            # cannot show (a search returns a sample). Uncited, a claim may only
            # say what was not found or not available. Found on a real run.
            failures.append(Failure("citation", "", "an uncited claim may only state a limitation "
                                                    "(not found, not available), not an absence or a fact"))
        return failures

    for m in _MONEY.finditer(text):
        unit = (m.group(2) or "").lower()
        shown = float(m.group(1).replace(",", ""))
        if not _close(shown, _decimals(m.group(1).replace(",", "")), pool.numbers, scale=_SCALE.get(unit, 1.0)):
            failures.append(Failure("money", m.group(0), "not in the cited results"))
    for m in _PERCENT.finditer(text):
        shown, d = float(m.group(1)), _decimals(m.group(1))
        rates = [c * 100 for c in pool.numbers if 0 <= c <= 1]
        if not (_close(shown, d, pool.numbers) or _close(shown, d, rates) or _close(shown, d, allowed)):
            failures.append(Failure("percent", m.group(0), "not in the cited results"))
    for m in _ISO.finditer(text):
        try:
            when = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            failures.append(Failure("date", m.group(0), "not a date"))
            continue
        if when not in pool.full_dates:
            failures.append(Failure("date", m.group(0), "not in the cited results"))
    for m in _MONTH_DAY.finditer(text):
        key = (MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        if key not in pool.dates:
            failures.append(Failure("date", m.group(0), "not in the cited results"))
    for m in _NUMBER_WORD.finditer(_strip(text, names)):
        value = float(_WORDS[m.group(1).lower()])
        if not (_close(value, 0, pool.numbers) or _close(value, 0, allowed)):
            failures.append(Failure("number", m.group(0), "not in the cited results; numbers must come from a tool"))
    for token in _PLAIN.findall(_strip(text, names)):
        value = float(token.replace(",", ""))
        if not (_close(value, _decimals(token), pool.numbers) or _close(value, _decimals(token), allowed)):
            failures.append(Failure("number", token, "not in the cited results; numbers must come from a tool"))

    for n in mentioned:
        if _base(n).lower() not in pool.text:
            failures.append(Failure("name", n, "this account is not in the cited results"))
    for ranking in pool.rankings:
        in_rank = [n for n in mentioned if n in ranking]
        if len(in_rank) >= 2:
            positions = [text.lower().find(_base(n).lower()) for n in in_rank]
            claimed = [n for _, n in sorted(zip(positions, in_rank))]
            expected = [n for n in ranking if n in in_rank]
            if claimed != expected:
                failures.append(Failure("order", ", ".join(claimed),
                                        f"the cited ranking orders them {', '.join(expected)}"))
    return failures


_HOW_MUCH = re.compile(r"\bhow much\b|\btotal (arr|revenue)\b|\bhow many\b", re.IGNORECASE)


def missing_figure(question: str, claims: list[str]) -> Optional[str]:
    """A question that asks "how much" or "how many" must get a figure: found on
    a real run, where five accounts were listed and their ARR total never
    stated. Returns what to add, or None when the answer has it."""
    m = _HOW_MUCH.search(question)
    if not m:
        return None
    wants_money = "much" in m.group(0).lower() or "arr" in m.group(0).lower() or "revenue" in m.group(0).lower()
    joined = " ".join(claims)
    if wants_money and _MONEY.search(joined) and re.search(r"\b(total|together|combined|in all|altogether|between them|sum)\b",
                                                            joined, re.IGNORECASE):
        return None
    if not wants_money and (_PLAIN.search(joined) or _NUMBER_WORD.search(joined)):
        return None
    return ("the question asks " + m.group(0).lower() + "; add that figure exactly as the tool gives it "
            "(total_arr for every match, shown_arr for the rows listed, or count) and say what it covers")


def strip_refs(text: str) -> str:
    """Remove inline refs a model may echo into prose: "(T1)", "[T2, T3]"."""
    text = re.sub(r"\s*[\(\[]\s*T\d+(\s*,\s*T\d+)*\s*[\)\]]", "", text)
    return re.sub(r"\s+([.,;])", r"\1", text).strip()


def describe(failures: list[Failure]) -> str:
    return "; ".join(f"{f.kind} {f.value!r}: {f.detail}" if f.value else f"{f.kind}: {f.detail}"
                     for f in failures)


def cited_results(citations: list[str], results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [results[c] for c in citations if c in results]


def compact(result: dict[str, Any], limit: int = 6000) -> str:
    """A tool result as the faithfulness judge and the reviser see it."""
    text = json.dumps(result.get("data", result.get("error")), default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def first_unknown(citations: list[str], results: dict[str, Any]) -> Optional[str]:
    return next((c for c in citations if c not in results), None)
