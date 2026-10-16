"""
Using a stored connection: refresh, cache, call, and know when it is dead
(ADR-0012, section 4, "Refresh" and "Revocation and loss").

The refresh token is the only thing stored and it is opened for exactly one
purpose: to ask Salesforce for an access token. That access token lives in this
process's memory, per connection, for a short while, and is never written down.
When it stops working the refresh runs again; when the refresh itself is refused
(``invalid_grant``: revoked by an admin, idle past the org's 30-day limit, user
deactivated) the connection becomes ``needs_reauth`` and nothing retries until an
operator reconnects.

Single-flight: a refresh runs inside a transaction that holds the connection's
advisory lock. With refresh-token rotation on, two concurrent refreshes would
each get a new token and each invalidate the other's; the lock makes the second
worker wait and then find the first worker's token already in the row.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from integrations import audit, crypto
from integrations.connections import Connection, ConnectionStore
from integrations.salesforce_oauth import (
    ExchangeFailed, HostRefused, OAuthError, SalesforceOAuth, validate_instance_url,
)

logger = logging.getLogger(__name__)

API_VERSION = "v59.0"
ACTOR = "operator"

# How long a fetched access token is reused before the next refresh. Salesforce
# does not say when it expires (the org's session timeout, two hours by default),
# so this is short on purpose and a 401 forces a refresh regardless.
ACCESS_TOKEN_TTL_SECONDS = 900

# Token-endpoint errors that mean "this refresh token will never work again", as
# opposed to a transient failure worth retrying later.
DEAD_TOKEN_ERRORS = frozenset({"invalid_grant", "invalid_app_access", "inactive_user",
                               "inactive_org", "invalid_client_id", "invalid_client"})


class TokenError(Exception):
    """Base. The message is safe to show; it never holds a token."""


class ConnectionGone(TokenError):
    """No such connection."""


class NeedsReauth(TokenError):
    """The stored token is dead. An operator has to connect again."""


class RefreshFailed(TokenError):
    """Transient: Salesforce or the network, try later."""


@dataclass(frozen=True)
class Access:
    connection_id: str
    access_token: str
    instance_url: str
    fetched_at: float          # monotonic
    rotated: bool              # whether this refresh issued a new refresh token

    def fresh(self, ttl: float = ACCESS_TOKEN_TTL_SECONDS) -> bool:
        return time.monotonic() - self.fetched_at < ttl


class TokenService:
    def __init__(self, store: ConnectionStore, provider: crypto.KeyProvider,
                 oauth: SalesforceOAuth, http: Optional[httpx.Client] = None,
                 ttl: float = ACCESS_TOKEN_TTL_SECONDS):
        self.store = store
        self.provider = provider
        self.oauth = oauth
        self.http = http or oauth.http
        self.ttl = ttl
        self._cache: dict[str, Access] = {}
        self._cache_lock = threading.Lock()

    @classmethod
    def from_env(cls, store: ConnectionStore, provider: crypto.KeyProvider) -> "TokenService":
        return cls(store, provider, SalesforceOAuth(os.environ["SALESFORCE_CLIENT_ID"],
                                                    os.environ["SALESFORCE_CLIENT_SECRET"]))

    # ------------------------------------------------------------------ access

    def invalidate(self, connection_id: str) -> None:
        with self._cache_lock:
            self._cache.pop(connection_id, None)

    def access(self, connection_id: str, *, force_refresh: bool = False) -> Access:
        """An access token for the connection, refreshing if needed.

        ``force_refresh`` is for a token that just failed (a 401): the cached one
        is treated as dead, and a refresh runs unless another worker has already
        replaced it.
        """
        with self._cache_lock:
            cached = self._cache.get(connection_id)
        if cached and cached.fresh(self.ttl) and not force_refresh:
            return cached
        access = self._refresh(connection_id, failed=cached.access_token if cached else None)
        with self._cache_lock:
            self._cache[connection_id] = access
        return access

    def _refresh(self, connection_id: str, failed: Optional[str] = None) -> Access:
        now = datetime.now(timezone.utc)
        with self.store.db.connection() as conn:
            self.store.lock(conn, connection_id)
            c = self.store.get_by_id(connection_id, conn)
            if c is None:
                raise ConnectionGone("This connection no longer exists.")
            if c.status != "active":
                raise NeedsReauth(c.status_reason or "The connection needs a fresh login.")

            # Another worker may have refreshed while this one waited for the lock;
            # its result is in the cache and the row already holds its token. A
            # cached token equal to the one that just failed is not a result.
            with self._cache_lock:
                cached = self._cache.get(connection_id)
            if cached and cached.fresh(self.ttl) and cached.access_token != failed:
                return cached

            refresh_token = crypto.unseal(self.provider, c.id, c.sealed)
            try:
                token = self.oauth.refresh(c.login_host, refresh_token=refresh_token)
            except ExchangeFailed as e:
                if e.error in DEAD_TOKEN_ERRORS:
                    reason = f"Salesforce refused the stored token ({e.error}). Connect again."
                    self.store.set_status(conn, c.id, "needs_reauth", reason)
                    audit.record(conn, "needs_reauth", actor="system", tenant_id=c.tenant_id,
                                 connection_id=c.id, org_id=c.org_id, detail={"error": e.error})
                    conn.commit()
                    logger.warning(f"connection {c.id} needs reauth: {e.error}")
                    raise NeedsReauth(reason) from None
                audit.record(conn, "refresh_failed", actor="system", tenant_id=c.tenant_id,
                             connection_id=c.id, org_id=c.org_id, detail={"error": e.error})
                conn.commit()
                raise RefreshFailed(f"Salesforce could not refresh the token ({e.error}).") from None
            except (OAuthError, httpx.HTTPError) as e:
                audit.record(conn, "refresh_failed", actor="system", tenant_id=c.tenant_id,
                             connection_id=c.id, org_id=c.org_id,
                             detail={"error": type(e).__name__})
                conn.commit()
                raise RefreshFailed(f"Salesforce could not be reached ({type(e).__name__}).") from None
            finally:
                del refresh_token

            # The host is pinned at connect time. A refresh that names another is
            # not followed: the row keeps its host and the token is not used.
            try:
                instance = validate_instance_url(token.instance_url)
            except HostRefused as e:
                instance = None
                reason = str(e)
            if instance != c.instance_url:
                reason = (f"Salesforce returned a different instance ({instance or reason}) "
                          f"than the one connected ({c.instance_url}). Connect again.")
                self.store.set_status(conn, c.id, "needs_reauth", reason)
                audit.record(conn, "host_refused", actor="system", tenant_id=c.tenant_id,
                             connection_id=c.id, org_id=c.org_id,
                             detail={"returned": token.instance_url, "pinned": c.instance_url})
                conn.commit()
                raise NeedsReauth(reason)

            rotated = bool(token.refresh_token)
            if rotated:
                sealed = crypto.seal(self.provider, c.id, token.refresh_token)
                self.store.update_token(conn, c.id, sealed, now)
            else:
                self.store.mark_refreshed(conn, c.id, now)
            audit.record(conn, "refresh", actor="system", tenant_id=c.tenant_id,
                         connection_id=c.id, org_id=c.org_id, detail={"rotated": rotated})

        logger.info(f"✓ Salesforce token refreshed for {c.org_id} (rotated={rotated})")
        return Access(c.id, token.access_token, c.instance_url, time.monotonic(), rotated)

    # ---------------------------------------------------------------- calling

    def request(self, connection_id: str, path: str) -> httpx.Response:
        """GET a REST path on the connected org, refreshing once on a 401.

        Returns the response whatever its status, for callers that read headers
        (``Sforce-Limit-Info``) or Salesforce's own error body. Does not touch
        ``last_used_at``; a caller making many requests records one use.
        """
        access = self.access(connection_id)
        r = self._get(access, path)
        if r.status_code == 401:
            access = self.access(connection_id, force_refresh=True)
            r = self._get(access, path)
        return r

    def get(self, connection_id: str, path: str) -> Any:
        """``request`` for a single call: JSON on 200, else an error naming the status."""
        r = self.request(connection_id, path)
        if r.status_code != 200:
            raise RefreshFailed(f"Salesforce answered {r.status_code} for {path.split('?')[0]}.")
        self.touch(connection_id)
        return r.json()

    def touch(self, connection_id: str) -> None:
        with self.store.db.connection() as conn:
            self.store.touch(conn, connection_id, datetime.now(timezone.utc))

    def _get(self, access: Access, path: str) -> httpx.Response:
        url = f"{access.instance_url}/services/data/{API_VERSION}/{path.lstrip('/')}"
        return self.http.get(url, headers={"Authorization": f"Bearer {access.access_token}"})

    def verify(self, connection_id: str) -> dict[str, Any]:
        """Prove the stored token works: the org's name and today's API allowance.

        Also what keeps the token alive: the org expires a refresh token unused for
        30 days, and a verify is a use.
        """
        org = self.get(connection_id, "query?q=SELECT+Name,OrganizationType+FROM+Organization")
        limits = self.get(connection_id, "limits")
        daily = limits.get("DailyApiRequests", {})
        with self._cache_lock:
            access = self._cache.get(connection_id)
        record = org.get("records", [{}])[0]
        return {
            "ok": True,
            "org_name": record.get("Name"),
            "org_type": record.get("OrganizationType"),
            "api_requests": {"max": daily.get("Max"), "remaining": daily.get("Remaining")},
            "token_rotated": bool(access and access.rotated),
            "checked_at": datetime.now(timezone.utc),
        }

    # ------------------------------------------------------------- disconnect

    def disconnect(self, connection_id: str, actor: str = ACTOR) -> dict[str, Any]:
        """Revoke at Salesforce, delete the row, audit. The token dies either way."""
        with self.store.db.connection() as conn:
            self.store.lock(conn, connection_id)
            c: Optional[Connection] = self.store.get_by_id(connection_id, conn)
            if c is None:
                raise ConnectionGone("This connection no longer exists.")
            status: Optional[int] = None
            try:
                refresh_token = crypto.unseal(self.provider, c.id, c.sealed)
                status = self.oauth.revoke(c.instance_url, refresh_token)
                del refresh_token
            except crypto.CustodyError as e:
                # Cannot open it, so cannot revoke it by value. Still deleted: a
                # token nobody can open is as good as dead, and the audit says so.
                logger.warning(f"disconnect {c.id}: token could not be opened ({type(e).__name__})")
            revoked = status == 200
            self.store.delete(conn, c.id)
            audit.record(conn, "disconnect", actor=actor, tenant_id=c.tenant_id,
                         connection_id=c.id, org_id=c.org_id,
                         detail={"revoked_at_salesforce": revoked, "revoke_status": status,
                                 "instance_url": c.instance_url})
        self.invalidate(connection_id)
        logger.info(f"✓ Salesforce disconnected: org {c.org_id} (revoke status {status})")
        return {"ok": True, "org_id": c.org_id, "revoked_at_salesforce": revoked,
                "revoke_status": status}
