"""
Salesforce OAuth 2.0 web-server flow with PKCE (ADR-0012, sections 1 and 3).

Only the HTTP shape of the flow lives here: building the authorise URL, exchanging
a code, reading the identity, revoking a token, and the one validation Salesforce
cannot do for us, which is whether the ``instance_url`` it returned is a host we
are prepared to send a token to. What to do with the result is the flow module's.

Endpoints, for reference (Salesforce "OAuth 2.0 Web Server Flow"):

    GET  {login}/services/oauth2/authorize   response_type=code, PKCE S256, state
    POST {login}/services/oauth2/token       grant_type=authorization_code | refresh_token
    GET  {id}                                identity: org, user, username
    POST {login}/services/oauth2/revoke      token=...

The HTTP client is injectable so tests drive the flow against a fake transport and
never touch the network.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit

import httpx

LOGIN_HOSTS = {"production": "login.salesforce.com", "sandbox": "test.salesforce.com"}
SCOPES = "api refresh_token"
INSTANCE_SUFFIX = ".my.salesforce.com"
TIMEOUT_SECONDS = 20.0


class OAuthError(Exception):
    """Base for everything below. The message never carries a token or code."""


class HostRefused(OAuthError):
    """The instance_url is not a Salesforce host we will send a token to."""


class ExchangeFailed(OAuthError):
    """Salesforce would not exchange the code (or refresh the token)."""

    def __init__(self, error: str, description: str = ""):
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: Optional[str]
    instance_url: str
    identity_url: str
    scope: str
    issued_at_ms: int

    @property
    def org_id(self) -> str:
        return parse_identity_url(self.identity_url)[0]

    @property
    def user_id(self) -> str:
        return parse_identity_url(self.identity_url)[1]


@dataclass(frozen=True)
class Identity:
    username: Optional[str]
    display_name: Optional[str]


def login_host(environment: str) -> str:
    try:
        return LOGIN_HOSTS[environment]
    except KeyError:
        raise OAuthError(f"unknown environment {environment!r}; use production or sandbox") from None


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge). The verifier stays server-side; the challenge goes
    in the authorise URL. 32 random bytes -> 43 url-safe characters, the RFC minimum."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_state() -> str:
    return secrets.token_urlsafe(32)


def authorize_url(host: str, *, client_id: str, redirect_uri: str, state: str,
                  code_challenge: str, scope: str = SCOPES) -> str:
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # Always show the consent screen on a reconnect too: an operator who
        # clicks Connect should see what they are granting, every time.
        "prompt": "consent",
    })
    return f"https://{host}/services/oauth2/authorize?{query}"


def validate_instance_url(url: str) -> str:
    """The ADR's rule, as code. Returns the normalised origin or raises HostRefused.

    Parsed, not string-matched: ``https://my.salesforce.com.evil.com`` and
    ``https://x.my.salesforce.com@evil.com`` both contain the suffix and both are
    refused, because the parsed host is the thing checked.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        raise HostRefused("instance_url is not a URL") from None
    if parts.scheme != "https":
        raise HostRefused("instance_url is not https")
    if parts.username or parts.password:
        raise HostRefused("instance_url carries user info")
    if parts.port is not None:
        raise HostRefused("instance_url is not on the default port")
    host = (parts.hostname or "").lower()
    if not host.endswith(INSTANCE_SUFFIX) or host == INSTANCE_SUFFIX.lstrip("."):
        raise HostRefused("instance_url host is not under .my.salesforce.com")
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise HostRefused("instance_url host has unexpected characters")
    return f"https://{host}"


_IDENTITY = re.compile(r"^https://[a-z0-9.-]+/id/(00D[A-Za-z0-9]{12,15})/(005[A-Za-z0-9]{12,15})$")


def parse_identity_url(url: str) -> tuple[str, str]:
    """(org_id, user_id) from ``https://login.salesforce.com/id/00D.../005...``."""
    m = _IDENTITY.match(url or "")
    if not m:
        raise OAuthError("token response carries no usable identity URL")
    return m.group(1), m.group(2)


class SalesforceOAuth:
    def __init__(self, client_id: str, client_secret: str, http: Optional[httpx.Client] = None):
        self.client_id = client_id
        self._client_secret = client_secret
        self.http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def _token_call(self, host: str, form: dict[str, str]) -> dict[str, Any]:
        r = self.http.post(f"https://{host}/services/oauth2/token", data=form)
        if r.status_code != 200:
            try:
                body = r.json()
            except ValueError:
                body = {}
            raise ExchangeFailed(str(body.get("error", f"http_{r.status_code}")),
                                 str(body.get("error_description", ""))[:200])
        return r.json()

    def exchange_code(self, host: str, *, code: str, redirect_uri: str,
                      code_verifier: str) -> TokenResponse:
        body = self._token_call(host, {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self._client_secret,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        })
        return self._parse(body)

    def refresh(self, host: str, *, refresh_token: str) -> TokenResponse:
        body = self._token_call(host, {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self._client_secret,
        })
        return self._parse(body)

    @staticmethod
    def _parse(body: dict[str, Any]) -> TokenResponse:
        for field in ("access_token", "instance_url", "id"):
            if not body.get(field):
                raise OAuthError(f"token response is missing {field}")
        return TokenResponse(
            access_token=body["access_token"],
            # Absent on a refresh without rotation: the old one stays valid.
            refresh_token=body.get("refresh_token"),
            instance_url=body["instance_url"],
            identity_url=body["id"],
            scope=body.get("scope", ""),
            issued_at_ms=int(body.get("issued_at", 0) or 0),
        )

    def identity(self, token: TokenResponse) -> Identity:
        r = self.http.get(token.identity_url,
                          headers={"Authorization": f"Bearer {token.access_token}"})
        if r.status_code != 200:
            return Identity(None, None)
        body = r.json()
        return Identity(body.get("username"), body.get("display_name"))

    def revoke(self, base: str, token: str) -> Optional[int]:
        """Best effort. Returns Salesforce's status code (200 means revoked), or None
        when it could not be reached. A refresh token's revocation takes its access
        tokens with it.

        ``base`` is the org's own My Domain origin (``https://x.my.salesforce.com``),
        which is where Salesforce documents the revoke endpoint; a bare login host is
        accepted for the case where the org's host is not trusted. The first two
        disconnects on staging posted to login.salesforce.com and Salesforce did not
        confirm either, which is why the status code is now returned and audited.
        """
        origin = base if base.startswith("https://") else f"https://{base}"
        try:
            r = self.http.post(f"{origin}/services/oauth2/revoke", data={"token": token})
        except httpx.HTTPError:
            return None
        return r.status_code
