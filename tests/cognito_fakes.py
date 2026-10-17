"""
A Cognito for the sign-in tests: managed login, the token, revoke and JWKS
endpoints over an httpx mock transport, signing real RS256 ID tokens with a key
generated per test. Plus an in-memory session store with SessionStore's methods.

The tokens are real JWTs so the verifier's checks run for real: a token signed
by another key, for another client, from another issuer, expired, with the wrong
nonce or with ``alg: none`` is refused by the same code that runs in production.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from integrations.cognito import CognitoClient, CognitoConfig
from integrations.sessions import (
    ABSOLUTE_LIFETIME, IDLE_TIMEOUT, PROVIDER, Session, hash_cookie,
)

REGION = "us-west-2"
POOL = "us-west-2_TestPool1"
CLIENT_ID = "testclientid123"
DOMAIN = "https://churnguard-test.auth.us-west-2.amazoncognito.com"
SITE = "https://staging.example.test"
ADMIN_SUB = "11111111-2222-3333-4444-555555555555"
ADMIN_EMAIL = "admin@example.test"


def config() -> CognitoConfig:
    return CognitoConfig(region=REGION, user_pool_id=POOL, client_id=CLIENT_ID,
                         client_secret="client-secret-value", domain=DOMAIN)


class FakeCognito:
    def __init__(self, *, groups=("admin",), tenant: Optional[str] = "default",
                 email: str = ADMIN_EMAIL, sub: str = ADMIN_SUB):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.groups, self.tenant, self.email, self.sub = list(groups), tenant, email, sub
        self.codes: dict[str, dict[str, Any]] = {}
        self.refresh_tokens: set[str] = set()
        self.revoked: list[str] = []
        self.disabled = False
        self.token_overrides: dict[str, Any] = {}
        self.header_overrides: dict[str, Any] = {}
        self.sign_with_other_key = False
        self.exchange_error: Optional[str] = None
        self.issue_refresh = True
        self.jwks_fetches = 0
        self.token_forms: list[dict[str, str]] = []
        self.refresh_scope: dict[str, str] = {}

    # ------------------------------------------------------------ client

    def client(self) -> CognitoClient:
        return CognitoClient(config(), http=httpx.Client(transport=httpx.MockTransport(self.handler)))

    # ------------------------------------------------------- managed login

    def sign_in(self, authorize_url: str) -> tuple[str, str]:
        """What the browser does on the managed login page: the person types a
        password and a code, Cognito redirects back with a code and the state."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        code = "code-" + secrets.token_hex(8)
        self.codes[code] = {"nonce": q["nonce"], "challenge": q["code_challenge"],
                            "redirect_uri": q["redirect_uri"], "scope": q.get("scope", "")}
        return code, q["state"]

    # ------------------------------------------------------------- tokens

    def id_token(self, nonce: Optional[str], scope: str = "openid") -> str:
        """Claims as Cognito issues them for the requested scopes: ``openid``
        alone (or with ``profile``) carries every readable attribute; ``openid``
        with only ``email`` and ``phone`` carries just those and drops custom
        attributes. The real pool did exactly that on staging 2026-10-17."""
        scopes = set(scope.split())
        narrowed = bool(scopes - {"openid"}) and "profile" not in scopes
        now = int(time.time())
        claims = {"sub": self.sub, "email": self.email, "aud": CLIENT_ID,
                  "iss": f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}",
                  "token_use": "id", "iat": now, "exp": now + 3600, "auth_time": now}
        if self.groups:
            claims["cognito:groups"] = list(self.groups)
        if self.tenant and not narrowed:
            claims["custom:tenant"] = self.tenant
        if nonce is not None:
            claims["nonce"] = nonce
        claims.update(self.token_overrides)
        headers = {"kid": "k1", **self.header_overrides}
        key = self.other_key if self.sign_with_other_key else self.key
        return jwt.encode(claims, key, algorithm="RS256", headers=headers)

    def _tokens(self, nonce: Optional[str], refresh: Optional[str],
                scope: str = "openid") -> dict[str, Any]:
        body = {"id_token": self.id_token(nonce, scope), "access_token": "access-" + secrets.token_hex(8),
                "token_type": "Bearer", "expires_in": 3600}
        if refresh:
            body["refresh_token"] = refresh
        return body

    def jwks(self) -> dict[str, Any]:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        public.update({"kid": "k1", "alg": "RS256", "use": "sig"})
        return {"keys": [public]}

    # ----------------------------------------------------------- transport

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/jwks.json"):
            self.jwks_fetches += 1
            return httpx.Response(200, json=self.jwks())
        if path == "/oauth2/token":
            assert request.headers["authorization"].startswith("Basic ")
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_forms.append(form)
            if form["grant_type"] == "authorization_code":
                if self.exchange_error:
                    return httpx.Response(400, json={"error": self.exchange_error})
                pending = self.codes.pop(form["code"], None)
                if pending is None or pending["redirect_uri"] != form["redirect_uri"]:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                import base64
                import hashlib
                digest = hashlib.sha256(form["code_verifier"].encode()).digest()
                if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != pending["challenge"]:
                    return httpx.Response(400, json={"error": "invalid_grant",
                                                     "error_description": "PKCE"})
                refresh = ("rt-" + secrets.token_hex(16)) if self.issue_refresh else None
                if refresh:
                    self.refresh_tokens.add(refresh)
                if refresh:
                    self.refresh_scope[refresh] = pending["scope"]
                return httpx.Response(200, json=self._tokens(pending["nonce"], refresh, pending["scope"]))
            if form["grant_type"] == "refresh_token":
                token = form["refresh_token"]
                if self.disabled or token not in self.refresh_tokens or token in self.revoked:
                    return httpx.Response(400, json={"error": "invalid_grant",
                                                     "error_description": "Refresh Token has been revoked"})
                return httpx.Response(200, json=self._tokens(None, None, self.refresh_scope.get(token, "openid")))
        if path == "/oauth2/revoke":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.revoked.append(form["token"])
            return httpx.Response(200)
        return httpx.Response(404)


class FakeSessionStore:
    """In memory, the same methods as SessionStore. ``db`` is a FakeDb, so audit
    rows land in the FakeStore it belongs to."""

    def __init__(self, db):
        self.db = db
        self.states: dict[str, dict[str, Any]] = {}
        self.rows: dict[str, Session] = {}
        self.hashes: dict[bytes, str] = {}

    def create_state(self, conn, *, state, verifier, nonce, redirect_uri, return_to, login_host,
                     ttl_seconds=600):
        self.states[state] = {"provider": PROVIDER, "code_verifier": verifier, "nonce": nonce,
                              "redirect_uri": redirect_uri, "return_to": return_to,
                              "consumed": False, "expires": time.time() + ttl_seconds}

    def consume_state(self, conn, state):
        row = self.states.get(state)
        if not row or row["consumed"] or row["expires"] <= time.time() or row["provider"] != PROVIDER:
            return None
        row["consumed"] = True
        return {k: row[k] for k in ("code_verifier", "redirect_uri", "nonce", "return_to")}

    def new_id(self, conn):
        return secrets.token_hex(16)

    def create(self, conn, *, id, cookie_value, tenant_id, user_sub, email, groups, sealed_refresh, now):
        session = Session(id=id, tenant_id=tenant_id, user_sub=user_sub, email=email,
                          groups=tuple(groups), sealed_refresh=sealed_refresh, created_at=now,
                          last_seen_at=now, checked_at=now, expires_at=now + ABSOLUTE_LIFETIME)
        self.rows[id] = session
        self.hashes[hash_cookie(cookie_value)] = id
        return session

    def get(self, cookie_value, now=None):
        if not cookie_value:
            return None
        now = now or datetime.now(timezone.utc)
        sid = self.hashes.get(hash_cookie(cookie_value))
        session = self.rows.get(sid) if sid else None
        if session is None:
            return None
        if not session.live(now):
            self.rows.pop(sid, None)
            return None
        return session

    def get_by_id(self, session_id, conn=None):
        return self.rows.get(session_id)

    def recheck(self, conn, session_id, groups, now):
        self.rows[session_id] = replace(self.rows[session_id], groups=tuple(groups), checked_at=now)

    def delete(self, conn, session_id):
        self.rows.pop(session_id, None)

    def delete_for_user(self, conn, user_sub):
        doomed = [k for k, v in self.rows.items() if v.user_sub == user_sub]
        for k in doomed:
            self.rows.pop(k)
        return len(doomed)

    def purge_expired(self, conn, now=None):
        now = now or datetime.now(timezone.utc)
        doomed = [k for k, v in self.rows.items() if not v.live(now)]
        for k in doomed:
            self.rows.pop(k)
        return len(doomed)

    def purge_states(self, conn, older_than_seconds=3600):
        return 0


__all__ = ["FakeCognito", "FakeSessionStore", "config", "SITE", "ADMIN_SUB", "ADMIN_EMAIL",
           "CLIENT_ID", "DOMAIN", "IDLE_TIMEOUT"]
