"""
The lock (2.6.2, ADR-0013 sections 3 and 4), end to end against fakes.

A real sign-in through the routes (Cognito faked, tokens really signed), then:
signed out, every data route answers 401 and only the probes and sign-in routes
are open; signed in, reads work for every role and actions by role (viewer none,
operator sync / test / roll back, admin also connect and disconnect); every
action re-checks the person with Cognito, so a disabled account is refused and
its session deleted; another tenant's connection does not exist for you; an
action must come from the site's own origin; the audit names the person; and the
operator key still works on /connections only, for this one step.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from integrations import crypto  # noqa: E402
from integrations.flow import ConnectFlow  # noqa: E402
from integrations.login import LoginFlow  # noqa: E402
from integrations.sync import SyncEngine  # noqa: E402
from integrations.tokens import TokenService  # noqa: E402
from tests.cognito_fakes import ADMIN_SUB, SITE, FakeCognito, FakeSessionStore  # noqa: E402
from tests.salesforce_fakes import CODE, FakeSalesforce, FakeStore, FakeSyncStore  # noqa: E402
from tests.test_sync_engine import org_data  # noqa: E402
import auth_routes  # noqa: E402
import connections_routes  # noqa: E402
from session_gate import SessionGate  # noqa: E402

KEY = "k" * 32
ORIGIN = {"Origin": SITE}


class State:
    def __init__(self, **kw):
        self.errors, self.not_applicable = {}, {}
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def provider():
    return crypto.LocalKeyProvider({"v1": secrets.token_bytes(32)}, current="v1")


def world(provider, monkeypatch, *, groups=("admin",), tenant="default", login=True):
    """A connected org with one promoted sync, a sign-in that works, and an app
    wired exactly as api.py wires it: the gate, the connections and auth routes,
    and one stand-in data route."""
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    monkeypatch.setenv("OPERATOR_KEY", KEY)
    for k, v in {"SALESFORCE_CLIENT_ID": "client-id", "SALESFORCE_CLIENT_SECRET": "s"}.items():
        monkeypatch.setenv(k, v)
    sf = FakeSalesforce()
    sf.records = org_data()
    store = FakeStore()
    flow = ConnectFlow(store, provider, sf.oauth(), site_origin=SITE)
    started = flow.start()
    connection = flow.complete(CODE, parse_qs(urlsplit(started.authorize_url).query)["state"][0]).connection
    monkeypatch.setattr(connections_routes.ConnectFlow, "from_env",
                        classmethod(lambda cls, s, p: ConnectFlow(s, p, sf.oauth(), site_origin=SITE)))
    runs = FakeSyncStore(store)
    tokens = TokenService(store, provider, sf.oauth())
    engine = SyncEngine(tokens, store, runs)
    engine.spawn = engine.run
    cognito = FakeCognito(groups=groups, tenant=tenant)
    sessions = FakeSessionStore(store.db)
    login_flow = LoginFlow(sessions, provider, cognito.client(), site_origin=SITE) if login else None
    state = State(connections=store, key_provider=provider, tokens=tokens, sync_runs=runs, sync=engine,
                  login=login_flow, sessions=sessions if login else None)

    app = FastAPI()
    app.add_middleware(SessionGate, get_state=lambda: state)
    app.include_router(connections_routes.build_router(lambda: state))
    app.include_router(auth_routes.build_router(lambda: state))

    @app.get("/dashboard-stats")
    async def stats():
        return {"total_active_customers": 3}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    return client, connection, store, sessions, cognito, sf


def sign_in(client, cognito):
    r = client.get("/auth/login?return=/")
    code, state = cognito.sign_in(r.headers["location"])
    r = client.get(f"/auth/callback?code={code}&state={state}")
    assert r.status_code == 303, r.text
    return client


# ---------------------------------------------------------------- signed out

def test_signed_out_only_the_probes_and_sign_in_are_open(provider, monkeypatch):
    client, c, *_ = world(provider, monkeypatch)
    for path in ("/dashboard-stats", "/connections", f"/connections/{c.id}/runs",
                 f"/connections/{c.id}/metrics"):
        r = client.get(path)
        assert r.status_code == 401 and r.json()["detail"] == "Sign in required.", path
    assert client.get("/health").status_code == 200
    assert client.get("/auth/me").json()["user"] is None
    assert client.get("/auth/login").status_code == 302
    r = client.get("/integrations/salesforce/callback?code=x&state=nope")
    assert r.status_code == 400                                   # reached the route, refused by state


def test_with_sign_in_off_nothing_is_locked(provider, monkeypatch):
    client, c, *_ = world(provider, monkeypatch, login=False)
    assert client.get("/dashboard-stats").status_code == 200
    body = client.get("/connections").json()
    assert body["operator_actions"] is True and body["admin_actions"] is True and body["role"] is None


def test_the_operator_key_still_opens_connections_only(provider, monkeypatch):
    client, c, store, *_ = world(provider, monkeypatch)
    key = {"X-Operator-Key": KEY}
    assert client.post("/connections/sync-due?wait=true", headers=key).json()["outcome"] == "ok"
    assert client.post(f"/connections/{c.id}/sync", headers=key).status_code == 202
    assert client.get("/dashboard-stats", headers=key).status_code == 401
    assert client.post(f"/connections/{c.id}/sync",
                       headers={"X-Operator-Key": "wrong" * 7}).status_code == 401
    assert [a["actor"] for a in store.audit if a["event"] == "sync_started"] == ["operator", "operator"]


# ----------------------------------------------------------------- signed in

def test_a_viewer_reads_everything_and_acts_on_nothing(provider, monkeypatch):
    client, c, *_ = world(provider, monkeypatch, groups=("viewer",))
    sign_in(client, _[-2])
    assert client.get("/dashboard-stats").status_code == 200
    body = client.get("/connections").json()
    assert body["connection"]["id"] == c.id and body["role"] == "viewer"
    assert body["operator_actions"] is False and body["admin_actions"] is False
    for path in (f"/connections/{c.id}/sync", f"/connections/{c.id}/verify", f"/connections/{c.id}/rollback",
                 f"/connections/{c.id}/disconnect", "/connections/start"):
        r = client.post(path, headers=ORIGIN, json={})
        assert r.status_code == 403 and "role" in r.json()["detail"], path


def test_an_operator_syncs_and_cannot_disconnect(provider, monkeypatch):
    client, c, store, sessions, cognito, sf = world(provider, monkeypatch, groups=("operator",))
    sign_in(client, cognito)
    body = client.get("/connections").json()
    assert body["operator_actions"] is True and body["admin_actions"] is False
    r = client.post(f"/connections/{c.id}/sync", headers=ORIGIN)
    assert r.status_code == 202
    started = [a for a in store.audit if a["event"] == "sync_started"][-1]
    assert started["actor"] == f"user:{ADMIN_SUB}"                         # the person, not "operator"
    assert client.post(f"/connections/{c.id}/verify", headers=ORIGIN).status_code == 200
    assert client.post(f"/connections/{c.id}/disconnect", headers=ORIGIN).status_code == 403
    assert client.post("/connections/start", headers=ORIGIN, json={}).status_code == 403


def test_an_admin_connects_and_disconnects_as_themselves(provider, monkeypatch):
    client, c, store, sessions, cognito, sf = world(provider, monkeypatch, groups=("admin",))
    sign_in(client, cognito)
    r = client.post("/connections/start", headers=ORIGIN, json={})
    assert r.status_code == 200
    state = parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]
    r = client.get(f"/integrations/salesforce/callback?code={CODE}&state={state}")
    assert r.status_code == 200 and "Connected" in r.text
    assert store.audit[-1]["event"] == "reconnect" and store.audit[-1]["actor"] == f"user:{ADMIN_SUB}"

    r = client.post(f"/connections/{c.id}/disconnect", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["revoked_at_salesforce"] is True
    assert store.audit[-1]["event"] == "disconnect" and store.audit[-1]["actor"] == f"user:{ADMIN_SUB}"


def test_an_action_from_another_origin_is_refused(provider, monkeypatch):
    client, c, store, sessions, cognito, sf = world(provider, monkeypatch, groups=("admin",))
    sign_in(client, cognito)
    assert client.post(f"/connections/{c.id}/sync").status_code == 403
    assert client.post(f"/connections/{c.id}/sync", headers={"Origin": "https://evil.example"}).status_code == 403
    assert not [a for a in store.audit if a["event"] == "sync_started"]


def test_a_disabled_person_is_refused_at_the_next_action(provider, monkeypatch):
    client, c, store, sessions, cognito, sf = world(provider, monkeypatch, groups=("admin",))
    sign_in(client, cognito)
    assert client.get("/dashboard-stats").status_code == 200
    cognito.disabled = True                                              # disabled in the console
    r = client.post(f"/connections/{c.id}/sync", headers=ORIGIN)
    assert r.status_code == 401 and "no longer valid" in r.json()["detail"]
    assert sessions.rows == {} and client.get("/dashboard-stats").status_code == 401
    assert [a["event"] for a in store.audit][-1] == "session_revoked"


def test_a_role_removed_in_the_console_is_gone_at_the_next_action(provider, monkeypatch):
    client, c, store, sessions, cognito, sf = world(provider, monkeypatch, groups=("admin",))
    sign_in(client, cognito)
    cognito.groups = ["viewer"]                                          # demoted, still signed in
    assert client.post(f"/connections/{c.id}/sync", headers=ORIGIN).status_code == 403
    assert client.get("/connections").json()["role"] == "viewer"


def test_another_tenants_connection_does_not_exist_for_you(provider, monkeypatch):
    client, c, store, sessions, cognito, sf = world(provider, monkeypatch, groups=("admin",), tenant="acme")
    sign_in(client, cognito)
    assert client.get("/connections").json()["connection"] is None
    assert client.get(f"/connections/{c.id}/runs").json()["runs"] == []
    assert client.get(f"/connections/{c.id}/metrics").status_code == 404
    for path in (f"/connections/{c.id}/sync", f"/connections/{c.id}/verify",
                 f"/connections/{c.id}/disconnect", f"/connections/{c.id}/rollback"):
        assert client.post(path, headers=ORIGIN).status_code == 404, path
    assert store.rows[c.id].status == "active"                          # untouched


def test_signing_out_locks_the_site_again(provider, monkeypatch):
    client, c, store, sessions, cognito, sf = world(provider, monkeypatch)
    sign_in(client, cognito)
    assert client.get("/dashboard-stats").status_code == 200
    assert client.post("/auth/logout", headers=ORIGIN).status_code == 303
    assert client.get("/dashboard-stats").status_code == 401
