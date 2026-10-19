"""
Personal access tokens, for MCP clients (ADR-0016 section 10a, 4.6.5).

A browser session cannot be handed to Claude Desktop or Claude Code, so a
signed-in person makes a token on the Access tokens page and pastes it into
their client. The token reads as that person, in their tenant, through the six
read-only tools; it never acts.

Same custody as the session cookie: 32 random bytes, shown once, only their
SHA-256 stored. A token ends at the first of: its expiry (30 or 90 days), its
owner revoking it, or the person leaving the team. Each use re-confirms the
person with Cognito at most every two minutes, like a read (2.6.6), so a
disabled or removed person's token is revoked at its next use; if Cognito
cannot be asked, the last confirmation stands.

``TokenStore`` is rows in and out; ``TokenAuth`` decides.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from integrations import audit
from integrations.db import Database

logger = logging.getLogger(__name__)

PREFIX = "cg_pat_"
LIFETIME_DAYS = (30, 90)
MAX_LIVE_PER_PERSON = 5
RECHECK_EVERY = timedelta(minutes=2)
# last_used_at is written at most this often, not on every call.
TOUCH_EVERY = timedelta(minutes=1)


def new_token() -> str:
    return PREFIX + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def hash_token(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii", "ignore")).digest()


def display_prefix(value: str) -> str:
    """Enough to tell two tokens apart on the page; far too little to use."""
    return value[:len(PREFIX) + 4]


class TokenRejected(Exception):
    """The token cannot be used. The message is safe to show the client."""


class TokenRefused(Exception):
    """A create or revoke that breaks a rule. The message is safe to show."""


@dataclass(frozen=True)
class AccessToken:
    id: str
    prefix: str
    name: str
    tenant_id: str
    user_sub: str
    email: str
    created_at: datetime
    expires_at: datetime
    last_used_at: Optional[datetime]
    checked_at: datetime
    revoked_at: Optional[datetime]
    revoked_reason: Optional[str]

    def live(self, now: datetime) -> bool:
        return self.revoked_at is None and now < self.expires_at

    def public(self) -> dict[str, Any]:
        """What the page shows. Never the token or its hash."""
        return {"id": self.id, "name": self.name, "prefix": self.prefix, "created_at": self.created_at,
                "expires_at": self.expires_at, "last_used_at": self.last_used_at}


_COLUMNS = ("id::text as id, prefix, name, tenant_id, user_sub, email, created_at, expires_at,"
            " last_used_at, checked_at, revoked_at, revoked_reason")


def _row(r: dict[str, Any]) -> AccessToken:
    return AccessToken(**{k: r[k] for k in AccessToken.__dataclass_fields__})


class TokenStore:
    def __init__(self, db: Database):
        self.db = db

    def create(self, conn, *, value: str, name: str, tenant_id: str, user_sub: str, email: str,
               days: int, now: datetime) -> AccessToken:
        row = conn.execute(
            f"insert into access_tokens (token_hash, prefix, name, tenant_id, user_sub, email,"
            f" created_at, expires_at, checked_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            f" returning {_COLUMNS}",
            (hash_token(value), display_prefix(value), name, tenant_id, user_sub, email,
             now, now + timedelta(days=days), now),
        ).fetchone()
        return _row(row)

    def live_for(self, conn, user_sub: str, now: datetime) -> list[AccessToken]:
        rows = conn.execute(
            f"select {_COLUMNS} from access_tokens where user_sub = %s and revoked_at is null"
            f" and expires_at > %s order by created_at desc",
            (user_sub, now),
        ).fetchall()
        return [_row(r) for r in rows]

    def by_value(self, value: str) -> Optional[AccessToken]:
        with self.db.connection() as conn:
            row = conn.execute(f"select {_COLUMNS} from access_tokens where token_hash = %s",
                               (hash_token(value),)).fetchone()
        return _row(row) if row else None

    def revoke(self, conn, token_id: str, *, user_sub: str, reason: str, now: datetime) -> Optional[AccessToken]:
        """Only the owner's own live token; anyone else's reads as not found."""
        row = conn.execute(
            f"update access_tokens set revoked_at = %s, revoked_reason = %s"
            f" where id::text = %s and user_sub = %s and revoked_at is null returning {_COLUMNS}",
            (now, reason, token_id, user_sub),
        ).fetchone()
        return _row(row) if row else None

    def revoke_for_user(self, conn, user_sub: str, *, reason: str, now: datetime) -> int:
        return conn.execute(
            "update access_tokens set revoked_at = %s, revoked_reason = %s"
            " where user_sub = %s and revoked_at is null",
            (now, reason, user_sub),
        ).rowcount

    def used(self, conn, token_id: str, *, now: datetime, confirmed: bool) -> None:
        if confirmed:
            conn.execute("update access_tokens set last_used_at = %s, checked_at = %s where id::text = %s",
                         (now, now, token_id))
        else:
            conn.execute("update access_tokens set last_used_at = %s where id::text = %s", (now, token_id))


class TokenAuth:
    """Creates, lists, revokes and checks tokens. ``team`` is the TeamAdmin the
    Team page uses: its ``get`` is how a token's owner is re-confirmed."""

    def __init__(self, store: TokenStore, team, *, clock=lambda: datetime.now(timezone.utc)):
        self.store = store
        self.team = team
        self.now = clock

    # ------------------------------------------------------------ the page

    def create(self, *, name: str, days: int, tenant_id: str, user_sub: str, email: str,
               actor: str) -> tuple[AccessToken, str]:
        name = " ".join(name.split())[:60]
        if not name:
            raise TokenRefused("Give the token a name, such as the client it is for.")
        if days not in LIFETIME_DAYS:
            raise TokenRefused(f"A token lasts {' or '.join(map(str, LIFETIME_DAYS))} days.")
        now = self.now()
        value = new_token()
        with self.store.db.connection() as conn:
            if len(self.store.live_for(conn, user_sub, now)) >= MAX_LIVE_PER_PERSON:
                raise TokenRefused(f"You have {MAX_LIVE_PER_PERSON} tokens already. Revoke one first.")
            token = self.store.create(conn, value=value, name=name, tenant_id=tenant_id, user_sub=user_sub,
                                      email=email, days=days, now=now)
            audit.record(conn, "token_created", actor=actor, tenant_id=tenant_id,
                         detail={"token_id": token.id, "name": name, "prefix": token.prefix, "days": days})
        return token, value

    def list(self, user_sub: str) -> list[AccessToken]:
        with self.store.db.connection() as conn:
            return self.store.live_for(conn, user_sub, self.now())

    def revoke(self, token_id: str, *, user_sub: str, tenant_id: str, actor: str) -> AccessToken:
        with self.store.db.connection() as conn:
            token = self.store.revoke(conn, token_id, user_sub=user_sub, reason="revoked by its owner",
                                      now=self.now())
            if token is None:
                raise TokenRefused("No such token.")
            audit.record(conn, "token_revoked", actor=actor, tenant_id=tenant_id,
                         detail={"token_id": token.id, "prefix": token.prefix, "reason": "revoked by its owner"})
        return token

    def revoke_for_user(self, conn, user_sub: str, *, tenant_id: str, actor: str, reason: str) -> int:
        """In the caller's transaction: the Team page's removal."""
        n = self.store.revoke_for_user(conn, user_sub, reason=reason, now=self.now())
        if n:
            audit.record(conn, "token_revoked", actor=actor, tenant_id=tenant_id,
                         detail={"user": f"user:{user_sub}", "count": n, "reason": reason})
        return n

    # ------------------------------------------------------------ a call

    def authenticate(self, value: Optional[str]) -> AccessToken:
        """The live token for a presented value, with its owner confirmed within
        ``RECHECK_EVERY``. Raises TokenRejected with a reason safe to show."""
        if not value or not value.startswith(PREFIX):
            raise TokenRejected("Send an access token as 'Authorization: Bearer cg_pat_…'.")
        token = self.store.by_value(value)
        now = self.now()
        if token is None:
            raise TokenRejected("This access token is not recognised.")
        if token.revoked_at is not None:
            raise TokenRejected("This access token was revoked.")
        if now >= token.expires_at:
            raise TokenRejected("This access token has expired. Make a new one on the Access tokens page.")

        confirmed = False
        if now - token.checked_at >= RECHECK_EVERY:
            from integrations.team import NotOnTeam

            try:
                member = self.team.get(token.tenant_id, token.user_sub)
            except NotOnTeam:
                self._end(token, "its owner is no longer on the team")
            except Exception as e:  # noqa: BLE001 - Cognito unreachable is not a verdict on the person
                logger.warning(f"token kept on last confirmation: {type(e).__name__}")
            else:
                if member.role is None:
                    raise TokenRejected("Your account has no role here. Ask an admin.")
                confirmed = True
        if confirmed or token.last_used_at is None or now - token.last_used_at >= TOUCH_EVERY:
            with self.store.db.connection() as conn:
                self.store.used(conn, token.id, now=now, confirmed=confirmed)
        return token

    def _end(self, token: AccessToken, reason: str) -> None:
        with self.store.db.connection() as conn:
            self.store.revoke_for_user(conn, token.user_sub, reason=reason, now=self.now())
            audit.record(conn, "token_revoked", actor="system", tenant_id=token.tenant_id,
                         detail={"user": f"user:{token.user_sub}", "token_id": token.id, "reason": reason})
        raise TokenRejected("This access token's owner can no longer sign in here.")
