"""
Connect Salesforce, end to end against fakes (ADR-0012, sections 1, 3, 8).

The flow is driven with a fake Salesforce (an httpx mock transport that behaves
like the token, identity and revoke endpoints) and an in-memory store with the
same methods as the Postgres one, so every refusal the ADR lists runs in CI with
no network and no database: a state that is unknown, reused or expired; an org
other than the pinned one; an instance host that is not Salesforce. Each refusal
must revoke the token it was handed, write the audit row, and store nothing.

The HTTP layer is tested through the real router: the operator gate, the public
status, and the callback page, which must carry neither code nor token.
"""

from __future__ import annotations

import logging
import secrets
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from integrations import crypto, redaction  # noqa: E402
from integrations.connections import Connection  # noqa: E402
from integrations.flow import ConnectFlow, FlowRefused  # noqa: E402
from integrations.salesforce_oauth import (  # noqa: E402
    HostRefused, SalesforceOAuth, parse_identity_url, pkce_pair, validate_instance_url,
)
import connections_routes  # noqa: E402

ORG = "00Dg700000J62qbEAB"
OTHER_ORG = "00D000000000001AAA"
USER = "005g700000AbCdEfGH"
HOST = "https://orgfarm-e065461ef9-dev-ed.develop.my.salesforce.com"
SITE = "https://staging.example.test"
REFRESH = "5Aep861" + "r" * 60
ACCESS = f"{ORG}!AQEAQ" + "a" * 50
CODE = "aPrxGt" + "c" * 40


# ------------------------------------------------------------------ pure rules

@pytest.mark.parametrize("url", [
    HOST, "https://acme.my.salesforce.com", "https://acme.sandbox.my.salesforce.com",
    "https://ACME.MY.SALESFORCE.COM",
])
def test_real_salesforce_hosts_are_accepted(url):
    assert validate_instance_url(url).endswith(".my.salesforce.com")


@pytest.mark.parametrize("url", [
    "https://my.salesforce.com.evil.com",
    "https://evil.com/x.my.salesforce.com",
    "https://x.my.salesforce.com@evil.com",
    "http://acme.my.salesforce.com",
    "https://acme.my.salesforce.com:8443",
    "https://my.salesforce.com",
    "https://acme.salesforce.com",
    "https://acme.lightning.force.com",
    "",
])
def test_look_alike_hosts_are_refused(url):
    with pytest.raises(HostRefused):
        validate_instance_url(url)


def test_identity_url_yields_org_and_user():
    assert parse_identity_url(f"https://login.salesforce.com/id/{ORG}/{USER}") == (ORG, USER)
    with pytest.raises(Exception):
        parse_identity_url("https://login.salesforce.com/id/not-an-org")


def test_pkce_challenge_is_s256_of_the_verifier():
    import base64
    import hashlib
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode()


# --------------------------------------------------------------- fake Salesforce

class FakeSalesforce:
    """The three endpoints the flow calls. Records what it was asked so a test can
    assert that a refused token was revoked, and that the exchange carried the
    verifier and the secret."""

    def __init__(self, org=ORG, instance=HOST, refresh=REFRESH, fail=None):
        self.org, self.instance, self.refresh, self.fail = org, instance, refresh, fail
        self.exchanges: list[dict] = []
        self.revoked: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/services/oauth2/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.exchanges.append(form)
            if self.fail:
                return httpx.Response(400, json={"error": self.fail, "error_description": "nope"})
            body = {"access_token": ACCESS, "instance_url": self.instance,
                    "id": f"https://login.salesforce.com/id/{self.org}/{USER}",
                    "scope": "api refresh_token", "issued_at": "1760616000000", "token_type": "Bearer"}
            if self.refresh:
                body["refresh_token"] = self.refresh
            return httpx.Response(200, json=body)
        if path == f"/id/{self.org}/{USER}":
            assert request.headers["authorization"] == f"Bearer {ACCESS}"
            return httpx.Response(200, json={"username": "r.banik16@example.com", "display_name": "R"})
        if path == "/services/oauth2/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        return httpx.Response(404)

    def oauth(self) -> SalesforceOAuth:
        return SalesforceOAuth("client-id", "client-secret-value",
                              http=httpx.Client(transport=httpx.MockTransport(self.handler)))


# ------------------------------------------------------------------ fake store

class FakeConn:
    """A connection whose execute() only ever sees audit inserts, which it records."""

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
    """Like the pool's context manager: an exception inside the block rolls back
    whatever was not committed. The audit row for a refused state was lost exactly
    this way once, so the fake has to be this honest."""

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
    """In memory, same method names as ConnectionStore, for the flow's sake."""

    def __init__(self):
        self.db = FakeDb(self)
        self.rows: dict[str, Connection] = {}
        self.states: dict[str, dict] = {}
        self.audit: list[dict] = []

    def get(self, tenant_id="default", provider="salesforce", conn=None):
        return next(iter(self.rows.values()), None)

    def new_id(self, conn):
        return secrets.token_hex(8)

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


@pytest.fixture
def provider():
    return crypto.LocalKeyProvider({"v1": secrets.token_bytes(32)}, current="v1")


def make_flow(store, provider, sf: FakeSalesforce, pinned=None):
    return ConnectFlow(store, provider, sf.oauth(), site_origin=SITE, pinned_org_id=pinned)


def callback_params(started) -> str:
    return parse_qs(urlsplit(started.authorize_url).query)["state"][0]


# ------------------------------------------------------------------- the flow

def test_connect_stores_a_sealed_token_and_audits_it(provider, caplog):
    store, sf = FakeStore(), FakeSalesforce()
    flow = make_flow(store, provider, sf)

    started = flow.start()
    q = parse_qs(urlsplit(started.authorize_url).query)
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == [SITE + "/api/integrations/salesforce/callback"]
    assert q["scope"] == ["api refresh_token"] and "code_verifier" not in q

    with caplog.at_level(logging.INFO):
        done = flow.complete(CODE, callback_params(started))

    assert done.event == "connect"
    c = done.connection
    assert (c.org_id, c.user_id, c.instance_url, c.status) == (ORG, USER, HOST, "active")
    assert c.username == "r.banik16@example.com"
    assert crypto.unseal(provider, c.id, c.sealed) == REFRESH
    assert REFRESH.encode() not in c.sealed.ciphertext

    # The exchange carried the verifier and the secret, server-side only.
    (form,) = sf.exchanges
    assert form["code_verifier"] and form["client_secret"] == "client-secret-value" and form["code"] == CODE
    assert sf.revoked == []

    # Audited, with the org and host and without the token.
    (row,) = [r for r in store.audit if r["event"] == "connect"]
    assert row["org_id"] == ORG and row["detail"]["instance_url"] == HOST
    assert REFRESH not in str(row) and ACCESS not in str(row)

    # Nothing token-shaped in the logs, before or after redaction.
    for value in (REFRESH, ACCESS, CODE, form["code_verifier"]):
        assert value not in caplog.text
        assert redaction.find_secret(caplog.text) is None


def test_a_reconnect_keeps_the_row_and_replaces_the_token(provider):
    store = FakeStore()
    first = make_flow(store, provider, FakeSalesforce(refresh="5Aep" + "1" * 50))
    one = first.complete(CODE, callback_params(first.start())).connection

    second = make_flow(store, provider, FakeSalesforce(refresh="5Aep" + "2" * 50))
    started = second.start()
    assert store.states[callback_params(started)]["expected_org_id"] == ORG   # pinned to the org connected
    done = second.complete(CODE, callback_params(started))

    assert done.event == "reconnect" and done.connection.id == one.id and len(store.rows) == 1
    assert crypto.unseal(provider, one.id, done.connection.sealed) == "5Aep" + "2" * 50


@pytest.mark.parametrize("state", ["never-issued", ""])
def test_unknown_state_is_rejected_before_any_exchange(provider, state):
    store, sf = FakeStore(), FakeSalesforce()
    flow = make_flow(store, provider, sf)
    with pytest.raises(FlowRefused) as e:
        flow.complete(CODE, state)
    assert e.value.event == "state_rejected"
    assert sf.exchanges == [] and store.rows == {}
    assert [r["event"] for r in store.audit] == ["state_rejected"]


def test_a_state_cannot_be_presented_twice(provider):
    store, sf = FakeStore(), FakeSalesforce()
    flow = make_flow(store, provider, sf)
    state = callback_params(flow.start())
    flow.complete(CODE, state)
    with pytest.raises(FlowRefused) as e:
        flow.complete(CODE, state)
    assert e.value.event == "state_rejected" and len(sf.exchanges) == 1


def test_an_expired_state_is_rejected(provider):
    store, sf = FakeStore(), FakeSalesforce()
    flow = make_flow(store, provider, sf)
    state = callback_params(flow.start())
    store.states[state]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(FlowRefused):
        flow.complete(CODE, state)


def test_another_org_is_refused_and_its_token_revoked(provider):
    store, sf = FakeStore(), FakeSalesforce(org=OTHER_ORG)
    flow = make_flow(store, provider, sf, pinned=ORG)
    with pytest.raises(FlowRefused) as e:
        flow.complete(CODE, callback_params(flow.start()))
    assert e.value.event == "org_mismatch_refused" and OTHER_ORG in e.value.reason
    assert store.rows == {}
    assert REFRESH in sf.revoked and ACCESS in sf.revoked
    (row,) = [r for r in store.audit if r["event"] == "org_mismatch_refused"]
    assert row["detail"] == {"expected_org_id": ORG, "returned_org_id": OTHER_ORG}


def test_a_foreign_host_is_refused_and_its_token_revoked(provider):
    store, sf = FakeStore(), FakeSalesforce(instance="https://my.salesforce.com.evil.com")
    flow = make_flow(store, provider, sf)
    with pytest.raises(FlowRefused) as e:
        flow.complete(CODE, callback_params(flow.start()))
    assert e.value.event == "host_refused" and store.rows == {}
    assert REFRESH in sf.revoked
    assert [r["event"] for r in store.audit] == ["host_refused"]


def test_a_denied_exchange_is_audited_without_the_code(provider):
    store, sf = FakeStore(), FakeSalesforce(fail="invalid_grant")
    flow = make_flow(store, provider, sf)
    with pytest.raises(FlowRefused) as e:
        flow.complete(CODE, callback_params(flow.start()))
    assert e.value.event == "connect_failed" and "invalid_grant" in e.value.reason
    (row,) = store.audit
    assert row["event"] == "connect_failed" and CODE not in str(row)


def test_no_refresh_token_means_nothing_is_kept(provider):
    store, sf = FakeStore(), FakeSalesforce(refresh=None)
    flow = make_flow(store, provider, sf)
    with pytest.raises(FlowRefused, match="refresh token"):
        flow.complete(CODE, callback_params(flow.start()))
    assert store.rows == {} and ACCESS in sf.revoked


# ------------------------------------------------------------------- the HTTP

class State:
    def __init__(self, store=None, provider=None):
        self.connections = store
        self.key_provider = provider
        self.errors = {} if store else {"connections": "DATABASE_URL is not set"}
        self.not_applicable = {}


def make_app(state) -> TestClient:
    app = FastAPI()
    app.include_router(connections_routes.build_router(lambda: state))
    return TestClient(app)


def test_operator_routes_are_404_until_a_key_exists(monkeypatch):
    monkeypatch.delenv("OPERATOR_KEY", raising=False)
    client = make_app(State(FakeStore(), None))
    r = client.post("/connections/start", json={})
    assert r.status_code == 404 and "not enabled" in r.json()["detail"]


def test_operator_routes_refuse_a_wrong_key(monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)
    client = make_app(State(FakeStore(), None))
    assert client.post("/connections/start", json={}).status_code == 403
    assert client.post("/connections/start", json={}, headers={"X-Operator-Key": "wrong"}).status_code == 403


def test_status_is_public_and_never_shows_the_username(provider, monkeypatch):
    monkeypatch.delenv("OPERATOR_KEY", raising=False)
    store = FakeStore()
    flow = make_flow(store, provider, FakeSalesforce())
    flow.complete(CODE, callback_params(flow.start()))

    body = make_app(State(store, provider)).get("/connections").json()
    assert body["enabled"] is True and body["operator_actions"] is False
    c = body["connection"]
    assert c["org_id"] == ORG and c["status"] == "active" and "username" not in c
    assert not any(k in ("sealed", "token_ciphertext", "token_nonce", "wrapped_data_key") for k in c)

    off = make_app(State()).get("/connections").json()
    assert off["enabled"] is False and "DATABASE_URL" in off["reason"] and off["connection"] is None


def test_callback_page_carries_neither_code_nor_token(provider, monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "client-id")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    monkeypatch.delenv("SALESFORCE_ORG_ID", raising=False)
    store = FakeStore()
    sf = FakeSalesforce()
    monkeypatch.setattr(connections_routes.ConnectFlow, "from_env",
                        classmethod(lambda cls, s, p: make_flow(store, provider, sf)))
    client = make_app(State(store, provider))

    r = client.post("/connections/start", json={"environment": "production"},
                    headers={"X-Operator-Key": "k" * 32})
    assert r.status_code == 200 and "state" not in r.json()
    state = parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]

    page = client.get("/integrations/salesforce/callback", params={"code": CODE, "state": state})
    assert page.status_code == 200 and "Connected to Salesforce" in page.text
    assert ORG in page.text and HOST.removeprefix("https://") in page.text
    for secret in (CODE, state, REFRESH, ACCESS, "client-secret-value"):
        assert secret not in page.text
    assert f'postMessage({{"type": "churnguard:salesforce-connect", "ok": true, "event": "connect"}}, "{SITE}")' in page.text

    again = client.get("/integrations/salesforce/callback", params={"code": CODE, "state": state})
    assert again.status_code == 400 and "not valid any more" in again.text

    denied = client.get("/integrations/salesforce/callback", params={"error": "access_denied"})
    assert denied.status_code == 400 and "access_denied" in denied.text and sf.revoked == []
