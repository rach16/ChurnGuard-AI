"""
Connecting a Salesforce org: start, complete, refuse (ADR-0012, sections 1, 3, 8).

``ConnectFlow`` is the one place that decides whether a token may be kept. It is
handed a store, a key provider and an OAuth client, and nothing it does reaches
Salesforce or the database except through them, which is what lets the tests run
the whole flow against fakes and still exercise every refusal.

The refusals are the point. A code that arrives with an unknown, used or expired
state is rejected and audited. A token for a host that is not Salesforce, or for
an org other than the one this deployment is pinned to, is **revoked at Salesforce
before anything else happens**, so a refusal never leaves a live credential behind,
and then audited with the org ids involved. The audit row and the connection row
are written in one transaction.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from integrations import audit, crypto
from integrations.connections import DEFAULT_TENANT, Connection, ConnectionStore
from integrations.salesforce_oauth import (
    ExchangeFailed, HostRefused, OAuthError, SalesforceOAuth, TokenResponse, authorize_url,
    login_host, new_state, parse_identity_url, pkce_pair, validate_instance_url,
)

logger = logging.getLogger(__name__)

CALLBACK_PATH = "/api/integrations/salesforce/callback"
ACTOR = "operator"       # one operator until 2.6 gives them names


class FlowRefused(Exception):
    """A connect attempt that was refused. ``reason`` is safe to show and to log."""

    def __init__(self, reason: str, event: str):
        super().__init__(reason)
        self.reason = reason
        self.event = event


@dataclass(frozen=True)
class Started:
    authorize_url: str
    state: str


@dataclass(frozen=True)
class Completed:
    connection: Connection
    event: str          # connect or reconnect


def redirect_uri(site_origin: str) -> str:
    return site_origin.rstrip("/") + CALLBACK_PATH


class ConnectFlow:
    def __init__(self, store: ConnectionStore, provider: crypto.KeyProvider,
                 oauth: SalesforceOAuth, *, site_origin: str,
                 pinned_org_id: Optional[str] = None, tenant_id: str = DEFAULT_TENANT):
        self.store = store
        self.provider = provider
        self.oauth = oauth
        self.site_origin = site_origin.rstrip("/")
        # SALESFORCE_ORG_ID, when set: even the very first connect must be this org.
        self.pinned_org_id = (pinned_org_id or "").strip() or None
        self.tenant_id = tenant_id

    @classmethod
    def from_env(cls, store: ConnectionStore, provider: crypto.KeyProvider) -> "ConnectFlow":
        return cls(
            store, provider,
            SalesforceOAuth(os.environ["SALESFORCE_CLIENT_ID"], os.environ["SALESFORCE_CLIENT_SECRET"]),
            site_origin=os.environ["PUBLIC_SITE_ORIGIN"],
            pinned_org_id=os.getenv("SALESFORCE_ORG_ID"),
        )

    # ----------------------------------------------------------------- start

    def expected_org_id(self, conn=None) -> Optional[str]:
        """The org a callback must return: the pin from the environment, else the
        org already connected, else none (first connect, unpinned)."""
        if self.pinned_org_id:
            return self.pinned_org_id
        existing = self.store.get(self.tenant_id, conn=conn)
        return existing.org_id if existing else None

    def start(self, environment: str = "production") -> Started:
        host = login_host(environment)
        verifier, challenge = pkce_pair()
        state = new_state()
        uri = redirect_uri(self.site_origin)
        with self.store.db.connection() as conn:
            self.store.create_state(conn, state=state, code_verifier=verifier, login_host=host,
                                    redirect_uri=uri, tenant_id=self.tenant_id,
                                    expected_org_id=self.expected_org_id(conn))
            self.store.purge_states(conn)
        return Started(
            authorize_url(host, client_id=self.oauth.client_id, redirect_uri=uri, state=state,
                          code_challenge=challenge),
            state,
        )

    # -------------------------------------------------------------- complete

    def complete(self, code: str, state: str) -> Completed:
        with self.store.db.connection() as conn:
            pending = self.store.consume_state(conn, state) if state else None
            if pending is None:
                audit.record(conn, "state_rejected", actor=ACTOR, tenant_id=self.tenant_id,
                             detail={"reason": "unknown, used or expired state"})
                # Commit before raising: the pooled connection rolls back on an
                # exception, and the first local run lost this row that way.
                conn.commit()
        if pending is None:
            raise FlowRefused("This sign-in link is not valid any more. Start again from "
                              "the Integrations page.", "state_rejected")

        try:
            token = self.oauth.exchange_code(pending["login_host"], code=code,
                                             redirect_uri=pending["redirect_uri"],
                                             code_verifier=pending["code_verifier"])
        except ExchangeFailed as e:
            self._audit("connect_failed", detail={"stage": "exchange", "error": e.error})
            raise FlowRefused(f"Salesforce did not accept the sign-in ({e.error}).",
                              "connect_failed") from None
        except OAuthError as e:
            self._audit("connect_failed", detail={"stage": "exchange", "error": str(e)})
            raise FlowRefused("Salesforce returned an unusable response.", "connect_failed") from None

        return self._keep_or_refuse(token, pending)

    def _keep_or_refuse(self, token: TokenResponse, pending: dict) -> Completed:
        host = pending["login_host"]
        try:
            org_id, user_id = parse_identity_url(token.identity_url)
        except OAuthError as e:
            self._revoke(host, token)
            self._audit("connect_failed", detail={"stage": "identity", "error": str(e)})
            raise FlowRefused("Salesforce returned no identity for this sign-in.",
                              "connect_failed") from None

        try:
            instance = validate_instance_url(token.instance_url)
        except HostRefused as e:
            self._revoke(host, token)
            self._audit("host_refused", org_id=org_id, detail={"reason": str(e)})
            raise FlowRefused("Salesforce returned an address this service will not send a "
                              "token to. Nothing was stored.", "host_refused") from None

        expected = pending.get("expected_org_id") or self.pinned_org_id
        if expected and org_id != expected:
            self._revoke(host, token)
            self._audit("org_mismatch_refused", org_id=org_id,
                        detail={"expected_org_id": expected, "returned_org_id": org_id})
            raise FlowRefused(f"That login belongs to org {org_id}, and this deployment is "
                              f"connected to {expected}. Nothing was stored.",
                              "org_mismatch_refused")

        if not token.refresh_token:
            self._revoke(host, token)
            self._audit("connect_failed", org_id=org_id, detail={"stage": "token", "error": "no refresh token"})
            raise FlowRefused("Salesforce issued no refresh token. Check the app's scopes "
                              "(refresh_token, offline_access).", "connect_failed")

        identity = self.oauth.identity(token)
        issued = (datetime.fromtimestamp(token.issued_at_ms / 1000, tz=timezone.utc)
                  if token.issued_at_ms else datetime.now(timezone.utc))

        with self.store.db.connection() as conn:
            existing = self.store.get(self.tenant_id, conn=conn)
            # Seal to the row's id: the existing one on a reconnect, a fresh one
            # otherwise, so the ciphertext is bound to the row it will live in.
            connection_id = existing.id if existing and existing.org_id == org_id \
                else self.store.new_id(conn)
            event = "reconnect" if existing and existing.org_id == org_id else "connect"
            sealed = crypto.seal(self.provider, connection_id, token.refresh_token)
            connection = self.store.upsert(
                conn, id=connection_id, tenant_id=self.tenant_id, org_id=org_id,
                instance_url=instance, login_host=host, user_id=user_id,
                username=identity.username, scopes=token.scope, sealed=sealed,
                token_issued_at=issued)
            audit.record(conn, event, actor=ACTOR, tenant_id=self.tenant_id,
                         connection_id=connection.id, org_id=org_id,
                         detail={"instance_url": instance, "login_host": host,
                                 "user_id": user_id, "scopes": token.scope,
                                 "master_key_version": sealed.master_key_version})
        logger.info(f"✓ Salesforce {event}: org {org_id} at {instance} as user {user_id}")
        return Completed(connection, event)

    # --------------------------------------------------------------- helpers

    def _revoke(self, host: str, token: TokenResponse) -> None:
        """Never leave a credential alive for a connection we refused."""
        for value in (token.refresh_token, token.access_token):
            if value:
                self.oauth.revoke(host, value)

    def _audit(self, event: str, *, org_id: Optional[str] = None,
               detail: Optional[dict] = None) -> None:
        with self.store.db.connection() as conn:
            audit.record(conn, event, actor=ACTOR, tenant_id=self.tenant_id, org_id=org_id,
                         detail=detail)
        logger.warning(f"Salesforce connect refused: {event} {detail or ''}")
