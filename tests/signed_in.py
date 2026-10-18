"""
Signing a test browser in, the way staging does it (2.6.3).

Actions need a signed-in person since the operator key was deleted, so route
tests sign in for real: the app gets the lock and the auth routes exactly as
api.py installs them, a fake Cognito issues really signed tokens, and the
client goes through /auth/login and /auth/callback and keeps the cookie. Then
every action carries the site's Origin, as a browser's would.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from integrations.login import LoginFlow
from tests.cognito_fakes import SITE, FakeCognito, FakeSessionStore

ORIGIN = {"Origin": SITE}


def enable_sign_in(state, provider, audit_db, *, groups=("admin",), tenant="default") -> FakeCognito:
    """Attach a working sign-in to a test State. ``audit_db`` is the FakeDb whose
    store collects the audit rows, so sign-in events land beside the rest."""
    cognito = FakeCognito(groups=groups, tenant=tenant)
    sessions = FakeSessionStore(audit_db)
    state.sessions = sessions
    state.login = LoginFlow(sessions, provider, cognito.client(), site_origin=SITE)
    state.login.cognito_fake = cognito
    return cognito


def app_with(state, *routers_from) -> TestClient:
    """An app wired as api.py wires it: the lock, the given routers, sign-in."""
    import auth_routes
    from session_gate import SessionGate

    app = FastAPI()
    app.add_middleware(SessionGate, get_state=lambda: state)
    for build in routers_from:
        app.include_router(build(lambda: state))
    app.include_router(auth_routes.build_router(lambda: state))
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def sign_in(client: TestClient, cognito: FakeCognito) -> TestClient:
    r = client.get("/auth/login?return=/")
    code, state = cognito.sign_in(r.headers["location"])
    r = client.get(f"/auth/callback?code={code}&state={state}")
    assert r.status_code == 303, r.text
    return client
