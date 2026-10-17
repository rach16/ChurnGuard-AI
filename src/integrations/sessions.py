"""
Signed-in sessions and the sign-in flow's single-use state (ADR-0013, section 2).

A store, not a flow: rows in and out, no decisions. ``login.py`` decides.

The cookie value is 32 random bytes. Only its SHA-256 is stored, so a copy of
this table opens no session; ``get`` hashes what the browser presents and looks
that up. A session ends at the first of: its absolute expiry (8 hours), an hour
without a request, sign-out, or a failed re-check with Cognito.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from integrations.crypto import Sealed
from integrations.db import Database

PROVIDER = "cognito"
STATE_TTL_SECONDS = 600
ABSOLUTE_LIFETIME = timedelta(hours=8)
IDLE_TIMEOUT = timedelta(hours=1)
# last_seen_at is written at most this often, not on every request.
TOUCH_EVERY = timedelta(minutes=1)


def new_cookie_value() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def hash_cookie(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii", "ignore")).digest()


@dataclass(frozen=True)
class Session:
    id: str
    tenant_id: str
    user_sub: str
    email: str
    groups: tuple[str, ...]
    sealed_refresh: Sealed
    created_at: datetime
    last_seen_at: datetime
    checked_at: datetime
    expires_at: datetime

    @property
    def role(self) -> Optional[str]:
        from integrations.cognito import ROLES
        return next((r for r in ROLES if r in self.groups), None)

    def live(self, now: datetime) -> bool:
        return now < self.expires_at and now - self.last_seen_at < IDLE_TIMEOUT

    def public(self) -> dict[str, Any]:
        """What the page may know about who is signed in. No token, no hash."""
        return {"email": self.email, "role": self.role, "groups": list(self.groups),
                "tenant": self.tenant_id, "signed_in_at": self.created_at,
                "expires_at": self.expires_at}


_COLUMNS = ("id::text as id, tenant_id, user_sub, email, groups, refresh_ciphertext, refresh_nonce,"
            " wrapped_data_key, master_key_version, created_at, last_seen_at, checked_at, expires_at")


def _row(r: dict[str, Any]) -> Session:
    return Session(
        id=r["id"], tenant_id=r["tenant_id"], user_sub=r["user_sub"], email=r["email"],
        groups=tuple(r["groups"] or ()),
        sealed_refresh=Sealed(bytes(r["refresh_ciphertext"]), bytes(r["refresh_nonce"]),
                              bytes(r["wrapped_data_key"]), r["master_key_version"]),
        created_at=r["created_at"], last_seen_at=r["last_seen_at"], checked_at=r["checked_at"],
        expires_at=r["expires_at"],
    )


class SessionStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------- state

    def create_state(self, conn, *, state: str, verifier: str, nonce: str, redirect_uri: str,
                     return_to: str, login_host: str, ttl_seconds: int = STATE_TTL_SECONDS) -> None:
        conn.execute(
            "insert into oauth_states (state, provider, code_verifier, login_host, redirect_uri,"
            " nonce, return_to, expires_at)"
            " values (%s, %s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))",
            (state, PROVIDER, verifier, login_host, redirect_uri, nonce, return_to, ttl_seconds),
        )

    def consume_state(self, conn, state: str) -> Optional[dict[str, Any]]:
        """Exactly once, while valid, and only a sign-in state: a Salesforce connect
        state presented here is not consumed."""
        row = conn.execute(
            "update oauth_states set consumed_at = now()"
            " where state = %s and provider = %s and consumed_at is null and expires_at > now()"
            " returning code_verifier, redirect_uri, nonce, return_to",
            (state, PROVIDER),
        ).fetchone()
        return dict(row) if row else None

    # ---------------------------------------------------------- sessions

    def new_id(self, conn) -> str:
        """The row id before the row exists, because the refresh token is sealed to it."""
        return str(conn.execute("select gen_random_uuid() as id").fetchone()["id"])

    def create(self, conn, *, id: str, cookie_value: str, tenant_id: str, user_sub: str,
               email: str, groups: tuple[str, ...], sealed_refresh: Sealed,
               now: datetime) -> Session:
        row = conn.execute(
            f"insert into sessions (id, id_hash, tenant_id, user_sub, email, groups,"
            f" refresh_ciphertext, refresh_nonce, wrapped_data_key, master_key_version,"
            f" created_at, last_seen_at, checked_at, expires_at)"
            f" values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning {_COLUMNS}",
            (id, hash_cookie(cookie_value), tenant_id, user_sub, email, list(groups),
             sealed_refresh.ciphertext, sealed_refresh.nonce, sealed_refresh.wrapped_data_key,
             sealed_refresh.master_key_version, now, now, now, now + ABSOLUTE_LIFETIME),
        ).fetchone()
        return _row(row)

    def get(self, cookie_value: Optional[str], now: Optional[datetime] = None) -> Optional[Session]:
        """The live session for a cookie, or None. An expired or idle one is deleted
        on sight. Touches last_seen_at, at most once a minute."""
        if not cookie_value:
            return None
        now = now or datetime.now(timezone.utc)
        with self.db.connection() as conn:
            row = conn.execute(f"select {_COLUMNS} from sessions where id_hash = %s",
                               (hash_cookie(cookie_value),)).fetchone()
            if row is None:
                return None
            session = _row(row)
            if not session.live(now):
                conn.execute("delete from sessions where id = %s", (session.id,))
                return None
            if now - session.last_seen_at >= TOUCH_EVERY:
                conn.execute("update sessions set last_seen_at = %s where id = %s", (now, session.id))
        return session

    def get_by_id(self, session_id: str, conn=None) -> Optional[Session]:
        sql = f"select {_COLUMNS} from sessions where id = %s"
        if conn is not None:
            row = conn.execute(sql, (session_id,)).fetchone()
        else:
            with self.db.connection() as c:
                row = c.execute(sql, (session_id,)).fetchone()
        return _row(row) if row else None

    def recheck(self, conn, session_id: str, groups: tuple[str, ...], now: datetime) -> None:
        """After a successful re-check with Cognito: the groups it returned replace ours."""
        conn.execute("update sessions set groups = %s, checked_at = %s where id = %s",
                     (list(groups), now, session_id))

    def delete(self, conn, session_id: str) -> None:
        conn.execute("delete from sessions where id = %s", (session_id,))

    def delete_for_user(self, conn, user_sub: str) -> int:
        return conn.execute("delete from sessions where user_sub = %s", (user_sub,)).rowcount

    def purge_expired(self, conn, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        return conn.execute(
            "delete from sessions where expires_at <= %s or last_seen_at <= %s",
            (now, now - IDLE_TIMEOUT),
        ).rowcount

    def purge_states(self, conn, older_than_seconds: int = 3600) -> int:
        return conn.execute(
            "delete from oauth_states where provider = %s"
            " and expires_at < now() - make_interval(secs => %s)",
            (PROVIDER, older_than_seconds),
        ).rowcount
