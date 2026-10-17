"""
The audit log: what happened to a connection, by whom, when (ADR-0012, section 8).

Append-only and written in the same transaction as the change it records, so a
connect that fails to audit does not happen. The one rule enforced here rather than
by schema: a row never carries a token, code or secret. ``record`` refuses a detail
that looks like one, by the same patterns the log filter uses.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from integrations.redaction import find_secret

EVENTS = frozenset({
    "connect", "reconnect", "connect_failed",
    "org_mismatch_refused", "host_refused", "state_rejected",
    "refresh", "refresh_failed", "needs_reauth",
    "sync_started", "sync_finished", "sync_deferred", "promotion", "rollback", "retention",
    "disconnect", "purge", "rewrap",
    "login", "login_refused", "logout", "session_revoked",
})


def assert_no_secret(detail: dict[str, Any]) -> None:
    leak = find_secret(json.dumps(detail, default=str))
    if leak:
        raise ValueError(f"audit detail contains a {leak}; refusing to write it")


def record(conn, event: str, *, actor: str, tenant_id: str = "default",
           connection_id: Optional[str] = None, org_id: Optional[str] = None,
           detail: Optional[dict[str, Any]] = None) -> int:
    """Append one event. Returns its id. Raises before writing if detail holds a secret."""
    from psycopg.types.json import Jsonb

    if event not in EVENTS:
        raise ValueError(f"unknown audit event {event!r}")
    detail = detail or {}
    assert_no_secret(detail)
    row = conn.execute(
        "insert into audit_events (event, actor, tenant_id, connection_id, org_id, detail)"
        " values (%s, %s, %s, %s, %s, %s) returning id",
        (event, actor, tenant_id, connection_id, org_id, Jsonb(detail)),
    ).fetchone()
    return row["id"]


def recent(conn, tenant_id: str = "default", limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select id, at, event, actor, connection_id, org_id, detail from audit_events"
        " where tenant_id = %s order by at desc, id desc limit %s",
        (tenant_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
