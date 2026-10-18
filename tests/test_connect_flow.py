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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from integrations import crypto, redaction  # noqa: E402
from integrations.flow import ConnectFlow, FlowRefused  # noqa: E402
from integrations.salesforce_oauth import (  # noqa: E402
    HostRefused, parse_identity_url, pkce_pair, validate_instance_url,
)
from tests.salesforce_fakes import (  # noqa: E402
    ACCESS, CODE, HOST, ORG, OTHER_ORG, REFRESH, SITE, USER, FakeSalesforce, FakeStore,
)
from tests.signed_in import ORIGIN, app_with, enable_sign_in, sign_in
import connections_routes  # noqa: E402


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
    assert set(sf.revoke_hosts) == {HOST.removeprefix("https://")}
    (row,) = [r for r in store.audit if r["event"] == "org_mismatch_refused"]
    assert row["detail"] == {"expected_org_id": ORG, "returned_org_id": OTHER_ORG}


def test_a_foreign_host_is_refused_and_its_token_revoked(provider):
    store, sf = FakeStore(), FakeSalesforce(instance="https://my.salesforce.com.evil.com")
    flow = make_flow(store, provider, sf)
    with pytest.raises(FlowRefused) as e:
        flow.complete(CODE, callback_params(flow.start()))
    assert e.value.event == "host_refused" and store.rows == {}
    assert REFRESH in sf.revoked
    assert set(sf.revoke_hosts) == {"login.salesforce.com"}   # never the refused host
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
    """No sign-in configured: the routes alone, as on a deployment without a user pool."""
    app = FastAPI()
    app.include_router(connections_routes.build_router(lambda: state))
    return TestClient(app)


def test_without_sign_in_there_are_no_actions(monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)               # the old key opens nothing now
    client = make_app(State(FakeStore(), None))
    r = client.post("/connections/start", json={}, headers={"X-Operator-Key": "k" * 32})
    assert r.status_code == 404 and "sign-in" in r.json()["detail"]


def test_only_a_signed_in_admin_may_connect(provider, monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    state = State(FakeStore(), provider)
    signed_out = app_with(state, connections_routes.build_router)
    enable_sign_in(state, provider, state.connections.db, groups=("operator",))
    assert signed_out.post("/connections/start", json={}, headers=ORIGIN).status_code == 401
    operator = sign_in(app_with(state, connections_routes.build_router), state.login.cognito_fake)
    assert operator.post("/connections/start", json={}, headers=ORIGIN).status_code == 403


def test_status_never_shows_the_username(provider, monkeypatch):
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
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "client-id")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    monkeypatch.delenv("SALESFORCE_ORG_ID", raising=False)
    store = FakeStore()
    sf = FakeSalesforce()
    monkeypatch.setattr(connections_routes.ConnectFlow, "from_env",
                        classmethod(lambda cls, s, p: make_flow(store, provider, sf)))
    state = State(store, provider)
    cognito = enable_sign_in(state, provider, store.db, groups=("admin",))
    client = sign_in(app_with(state, connections_routes.build_router), cognito)

    r = client.post("/connections/start", json={"environment": "production"}, headers=ORIGIN)
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
