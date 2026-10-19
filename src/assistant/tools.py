"""
The assistant's tools (ADR-0016 section 3).

Six read-only functions over the served snapshot, the same code that serves
the site (``core.story``), so a tool cannot return a figure the site would not
show. Three rules hold for every one of them, enforced here and not in a
prompt:

* **Authorization.** The tenant comes from the signed-in session, never from
  the model. A context whose tenant is not the one being served has no book;
  an ``account_id`` outside the served book is refused as a tool error the agent
  can read, not an exception.
* **Arithmetic belongs to the tool.** Every total, count and ranking an answer
  may need is a field of the result (``total_arr``, ``count``, ``sorted_by``),
  so the model never has to add or rank, and the checker can forbid it.
* **Every result carries its version.** ``snapshot_id`` and ``as_of`` on each
  result, so an answer can tell when its figures and its notes come from
  different syncs. Results are trimmed (20 rows, 400 characters a string) and
  scrubbed of emails, phone numbers and secrets before a model sees them.

``SPECS`` describes the tools as JSON schemas, in the shape OpenAI-style tool
calling and MCP both take.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from assistant.notes_index import NotesIndex
from assistant.privacy import scrub
from core.story import RENEWAL_SOON_DAYS, StoryBuilder

logger = logging.getLogger("churnguard.assistant.tools")

MAX_ROWS = 20
BANDS = ("Critical", "High", "Medium", "Low")


class ToolError(Exception):
    """A refusal the agent should see and work around; the message is safe to show."""


@dataclass(frozen=True)
class ToolContext:
    """Built per request from the session, never from the model."""
    tenant: str                         # the signed-in person's tenant
    served_tenant: str                  # whose snapshot the builder serves
    builder: Optional[StoryBuilder]
    snapshot_id: Optional[str]
    as_of: str
    notes: Optional[NotesIndex] = None


SPECS: list[dict[str, Any]] = [
    {"name": "find_accounts",
     "description": ("Active accounts matching the filters, riskiest first unless sorted by ARR. "
                     "Returns up to `limit` rows plus `count` and `total_arr` over ALL matches, "
                     "and `shown_arr` over the rows returned. Use it to resolve an account name to its id."),
     "parameters": {"type": "object", "additionalProperties": False, "properties": {
         "name": {"type": "string", "description": "Part of the account name, any case"},
         "segment": {"type": "string", "enum": ["Enterprise", "Commercial", "SMB"]},
         "band": {"type": "string", "enum": list(BANDS)},
         "at_risk_only": {"type": "boolean", "description": "Only the riskiest 20% of the book"},
         "renews_within_days": {"type": "integer", "minimum": 0, "maximum": 730},
         "signal": {"type": "string", "description": "Text of an adverse signal, e.g. 'champion', 'seats'"},
         "sort": {"type": "string", "enum": ["risk", "arr"], "default": "risk"},
         "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS, "default": 10}}}},
    {"name": "get_story",
     "description": ("One account's story: headline, signals with source and date (adverse first), "
                     "renewal, risk score and band, and how the score is built."),
     "parameters": {"type": "object", "additionalProperties": False, "required": ["account_id"],
                    "properties": {"account_id": {"type": "integer"}}}},
    {"name": "get_plays",
     "description": "Save plays that worked on accounts with this account's top risk reason, with renewed of decided.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["account_id"],
                    "properties": {"account_id": {"type": "integer"}}}},
    {"name": "get_book_summary",
     "description": "Totals for the whole book: active accounts, at risk, ARR at risk, renewals due within 90 days.",
     "parameters": {"type": "object", "additionalProperties": False, "properties": {}}},
    {"name": "get_causes",
     "description": ("Why customers left: churned accounts by cause with ARR lost, competitors named, "
                     "the active book by its first adverse signal, and every save play's outcome."),
     "parameters": {"type": "object", "additionalProperties": False, "properties": {}}},
    {"name": "search_notes",
     "description": ("Search CSM notes and support cases. Pass account_id to search one account only. "
                     "Each passage has the account, date and source id to cite."),
     "parameters": {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {
         "query": {"type": "string", "minLength": 2, "maxLength": 300},
         "account_id": {"type": "integer"},
         "k": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5}}}},
]
NAMES = tuple(s["name"] for s in SPECS)


class Tools:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    # ------------------------------------------------------------ envelope

    def call(self, name: str, args: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Run one tool by name. Never raises: a refusal or a bad call is a
        result with ``ok: false`` and a reason the agent can act on."""
        fn: Optional[Callable[..., Any]] = getattr(self, name, None) if name in NAMES else None
        if fn is None:
            return self._error(f"No tool named {name!r}. Available: {', '.join(NAMES)}.")
        try:
            return self._ok(fn(**(args or {})))
        except ToolError as e:
            return self._error(str(e))
        except TypeError as e:
            return self._error(f"Bad arguments for {name}: {e}")
        except Exception as e:  # noqa: BLE001 - the agent gets a result, the log gets the trace
            logger.error(f"tool {name} failed: {type(e).__name__}: {e}", exc_info=True)
            return self._error(f"{name} failed on the server ({type(e).__name__}).")

    def _ok(self, data: Any) -> dict[str, Any]:
        return {"ok": True, "snapshot_id": self.ctx.snapshot_id, "as_of": self.ctx.as_of, "data": scrub(data)}

    def _error(self, message: str) -> dict[str, Any]:
        return {"ok": False, "snapshot_id": self.ctx.snapshot_id, "as_of": self.ctx.as_of, "error": message}

    # ------------------------------------------------------- authorization

    def _book(self) -> StoryBuilder:
        if self.ctx.builder is None or self.ctx.tenant != self.ctx.served_tenant:
            raise ToolError("No data is served for your organization.")
        return self.ctx.builder

    def _account(self, account_id: Any) -> dict[str, Any]:
        book = self._book()
        try:
            aid = int(account_id)
        except (TypeError, ValueError):
            raise ToolError("account_id must be a number; find_accounts resolves a name to one.")
        c = book.scorer.get_customer(aid)
        if c is None:
            raise ToolError(f"No active account {aid} in your organization. Use find_accounts to look it up.")
        return c

    # ---------------------------------------------------------------- tools

    def find_accounts(self, name: Optional[str] = None, segment: Optional[str] = None,
                      band: Optional[str] = None, at_risk_only: bool = False,
                      renews_within_days: Optional[int] = None, signal: Optional[str] = None,
                      sort: str = "risk", limit: int = 10) -> dict[str, Any]:
        rows = self._book().queue()
        if name:
            rows = [r for r in rows if name.strip().lower() in r["name"].lower()]
        if segment:
            rows = [r for r in rows if r["segment"].lower() == segment.lower()]
        if band:
            rows = [r for r in rows if r["risk_level"].lower() == band.lower()]
        if at_risk_only:
            rows = [r for r in rows if r["at_risk"]]
        if renews_within_days is not None:
            rows = [r for r in rows if r["renewal_days"] is not None and 0 <= r["renewal_days"] <= int(renews_within_days)]
        if signal:
            rows = [r for r in rows if any(signal.lower() in t.lower() for t in r["adverse"])]
        if sort == "arr":
            rows = sorted(rows, key=lambda r: -r["arr"])
            sorted_by = "arr desc"
        else:
            rows = sorted(rows, key=lambda r: -r["risk_score"])
            sorted_by = "risk_score desc"
        limit = max(1, min(int(limit), MAX_ROWS))
        shown = rows[:limit]
        total, shown_total = float(sum(r["arr"] for r in rows)), float(sum(r["arr"] for r in shown))
        what = " ".join(x for x in (
            "at-risk" if at_risk_only else "", band or "", segment or "", "active") if x)
        # The totals as sentences, so the meaning travels with the number: on a
        # real run the model attached total_arr to "all active accounts" when it
        # covered only the at-risk filter (2026-10-18).
        summary = [f"{len(rows)} {what} accounts match these filters; together they hold ${total:,.0f}."]
        if len(shown) < len(rows):
            summary.append(f"The {len(shown)} listed here, ranked by {sorted_by.split()[0].replace('_', ' ')}, "
                           f"hold ${shown_total:,.0f} together.")
        return {
            "summary": summary,
            "about": ("Active accounts matching the filters. count and total_arr cover every match; "
                      "shown and shown_arr cover only the rows listed. risk_score is today's heuristic "
                      "score, not a churn probability."),
            "count": len(rows), "total_arr": total,
            "shown": len(shown), "shown_arr": shown_total,
            "sorted_by": sorted_by,
            "accounts": [{"rank": i + 1, "account_id": r["id"], "name": r["name"], "segment": r["segment"],
                          "arr": r["arr"], "risk_score": r["risk_score"], "band": r["risk_level"],
                          "at_risk": r["at_risk"], "renewal_days": r["renewal_days"],
                          "lead_signal": r["reason"], "adverse_signals": r["adverse"][:4]}
                         for i, r in enumerate(shown)],
        }

    def get_story(self, account_id: int) -> dict[str, Any]:
        c = self._account(account_id)
        story = self._book().story(c["id"])
        return {
            "account_id": story["id"], "name": story["name"], "segment": story["segment"],
            "industry": story["industry"], "arr": story["arr"], "headline": story["headline"],
            "risk": story["risk"], "renewal": story["renewal"],
            "signals": [{"kind": s["kind"], "text": s["text"], "source": s["source"], "date": s["date"]}
                        for s in story["signals"]],
            "how_built": {"factors": story["how_built"]["factors"],
                          "history_weeks": story["how_built"]["history_weeks"],
                          "at_risk_rule": story["how_built"]["at_risk_rule"]},
        }

    def get_plays(self, account_id: int) -> dict[str, Any]:
        c = self._account(account_id)
        book = self._book()
        plays = book.playbook.plays_for(c["risk_reason"], c["segment"]) if book.playbook is not None else []
        return {"account_id": c["id"], "name": c["name"], "top_reason": c["risk_reason"],
                "plays": [{k: v for k, v in p.to_dict().items()
                           if k in ("action", "renewed", "decided", "pending", "save_rate", "scope",
                                    "cases", "confidence")} for p in plays]}

    def get_book_summary(self) -> dict[str, Any]:
        book = self._book()
        rows = book.queue()
        at_risk = [r for r in rows if r["at_risk"]]
        soon = [r for r in rows if r["renewal_days"] is not None and 0 <= r["renewal_days"] <= RENEWAL_SOON_DAYS]
        return {"about": ("The whole active book. at_risk means the riskiest 20% by today's score; "
                          "renewing counts accounts whose contract ends within the window."),
                "active_accounts": len(rows), "active_arr": float(sum(r["arr"] for r in rows)),
                "at_risk_accounts": len(at_risk), "at_risk_arr": float(sum(r["arr"] for r in at_risk)),
                "at_risk_rule": "the riskiest 20% of the active book",
                "renewing_within_days": RENEWAL_SOON_DAYS, "renewing_accounts": len(soon),
                "renewing_arr": float(sum(r["arr"] for r in soon)),
                "history_weeks": book.scorer.history_weeks()}

    def get_causes(self) -> dict[str, Any]:
        causes = self._book().causes()
        plays = causes.get("plays") or []
        ranked = [{**p, "rank": i + 1} for i, p in enumerate(plays)]
        best = (f"The best record across the book: {plays[0]['action']}, renewed {plays[0]['renewed']} of "
                f"{plays[0]['decided']}." if plays and plays[0].get("decided") else None)
        # The best play as a sentence: an agent once named the play with the
        # most renewals (6 of 10) as the one that worked best, over 6 of 7.
        return {"best_play": best, **{k: v for k, v in causes.items() if k != "plays"}, "plays": ranked,
                "about": ("churned.by_cause counts accounts that have ALREADY churned, by the churn category "
                          "recorded in Salesforce, with the ARR they took; churned.competitors counts churned "
                          "accounts that named each competitor. active.by_signal counts current accounts by "
                          "their first adverse signal. None of these counts what customers said in notes: "
                          "use search_notes for that. plays: every save play, renewed of decided, ranked by "
                          "save rate; best_play names the first.")}

    def search_notes(self, query: str, account_id: Optional[int] = None, k: int = 5) -> dict[str, Any]:
        self._book()
        if self.ctx.notes is None:
            raise ToolError("Notes search is not available on this deployment.")
        customer_id = self._account(account_id)["customer_id"] if account_id is not None else None
        passages = self.ctx.notes.search(query, customer_id=customer_id, k=max(1, min(int(k), 8)))
        return {"about": ("Passages from CSM notes and support cases matching the query, most relevant first. "
                          "A passage is what someone wrote on that date, not a verified fact. This is a "
                          "search sample, not a count of every mention."),
                "query": query, "account_id": account_id,
                "passages": [{"account_id": p.account_id, "account": p.account, "kind": p.kind,
                              "date": p.date, "text": p.text, "source_id": p.source_id,
                              "snapshot_id": p.snapshot_id} for p in passages]}
