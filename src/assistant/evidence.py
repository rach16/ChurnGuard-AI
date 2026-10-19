"""
What a source shows when it is opened (4.6.3, after the owner's staging test).

A citation used to be a link to the account page, which repeated itself for
every source and did not show what the sentence rested on ("save plays" went to
the same page as "account story", and the page does not list play outcomes).
Now each cited tool result becomes a small, display-ready piece of evidence,
built by code from the result itself, never by the model: the signals with
their dates, the plays with renewed of decided, the account rows, the notes
with their dates, the churn causes, the book totals.
"""

from __future__ import annotations

from typing import Any, Optional

MAX_ITEMS = 10


def _money(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x >= 1_000_000:
        return f"${x / 1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x:,.0f}"
    return f"${x:.0f}"


def evidence_for(tool: Optional[str], result: dict[str, Any]) -> dict[str, Any]:
    """One tool result as the page shows it under a sentence."""
    if not result.get("ok"):
        return {"kind": "error", "text": result.get("error") or "This lookup failed."}
    d = result.get("data") or {}
    if tool == "get_story":
        return {"kind": "story", "account_id": d.get("account_id"), "name": d.get("name"),
                "headline": d.get("headline"),
                "items": [{"kind": s["kind"], "text": s["text"], "source": s["source"], "date": s.get("date")}
                          for s in d.get("signals", [])][:MAX_ITEMS]}
    if tool == "get_plays":
        return {"kind": "plays", "account_id": d.get("account_id"), "name": d.get("name"),
                "top_reason": d.get("top_reason"),
                "items": [{"action": p.get("action"), "renewed": p.get("renewed"), "decided": p.get("decided"),
                           "pending": p.get("pending"), "scope": p.get("scope")}
                          for p in d.get("plays", [])][:MAX_ITEMS]}
    if tool == "find_accounts":
        return {"kind": "accounts", "summary": d.get("summary", []),
                "items": [{"account_id": a["account_id"], "name": a["name"], "arr": _money(a["arr"]),
                           "risk_score": a["risk_score"], "band": a["band"], "renewal_days": a.get("renewal_days")}
                          for a in d.get("accounts", [])][:MAX_ITEMS]}
    if tool == "search_notes":
        return {"kind": "notes",
                "items": [{"account_id": p["account_id"], "account": p["account"], "date": p["date"],
                           "source": "case" if p.get("kind") == "case" else "CSM note", "text": p["text"]}
                          for p in d.get("passages", [])][:MAX_ITEMS]}
    if tool == "get_book_summary":
        return {"kind": "facts", "items": [
            {"label": "Active accounts", "value": f"{d.get('active_accounts')} ({_money(d.get('active_arr'))} ARR)"},
            {"label": "At risk (riskiest 20%)", "value": f"{d.get('at_risk_accounts')} ({_money(d.get('at_risk_arr'))} ARR)"},
            {"label": f"Renewing within {d.get('renewing_within_days')} days",
             "value": f"{d.get('renewing_accounts')} ({_money(d.get('renewing_arr'))} ARR)"},
            {"label": "Weeks of usage history", "value": str(d.get("history_weeks"))}]}
    if tool == "get_causes":
        churned = d.get("churned") or {}
        return {"kind": "causes",
                "items": [{"label": c["cause"], "value": f"{c['accounts']} churned, {_money(c['arr_lost'])} ARR lost"}
                          for c in churned.get("by_cause", [])][:MAX_ITEMS],
                "competitors": [f"{c['name']} ({c['accounts']})" for c in churned.get("competitors", [])][:MAX_ITEMS]}
    return {"kind": "facts", "items": []}
