"""
Using the stored token: refresh, rotation, cache, death, disconnect
(ADR-0012, section 4). Fakes only, no network, no database.

What these pin: the refresh token is opened once per refresh and the rotated one
is sealed back into the same row; an access token is reused until it stops
working, then refreshed exactly once; ``invalid_grant`` turns the connection into
``needs_reauth`` and nothing retries; a refresh that names a different host is not
followed; disconnect revokes at Salesforce, deletes the row and audits it; and the
operator routes for all of this answer 404, 409 and 502 for the right reasons.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from integrations import crypto  # noqa: E402
from integrations.flow import ConnectFlow  # noqa: E402
from integrations.tokens import ConnectionGone, NeedsReauth, RefreshFailed, TokenService  # noqa: E402
from tests.salesforce_fakes import ACCESS, CODE, HOST, ORG, REFRESH, SITE, FakeSalesforce, FakeStore  # noqa: E402
import connections_routes  # noqa: E402


@pytest.fixture
def provider():
    return crypto.LocalKeyProvider({"v1": secrets.token_bytes(32)}, current="v1")


def connected(store, provider, sf: FakeSalesforce):
    """A connection as the flow leaves it, so the token service starts from reality."""
    from urllib.parse import parse_qs, urlsplit

    flow = ConnectFlow(store, provider, sf.oauth(), site_origin=SITE)
    started = flow.start()
    state = parse_qs(urlsplit(started.authorize_url).query)["state"][0]
    return flow.complete(CODE, state).connection


def service(store, provider, sf: FakeSalesforce, ttl=900.0) -> TokenService:
    return TokenService(store, provider, sf.oauth(), ttl=ttl)


# ----------------------------------------------------------------- refreshing

def test_refresh_rotates_and_reseals_into_the_same_row(provider):
    store, sf = FakeStore(), FakeSalesforce(rotate=True)
    c = connected(store, provider, sf)
    svc = service(store, provider, sf)

    access = svc.access(c.id)
    assert access.instance_url == HOST and access.rotated is True
    assert access.access_token == sf.issued_access[0]

    (form,) = sf.refreshes
    assert form["refresh_token"] == REFRESH and form["client_secret"] == "client-secret-value"
    row = store.rows[c.id]
    new_refresh = crypto.unseal(provider, c.id, row.sealed)
    assert new_refresh != REFRESH and new_refresh.startswith("5AepROT")
    assert row.last_refreshed_at is not None and row.status == "active"
    assert [a["event"] for a in store.audit][-1] == "refresh"
    assert store.audit[-1]["detail"] == {"rotated": True} and REFRESH not in str(store.audit)
    assert store.locks == [c.id]


def test_without_rotation_the_token_stays_and_the_timestamp_moves(provider):
    store, sf = FakeStore(), FakeSalesforce(rotate=False)
    c = connected(store, provider, sf)
    svc = service(store, provider, sf)
    svc.access(c.id)
    row = store.rows[c.id]
    assert crypto.unseal(provider, c.id, row.sealed) == REFRESH
    assert row.last_refreshed_at is not None
    assert store.audit[-1]["detail"] == {"rotated": False}


def test_the_access_token_is_reused_until_it_stops_working(provider):
    store, sf = FakeStore(), FakeSalesforce()
    c = connected(store, provider, sf)
    svc = service(store, provider, sf)

    svc.get(c.id, "limits")
    svc.get(c.id, "limits")
    assert len(sf.refreshes) == 1                       # cached between calls

    sf.api_401_once = True                              # the org's session timed out
    svc.get(c.id, "limits")
    assert len(sf.refreshes) == 2                       # exactly one more refresh
    assert len(sf.api_calls) == 4                       # the 401 and its retry


def test_a_stale_cache_entry_is_refreshed(provider):
    store, sf = FakeStore(), FakeSalesforce()
    c = connected(store, provider, sf)
    svc = service(store, provider, sf, ttl=0.0)
    svc.access(c.id)
    svc.access(c.id)
    assert len(sf.refreshes) == 2


def test_invalid_grant_means_needs_reauth_and_no_retry(provider):
    store, sf = FakeStore(), FakeSalesforce(refresh_fail="invalid_grant")
    c = connected(store, provider, sf)
    svc = service(store, provider, sf)

    with pytest.raises(NeedsReauth, match="invalid_grant"):
        svc.access(c.id)
    row = store.rows[c.id]
    assert row.status == "needs_reauth" and "Connect again" in row.status_reason
    assert [a["event"] for a in store.audit][-1] == "needs_reauth"
    assert store.audit[-1]["actor"] == "system" and store.audit[-1]["detail"] == {"error": "invalid_grant"}

    with pytest.raises(NeedsReauth):
        svc.access(c.id)
    assert len(sf.refreshes) == 1                       # dead tokens are not retried


def test_a_transient_refresh_failure_keeps_the_connection_active(provider):
    store, sf = FakeStore(), FakeSalesforce(refresh_fail="server_error")
    c = connected(store, provider, sf)
    svc = service(store, provider, sf)
    with pytest.raises(RefreshFailed, match="server_error"):
        svc.access(c.id)
    assert store.rows[c.id].status == "active"
    assert store.audit[-1]["event"] == "refresh_failed"


def test_a_refresh_naming_another_host_is_not_followed(provider):
    store, sf = FakeStore(), FakeSalesforce()
    c = connected(store, provider, sf)
    sf.refresh_instance = "https://other.my.salesforce.com"
    svc = service(store, provider, sf)
    with pytest.raises(NeedsReauth, match="different instance"):
        svc.access(c.id)
    row = store.rows[c.id]
    assert row.status == "needs_reauth" and row.instance_url == HOST
    assert store.audit[-1]["event"] == "host_refused"
    assert store.audit[-1]["detail"] == {"returned": "https://other.my.salesforce.com", "pinned": HOST}
    assert sf.api_calls == []


def test_reconnecting_clears_needs_reauth(provider):
    store = FakeStore()
    dead = FakeSalesforce(refresh_fail="invalid_grant")
    c = connected(store, provider, dead)
    with pytest.raises(NeedsReauth):
        service(store, provider, dead).access(c.id)
    assert store.rows[c.id].status == "needs_reauth"

    alive = FakeSalesforce()
    again = connected(store, provider, alive)
    assert again.id == c.id and again.status == "active" and again.status_reason is None
    assert service(store, provider, alive).access(c.id).access_token == alive.issued_access[0]


# --------------------------------------------------------------------- verify

def test_verify_reports_the_org_and_its_allowance(provider):
    store, sf = FakeStore(), FakeSalesforce()
    c = connected(store, provider, sf)
    out = service(store, provider, sf).verify(c.id)
    assert out["ok"] and out["org_name"] == "ChurnGuard Dev" and out["org_type"] == "Developer Edition"
    assert out["api_requests"] == {"max": 15000, "remaining": 14990} and out["token_rotated"] is True
    assert store.rows[c.id].last_used_at is not None
    assert len(sf.refreshes) == 1 and len(sf.api_calls) == 2


# ----------------------------------------------------------------- disconnect

def test_disconnect_revokes_deletes_and_audits(provider):
    store, sf = FakeStore(), FakeSalesforce()
    c = connected(store, provider, sf)
    svc = service(store, provider, sf)
    svc.access(c.id)

    out = svc.disconnect(c.id)
    assert out == {"ok": True, "org_id": ORG, "revoked_at_salesforce": True, "revoke_status": 200}
    assert store.rows == {}
    assert sf.revoked[-1].startswith("5AepROT")          # the current (rotated) token
    assert sf.revoke_hosts[-1] == HOST.removeprefix("https://")   # the org's own host
    row = store.audit[-1]
    assert row["event"] == "disconnect" and row["actor"] == "operator" and row["org_id"] == ORG
    assert row["detail"]["revoked_at_salesforce"] is True and REFRESH not in str(row)

    with pytest.raises(ConnectionGone):
        svc.access(c.id)
    with pytest.raises(ConnectionGone):
        svc.disconnect(c.id)


def test_disconnect_still_deletes_a_token_it_cannot_open(provider):
    store, sf = FakeStore(), FakeSalesforce()
    c = connected(store, provider, sf)
    other_key = crypto.LocalKeyProvider({"v1": secrets.token_bytes(32)}, current="v1")
    out = service(store, other_key, sf).disconnect(c.id)
    assert out["revoked_at_salesforce"] is False and out["revoke_status"] is None and store.rows == {}
    assert store.audit[-1]["detail"]["revoked_at_salesforce"] is False


# ----------------------------------------------------------------------- HTTP

class State:
    def __init__(self, store, provider, tokens=None):
        self.connections = store
        self.key_provider = provider
        self.tokens = tokens
        self.errors = {}
        self.not_applicable = {}


def make_app(state) -> TestClient:
    app = FastAPI()
    app.include_router(connections_routes.build_router(lambda: state))
    return TestClient(app)


KEY = {"X-Operator-Key": "k" * 32}


def test_verify_and_disconnect_routes(provider, monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)
    store, sf = FakeStore(), FakeSalesforce()
    c = connected(store, provider, sf)
    client = make_app(State(store, provider, service(store, provider, sf)))

    assert client.post(f"/connections/{c.id}/verify").status_code == 403
    r = client.post(f"/connections/{c.id}/verify", headers=KEY)
    assert r.status_code == 200 and r.json()["org_name"] == "ChurnGuard Dev"
    assert ACCESS not in r.text and "5Aep" not in r.text

    assert client.post("/connections/nope/verify", headers=KEY).status_code == 404
    assert client.post("/connections/nope/disconnect", headers=KEY).status_code == 404

    r = client.post(f"/connections/{c.id}/disconnect", headers=KEY)
    assert r.status_code == 200 and r.json()["revoked_at_salesforce"] is True
    assert client.get("/connections").json()["connection"] is None


def test_verify_route_says_why_when_the_token_is_dead(provider, monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)
    store, sf = FakeStore(), FakeSalesforce(refresh_fail="invalid_grant")
    c = connected(store, provider, sf)
    client = make_app(State(store, provider, service(store, provider, sf)))
    r = client.post(f"/connections/{c.id}/verify", headers=KEY)
    assert r.status_code == 409 and "invalid_grant" in r.json()["detail"]
    assert client.get("/connections").json()["connection"]["status"] == "needs_reauth"


def test_routes_are_503_without_app_credentials(provider, monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)
    store = FakeStore()
    client = make_app(State(store, provider, tokens=None))
    r = client.post("/connections/x/verify", headers=KEY)
    assert r.status_code == 503 and "SALESFORCE_CLIENT_ID" in r.json()["detail"]


# --------------------------------------------------------------------- rewrap

def test_rewrap_script_moves_every_row_to_the_current_key(provider):
    sys.path.insert(0, str(ROOT / "scripts"))
    from rewrap_tokens import rewrap_all

    store, sf = FakeStore(), FakeSalesforce()
    c = connected(store, provider, sf)
    rotated = crypto.LocalKeyProvider({"v1": provider.keys["v1"], "v2": secrets.token_bytes(32)},
                                      current="v2")

    dry = rewrap_all(store, rotated, apply=False)
    assert dry == [{"connection_id": c.id, "org_id": ORG, "from": "v1", "to": "v2",
                    "changed": True, "applied": False}]
    assert store.rows[c.id].sealed.master_key_version == "v1"      # dry run wrote nothing

    done = rewrap_all(store, rotated, apply=True)
    assert done[0]["applied"] is True
    row = store.rows[c.id]
    assert row.sealed.master_key_version == "v2"
    assert row.sealed.ciphertext == c.sealed.ciphertext               # token untouched
    assert store.audit[-1]["event"] == "rewrap" and store.audit[-1]["detail"] == {"from": "v1", "to": "v2"}

    # Only the new key is needed from here on, and the token still works.
    only_new = crypto.LocalKeyProvider({"v2": rotated.keys["v2"]}, current="v2")
    assert service(store, only_new, sf).access(c.id).instance_url == HOST
    assert rewrap_all(store, only_new, apply=True)[0]["changed"] is False
