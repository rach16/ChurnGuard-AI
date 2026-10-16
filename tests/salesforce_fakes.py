"""
Fakes shared by the connect and token tests: a Salesforce that answers the token,
identity, revoke, query and limits endpoints over an httpx mock transport, and an
in-memory store with the methods the flow and the token service call.

The store's connection context manager rolls back on an exception, like the
pool's does. A refused-state audit row was lost to exactly that once, so the fake
has to be this honest or the test cannot see the bug.
"""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx

from integrations.connections import Connection
from integrations.salesforce_oauth import SalesforceOAuth

ORG = "00Dg700000J62qbEAB"
OTHER_ORG = "00D000000000001AAA"
USER = "005g700000AbCdEfGH"
HOST = "https://orgfarm-e065461ef9-dev-ed.develop.my.salesforce.com"
SITE = "https://staging.example.test"
REFRESH = "5Aep861" + "r" * 60
ACCESS = f"{ORG}!AQEAQ" + "a" * 50
CODE = "aPrxGt" + "c" * 40


class FakeSalesforce:
    """Records every exchange, refresh and revoke so a test can assert that a
    refused token was revoked, that the exchange carried the verifier and the
    secret, and how many refreshes a sequence of calls really made."""

    def __init__(self, org=ORG, instance=HOST, refresh=REFRESH, fail=None, *,
                 rotate=True, refresh_fail=None, api_401_once=False):
        self.org, self.instance, self.refresh, self.fail = org, instance, refresh, fail
        self.rotate, self.refresh_fail, self.api_401_once = rotate, refresh_fail, api_401_once
        self.exchanges: list[dict] = []
        self.refreshes: list[dict] = []
        self.revoked: list[str] = []
        self.api_calls: list[str] = []
        self.issued_access: list[str] = []
        self.refresh_instance = instance

    def _token_body(self, access: str, refresh: str | None) -> dict:
        body = {"access_token": access, "instance_url": self.instance,
                "id": f"https://login.salesforce.com/id/{self.org}/{USER}",
                "scope": "api refresh_token", "issued_at": "1760616000000", "token_type": "Bearer"}
        if refresh:
            body["refresh_token"] = refresh
        return body

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/services/oauth2/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form.get("grant_type") == "refresh_token":
                self.refreshes.append(form)
                if self.refresh_fail:
                    return httpx.Response(400, json={"error": self.refresh_fail, "error_description": "nope"})
                access = f"{self.org}!NEW{len(self.refreshes)}" + "b" * 50
                self.issued_access.append(access)
                new_refresh = ("5AepROT" + secrets.token_hex(20)) if self.rotate else None
                body = self._token_body(access, new_refresh)
                body["instance_url"] = self.refresh_instance
                return httpx.Response(200, json=body)
            self.exchanges.append(form)
            if self.fail:
                return httpx.Response(400, json={"error": self.fail, "error_description": "nope"})
            return httpx.Response(200, json=self._token_body(ACCESS, self.refresh))
        if path == f"/id/{self.org}/{USER}":
            assert request.headers["authorization"] == f"Bearer {ACCESS}"
            return httpx.Response(200, json={"username": "r.banik16@example.com", "display_name": "R"})
        if path == "/services/oauth2/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        if path.startswith("/services/data/"):
            self.api_calls.append(path + ("?" + request.url.query.decode() if request.url.query else ""))
            bearer = request.headers.get("authorization", "")
            if self.api_401_once:
                self.api_401_once = False
                return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID"}])
            if not any(bearer == f"Bearer {t}" for t in self.issued_access):
                return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID"}])
            if path.endswith("/limits"):
                return httpx.Response(200, json={"DailyApiRequests": {"Max": 15000, "Remaining": 14990}})
            if path.endswith("/query"):
                return httpx.Response(200, json={"records": [{"Name": "ChurnGuard Dev", "OrganizationType": "Developer Edition"}]})
            return httpx.Response(404)
        return httpx.Response(404)

    def oauth(self) -> SalesforceOAuth:
        return SalesforceOAuth("client-id", "client-secret-value",
                              http=httpx.Client(transport=httpx.MockTransport(self.handler)))


class FakeConn:
    """Only audit inserts reach execute(); everything else goes through the store."""

    def __init__(self, store):
        self.store = store
        self.committed = len(store.audit)

    def execute(self, sql, params=None):
        if sql.lstrip().lower().startswith("insert into audit_events"):
            self.store.audit.append({"event": params[0], "actor": params[1], "tenant_id": params[2],
                                     "connection_id": params[3], "org_id": params[4],
                                     "detail": params[5].obj})
            return self
        raise AssertionError(f"unexpected SQL in fake: {sql[:60]}")

    def fetchone(self):
        return {"id": len(self.store.audit)}

    def commit(self):
        self.committed = len(self.store.audit)


class FakeDb:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def connection(self):
        conn = FakeConn(self.store)
        try:
            yield conn
        except BaseException:
            del self.store.audit[conn.committed:]
            raise


class FakeStore:
    """In memory, same method names as ConnectionStore."""

    def __init__(self):
        self.db = FakeDb(self)
        self.rows: dict[str, Connection] = {}
        self.states: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.locks: list[str] = []

    def get(self, tenant_id="default", provider="salesforce", conn=None):
        return next(iter(self.rows.values()), None)

    def get_by_id(self, connection_id, conn=None):
        return self.rows.get(connection_id)

    def new_id(self, conn):
        return secrets.token_hex(8)

    def _replace(self, row: Connection, **changes) -> Connection:
        from dataclasses import replace
        updated = replace(row, updated_at=datetime.now(timezone.utc), **changes)
        self.rows[row.id] = updated
        return updated

    def upsert(self, conn, *, id, tenant_id, org_id, instance_url, login_host, user_id, username,
               scopes, sealed, token_issued_at, auth_method="web_server_pkce"):
        existing = next((r for r in self.rows.values() if r.org_id == org_id), None)
        row_id = existing.id if existing else id
        now = datetime.now(timezone.utc)
        self.rows[row_id] = Connection(
            id=row_id, tenant_id=tenant_id, provider="salesforce", auth_method=auth_method,
            org_id=org_id, instance_url=instance_url, login_host=login_host, user_id=user_id,
            username=username, scopes=scopes, status="active", status_reason=None, sealed=sealed,
            token_issued_at=token_issued_at, last_refreshed_at=None, last_used_at=None,
            created_at=existing.created_at if existing else now, updated_at=now)
        return self.rows[row_id]

    def update_token(self, conn, connection_id, sealed, refreshed_at):
        self._replace(self.rows[connection_id], sealed=sealed, last_refreshed_at=refreshed_at,
                      status="active", status_reason=None)

    def mark_refreshed(self, conn, connection_id, refreshed_at):
        self._replace(self.rows[connection_id], last_refreshed_at=refreshed_at,
                      last_used_at=refreshed_at, status="active", status_reason=None)

    def rewrap_token(self, conn, connection_id, sealed):
        self._replace(self.rows[connection_id], sealed=sealed)

    def set_status(self, conn, connection_id, status, reason=None):
        self._replace(self.rows[connection_id], status=status, status_reason=reason)

    def touch(self, conn, connection_id, used_at):
        if connection_id in self.rows:
            self._replace(self.rows[connection_id], last_used_at=used_at)

    def delete(self, conn, connection_id):
        self.rows.pop(connection_id, None)

    def lock(self, conn, connection_id):
        self.locks.append(connection_id)

    def try_lock(self, conn, connection_id):
        return True

    def create_state(self, conn, *, state, code_verifier, login_host, redirect_uri,
                     tenant_id="default", expected_org_id=None, ttl_seconds=600):
        self.states[state] = {"state": state, "code_verifier": code_verifier, "login_host": login_host,
                              "redirect_uri": redirect_uri, "tenant_id": tenant_id,
                              "expected_org_id": expected_org_id, "consumed": False,
                              "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)}

    def consume_state(self, conn, state):
        row = self.states.get(state)
        if not row or row["consumed"] or row["expires_at"] <= datetime.now(timezone.utc):
            return None
        row["consumed"] = True
        return dict(row)

    def purge_states(self, conn, older_than_seconds=3600):
        return 0
