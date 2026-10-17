"""
The sign-in flow (ADR-0013, section 2): start, complete, re-check, sign out.

The same shape as the Salesforce connect in ``flow.py``, with Cognito on the
other end: state, nonce and PKCE verifier kept server-side and single use; the
code exchanged here, with the app client's secret; the ID token verified before
anything is believed. A refused sign-in revokes the refresh token it was handed,
writes ``login_refused`` with the reason, and creates nothing.

Who is refused, beyond a bad token: an account in none of the three groups (it
would see nothing; ADR-0013 §3), and an account with no tenant.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from integrations import audit, crypto
from integrations.cognito import (
    CALLBACK_PATH, CognitoClient, CognitoError, IdTokenInvalid, Person, TokenRefused,
)
from integrations.salesforce_oauth import pkce_pair
from integrations.sessions import Session, SessionStore, new_cookie_value

logger = logging.getLogger(__name__)


class LoginRefused(Exception):
    """Sign-in did not happen. ``reason`` is safe to show; ``code`` is for the audit."""

    def __init__(self, reason: str, code: str):
        super().__init__(reason)
        self.reason, self.code = reason, code


class SessionEnded(Exception):
    """The re-check failed: the person was disabled, signed out everywhere, or removed."""


@dataclass(frozen=True)
class Completed:
    session: Session
    cookie_value: str
    return_to: str


def safe_return(path: Optional[str]) -> str:
    """A relative path on this site, or '/'. Never another origin (no open redirect)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path[:512]


def actor(session: Session) -> str:
    return f"user:{session.user_sub}"


class LoginFlow:
    def __init__(self, store: SessionStore, provider: crypto.KeyProvider, cognito: CognitoClient,
                 *, site_origin: str, clock=lambda: datetime.now(timezone.utc)):
        self.store = store
        self.provider = provider
        self.cognito = cognito
        self.site_origin = site_origin.rstrip("/")
        self.now = clock

    @property
    def redirect_uri(self) -> str:
        return f"{self.site_origin}{CALLBACK_PATH}"

    # ------------------------------------------------------------- start

    def start(self, return_to: Optional[str] = None) -> str:
        verifier, challenge = pkce_pair()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.store.db.connection() as conn:
            self.store.create_state(conn, state=state, verifier=verifier, nonce=nonce,
                                    redirect_uri=self.redirect_uri, return_to=safe_return(return_to),
                                    login_host=self.cognito.config.domain)
        return self.cognito.authorize_url(redirect_uri=self.redirect_uri, state=state,
                                          nonce=nonce, challenge=challenge)

    # ---------------------------------------------------------- complete

    def complete(self, code: str, state: str) -> Completed:
        with self.store.db.connection() as conn:
            pending = self.store.consume_state(conn, state) if state else None
            if pending is None:
                audit.record(conn, "login_refused", actor="anonymous",
                             detail={"reason": "state unknown, used or expired"})
                conn.commit()
                raise LoginRefused("This sign-in link has expired or was already used. Start again.",
                                   "state")
        if not code:
            raise LoginRefused("Cognito did not send a code back.", "no_code")

        try:
            tokens = self.cognito.exchange_code(code=code, redirect_uri=pending["redirect_uri"],
                                                verifier=pending["code_verifier"])
        except TokenRefused as e:
            self._refused(None, f"exchange: {e.error}", None)
            raise LoginRefused(f"Cognito refused the sign-in ({e.error}).", "exchange") from None

        try:
            person = self.cognito.verify_id_token(tokens.id_token, nonce=pending["nonce"])
        except IdTokenInvalid as e:
            self._refused(None, f"id token rejected ({e.check})", tokens.refresh_token)
            raise LoginRefused("The sign-in could not be verified.", "id_token") from None

        if person.role is None:
            self._refused(person, "no role: not in admin, operator or viewer", tokens.refresh_token)
            raise LoginRefused("This account has no role here yet. Ask an admin to add you to a group.",
                               "no_role")
        if not person.tenant:
            self._refused(person, "no tenant attribute", tokens.refresh_token)
            raise LoginRefused("This account is not assigned to a tenant yet. Ask an admin.",
                               "no_tenant")
        if not tokens.refresh_token:
            self._refused(person, "no refresh token issued", None)
            raise LoginRefused("Cognito did not issue a session. Check the app client's settings.",
                               "no_refresh")

        cookie = new_cookie_value()
        now = self.now()
        with self.store.db.connection() as conn:
            session_id = self.store.new_id(conn)
            sealed = crypto.seal(self.provider, session_id, tokens.refresh_token,
                                 purpose=crypto.SESSION_PURPOSE)
            session = self.store.create(conn, id=session_id, cookie_value=cookie,
                                        tenant_id=person.tenant, user_sub=person.sub,
                                        email=person.email, groups=person.groups,
                                        sealed_refresh=sealed, now=now)
            audit.record(conn, "login", actor=actor(session), tenant_id=session.tenant_id,
                         detail={"email": person.email, "role": person.role,
                                 "session_id": session.id})
        logger.info(f"✓ signed in: {person.email} as {person.role}")
        return Completed(session, cookie, pending["return_to"] or "/")

    def _refused(self, person: Optional[Person], reason: str, refresh_token: Optional[str]) -> None:
        status = self.cognito.revoke(refresh_token) if refresh_token else None
        with self.store.db.connection() as conn:
            audit.record(conn, "login_refused",
                         actor=f"user:{person.sub}" if person else "anonymous",
                         tenant_id=(person.tenant if person and person.tenant else "default"),
                         detail={"reason": reason, "email": person.email if person else None,
                                 "revoke_status": status})
        logger.warning(f"sign-in refused: {reason}")

    # ----------------------------------------------------------- re-check

    def recheck(self, session: Session) -> Session:
        """Ask Cognito whether this person may still act (ADR-0013 §4). A refusal
        deletes the session and raises; success refreshes the groups."""
        refresh_token = crypto.unseal(self.provider, session.id, session.sealed_refresh,
                                      purpose=crypto.SESSION_PURPOSE)
        try:
            tokens = self.cognito.refresh(refresh_token)
            person = self.cognito.verify_id_token(tokens.id_token, nonce=None)
        except CognitoError as e:
            with self.store.db.connection() as conn:
                self.store.delete(conn, session.id)
                audit.record(conn, "session_revoked", actor="system", tenant_id=session.tenant_id,
                             detail={"user": actor(session), "email": session.email,
                                     "reason": str(e)[:200]})
            raise SessionEnded("Your sign-in is no longer valid. Sign in again.") from None
        finally:
            del refresh_token
        now = self.now()
        with self.store.db.connection() as conn:
            self.store.recheck(conn, session.id, person.groups, now)
        return self.store.get_by_id(session.id)

    # ------------------------------------------------------------ logout

    def logout(self, session: Optional[Session]) -> str:
        """Revoke at Cognito, delete the row, audit. Returns where to send the
        browser: Cognito's logout, which ends its own session and comes back here."""
        if session is not None:
            status: Optional[int] = None
            try:
                token = crypto.unseal(self.provider, session.id, session.sealed_refresh,
                                      purpose=crypto.SESSION_PURPOSE)
                status = self.cognito.revoke(token)
                del token
            except crypto.CustodyError:
                pass
            with self.store.db.connection() as conn:
                self.store.delete(conn, session.id)
                audit.record(conn, "logout", actor=actor(session), tenant_id=session.tenant_id,
                             detail={"email": session.email, "revoke_status": status})
        return self.cognito.logout_url(f"{self.site_origin}/")
