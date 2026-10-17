"""
Talking to Cognito: the endpoints the sign-in flow uses, and checking an ID token
(ADR-0013, sections 1 and 2).

The backend is the only OAuth client. It sends the browser to Cognito's managed
login page, takes the code back, exchanges it with the PKCE verifier and the app
client's secret, and verifies the ID token before anything is believed. Nothing
here touches the database; the flow in ``login.py`` does.

An ID token is accepted only when every one of these holds: signed by a key in
the pool's published set, with RS256 and no other algorithm; issued by this pool;
for this app client; ``token_use`` is ``id``; the nonce is the one this server
issued; not expired. A token that fails any of them is refused with the name of
the check, never with its contents.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit

import httpx

ALGORITHMS = ["RS256"]
# ``openid`` alone, on purpose. Cognito puts every attribute the app client may
# read into the ID token only when no other OIDC scope is requested; adding
# ``email`` narrows the token to sub, email and email_verified and drops
# ``custom:tenant``, which refused every sign-in on staging 2026-10-17 (AWS docs,
# "Scopes, M2M, and resource servers", openid).
SCOPES = "openid"
JWKS_TTL_SECONDS = 3600
CALLBACK_PATH = "/api/auth/callback"
ROLES = ("admin", "operator", "viewer")          # highest first


class CognitoError(Exception):
    """Base. The message is safe to show and never holds a token."""


class TokenRefused(CognitoError):
    """Cognito would not exchange the code or refresh the session."""

    def __init__(self, error: str, description: str = ""):
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error


class IdTokenInvalid(CognitoError):
    """The ID token failed a check. ``check`` names which."""

    def __init__(self, check: str):
        super().__init__(f"ID token refused: {check}")
        self.check = check


@dataclass(frozen=True)
class Tokens:
    id_token: str
    access_token: str
    refresh_token: Optional[str]
    expires_in: int


@dataclass(frozen=True)
class Person:
    """Who the ID token says signed in."""
    sub: str
    email: str
    groups: tuple[str, ...]
    tenant: Optional[str]

    @property
    def role(self) -> Optional[str]:
        return next((r for r in ROLES if r in self.groups), None)


@dataclass(frozen=True)
class CognitoConfig:
    region: str
    user_pool_id: str
    client_id: str
    client_secret: str
    domain: str                       # https://<prefix>.auth.<region>.amazoncognito.com

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    @classmethod
    def from_env(cls) -> Optional["CognitoConfig"]:
        """None unless every value is set: a deployment without a user pool has no
        sign-in, and says so, rather than half of one."""
        values = {k: os.getenv(k, "").strip() for k in
                  ("COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID", "COGNITO_CLIENT_SECRET",
                   "COGNITO_DOMAIN")}
        if not all(values.values()):
            return None
        pool = values["COGNITO_USER_POOL_ID"]
        # The pool's region is the prefix of its id. Not AWS_REGION: that names
        # the master key's region, and the two need not match (staging's pool is
        # in us-east-1, its key in us-west-2).
        region = pool.split("_", 1)[0]
        return cls(region=region, user_pool_id=pool, client_id=values["COGNITO_CLIENT_ID"],
                   client_secret=values["COGNITO_CLIENT_SECRET"],
                   domain=normalise_domain(values["COGNITO_DOMAIN"]))


def missing_settings() -> list[str]:
    return [k for k in ("COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID", "COGNITO_CLIENT_SECRET",
                        "COGNITO_DOMAIN") if not os.getenv(k, "").strip()]


def normalise_domain(value: str) -> str:
    """Accepts the bare host or the URL the console shows; always https, no path."""
    value = value.strip().rstrip("/")
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("COGNITO_DOMAIN must be an https host")
    return f"https://{parts.hostname}"


class CognitoClient:
    def __init__(self, config: CognitoConfig, http: Optional[httpx.Client] = None,
                 clock=time.time):
        self.config = config
        self.http = http or httpx.Client(timeout=15.0)
        self.clock = clock
        self._jwks: dict[str, Any] = {}
        self._jwks_at = 0.0
        self._jwks_lock = threading.Lock()

    # ----------------------------------------------------------- endpoints

    def authorize_url(self, *, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
        query = urlencode({
            "response_type": "code", "client_id": self.config.client_id,
            "redirect_uri": redirect_uri, "scope": SCOPES, "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        return f"{self.config.domain}/oauth2/authorize?{query}"

    def logout_url(self, logout_uri: str) -> str:
        """Ends the Cognito session too, so the next sign-in asks for the password."""
        return f"{self.config.domain}/logout?" + urlencode(
            {"client_id": self.config.client_id, "logout_uri": logout_uri})

    def _basic(self) -> dict[str, str]:
        raw = f"{self.config.client_id}:{self.config.client_secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}

    def _token(self, form: dict[str, str]) -> Tokens:
        r = self.http.post(f"{self.config.domain}/oauth2/token", data=form, headers=self._basic())
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200:
            raise TokenRefused(body.get("error") or f"HTTP_{r.status_code}",
                               (body.get("error_description") or "")[:200])
        return Tokens(id_token=body["id_token"], access_token=body["access_token"],
                      refresh_token=body.get("refresh_token"),
                      expires_in=int(body.get("expires_in") or 3600))

    def exchange_code(self, *, code: str, redirect_uri: str, verifier: str) -> Tokens:
        return self._token({"grant_type": "authorization_code", "client_id": self.config.client_id,
                            "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier})

    def refresh(self, refresh_token: str) -> Tokens:
        """Fails for a disabled user, a deleted user, a revoked token or a global
        sign-out: the re-check before every action (ADR-0013 §4)."""
        return self._token({"grant_type": "refresh_token", "client_id": self.config.client_id,
                            "refresh_token": refresh_token})

    def revoke(self, refresh_token: str) -> Optional[int]:
        """Revokes the refresh token and the tokens issued from it. The status
        code, or None when Cognito could not be reached."""
        try:
            r = self.http.post(f"{self.config.domain}/oauth2/revoke",
                               data={"token": refresh_token, "client_id": self.config.client_id},
                               headers=self._basic())
        except httpx.HTTPError:
            return None
        return r.status_code

    # ----------------------------------------------------------- ID token

    def _keys(self, force: bool = False) -> dict[str, Any]:
        with self._jwks_lock:
            if force or not self._jwks or self.clock() - self._jwks_at > JWKS_TTL_SECONDS:
                r = self.http.get(self.config.jwks_url)
                r.raise_for_status()
                self._jwks = {k["kid"]: k for k in r.json().get("keys", []) if "kid" in k}
                self._jwks_at = self.clock()
            return self._jwks

    def verify_id_token(self, token: str, *, nonce: Optional[str]) -> Person:
        import jwt

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise IdTokenInvalid("malformed") from None
        if header.get("alg") not in ALGORITHMS:
            raise IdTokenInvalid("algorithm")
        kid = header.get("kid")
        keys = self._keys()
        if kid not in keys:
            keys = self._keys(force=True)          # the pool rotated its keys
            if kid not in keys:
                raise IdTokenInvalid("unknown signing key")
        key = jwt.PyJWK(keys[kid]).key
        try:
            claims = jwt.decode(token, key, algorithms=ALGORITHMS, audience=self.config.client_id,
                                issuer=self.config.issuer, leeway=30,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.ExpiredSignatureError:
            raise IdTokenInvalid("expired") from None
        except jwt.InvalidAudienceError:
            raise IdTokenInvalid("audience") from None
        except jwt.InvalidIssuerError:
            raise IdTokenInvalid("issuer") from None
        except jwt.PyJWTError:
            raise IdTokenInvalid("signature or claims") from None
        if claims.get("token_use") != "id":
            raise IdTokenInvalid("token_use")
        if nonce is not None and claims.get("nonce") != nonce:
            raise IdTokenInvalid("nonce")
        email = claims.get("email")
        if not email:
            raise IdTokenInvalid("no email")
        groups = claims.get("cognito:groups") or []
        if isinstance(groups, str):
            groups = [groups]
        return Person(sub=claims["sub"], email=email, groups=tuple(sorted(groups)),
                      tenant=claims.get("custom:tenant"))
