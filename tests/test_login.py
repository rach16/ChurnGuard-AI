"""
Operator sign-in against fakes (ADR-0013, sections 2 to 4). No network, no database.

What these pin: the authorise URL carries PKCE, state and nonce, and the state is
single use; a completed sign-in creates a session whose refresh token is sealed
for its own purpose and row; every way an ID token can be wrong is refused by
name and revokes what it was handed; an account with no role or no tenant cannot
sign in; the return path cannot leave the site; the re-check ends a disabled
person's session at once; sign-out revokes at Cognito and deletes the row; and
the routes set a ``__Host-`` cookie, never show a token, and refuse a sign-out
from another origin.
"""

from __future__ import annotations

import secrets
import sys
import time
from datetime import timedelta
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
from integrations.cognito import IdTokenInvalid, normalise_domain  # noqa: E402
from integrations.login import LoginFlow, LoginRefused, SessionEnded, safe_return  # noqa: E402
from integrations.redaction import find_secret  # noqa: E402
from tests.cognito_fakes import (  # noqa: E402
    ADMIN_EMAIL, ADMIN_SUB, CLIENT_ID, DOMAIN, SITE, FakeCognito, FakeSessionStore,
)
from tests.salesforce_fakes import FakeStore  # noqa: E402
import auth_routes  # noqa: E402


@pytest.fixture
def provider():
    return crypto.LocalKeyProvider({"v1": secrets.token_bytes(32)}, current="v1")


def setup(provider, cognito=None):
    cognito = cognito or FakeCognito()
    audit_store = FakeStore()
    sessions = FakeSessionStore(audit_store.db)
    flow = LoginFlow(sessions, provider, cognito.client(), site_origin=SITE)
    return flow, cognito, sessions, audit_store


def signed_in(flow, cognito, return_to="/integrations"):
    code, state = cognito.sign_in(flow.start(return_to))
    return flow.complete(code, state)


# ------------------------------------------------------------------ start

def test_the_authorise_url_carries_pkce_state_and_nonce(provider):
    flow, cognito, sessions, _ = setup(provider)
    url = flow.start("/integrations")
    parts = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert f"{parts.scheme}://{parts.netloc}" == DOMAIN and parts.path == "/oauth2/authorize"
    assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID
    assert q["redirect_uri"] == f"{SITE}/api/auth/callback" and q["scope"] == "openid"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert len(q["state"]) >= 40 and len(q["nonce"]) >= 40 and q["state"] != q["nonce"]
    stored = sessions.states[q["state"]]
    assert stored["nonce"] == q["nonce"] and stored["return_to"] == "/integrations"
    assert "code_verifier" not in url and stored["code_verifier"] not in url


# --------------------------------------------------------------- complete

def test_a_completed_sign_in_creates_a_sealed_session(provider):
    flow, cognito, sessions, audit = setup(provider)
    done = signed_in(flow, cognito)
    s = done.session
    assert done.return_to == "/integrations" and len(done.cookie_value) == 43
    assert (s.user_sub, s.email, s.role, s.tenant_id) == (ADMIN_SUB, ADMIN_EMAIL, "admin", "default")
    assert s.expires_at - s.created_at == timedelta(hours=8)

    refresh = crypto.unseal(provider, s.id, s.sealed_refresh, purpose=crypto.SESSION_PURPOSE)
    assert refresh in cognito.refresh_tokens
    with pytest.raises(crypto.CustodyError):              # bound to its own purpose
        crypto.unseal(provider, s.id, s.sealed_refresh)
    with pytest.raises(crypto.CustodyError):              # and its own row
        crypto.unseal(provider, "another-row", s.sealed_refresh, purpose=crypto.SESSION_PURPOSE)

    assert sessions.get(done.cookie_value).id == s.id
    assert sessions.get("not-the-cookie") is None
    row = audit.audit[-1]
    assert row["event"] == "login" and row["actor"] == f"user:{ADMIN_SUB}"
    assert row["detail"]["email"] == ADMIN_EMAIL and row["detail"]["role"] == "admin"
    assert find_secret(str(audit.audit)) is None and refresh not in str(audit.audit)
    verifier_sent = cognito.token_forms[0]["code_verifier"]
    assert verifier_sent and cognito.token_forms[0]["redirect_uri"] == f"{SITE}/api/auth/callback"


def test_a_state_is_single_use_and_its_refusal_is_audited(provider):
    flow, cognito, sessions, audit = setup(provider)
    code, state = cognito.sign_in(flow.start("/"))
    flow.complete(code, state)
    with pytest.raises(LoginRefused) as e:
        flow.complete(code, state)
    assert e.value.code == "state"
    assert audit.audit[-1]["event"] == "login_refused"            # committed despite the raise
    assert audit.audit[-1]["detail"]["reason"].startswith("state")
    with pytest.raises(LoginRefused):
        flow.complete("anything", "never-issued")


@pytest.mark.parametrize("override,check", [
    ({"token_overrides": {"nonce": "not-the-one"}}, "nonce"),
    ({"token_overrides": {"aud": "another-client"}}, "audience"),
    ({"token_overrides": {"iss": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_Other"}}, "issuer"),
    ({"token_overrides": {"exp": int(time.time()) - 3600}}, "expired"),
    ({"token_overrides": {"token_use": "access"}}, "token_use"),
    ({"sign_with_other_key": True}, "signature or claims"),
    ({"header_overrides": {"kid": "k9"}}, "unknown signing key"),
])
def test_every_wrong_id_token_is_refused_and_its_session_revoked(provider, override, check):
    cognito = FakeCognito()
    for k, v in override.items():
        setattr(cognito, k, v)
    flow, cognito, sessions, audit = setup(provider, cognito)
    with pytest.raises(LoginRefused) as e:
        signed_in(flow, cognito)
    assert e.value.code == "id_token" and sessions.rows == {}
    assert audit.audit[-1]["event"] == "login_refused"
    assert audit.audit[-1]["detail"]["reason"] == f"id token rejected ({check})"
    assert audit.audit[-1]["detail"]["revoke_status"] == 200 and len(cognito.revoked) == 1


def test_alg_none_is_refused_by_name(provider):
    import jwt
    flow, cognito, _, _ = setup(provider)
    unsigned = jwt.encode({"sub": "x"}, key=None, algorithm="none")
    with pytest.raises(IdTokenInvalid) as e:
        flow.cognito.verify_id_token(unsigned, nonce=None)
    assert e.value.check == "algorithm"


def test_the_signing_keys_are_cached_and_refetched_on_an_unknown_kid(provider):
    flow, cognito, _, _ = setup(provider)
    signed_in(flow, cognito)
    signed_in(flow, cognito)
    assert cognito.jwks_fetches == 1
    cognito.header_overrides = {"kid": "rotated"}
    with pytest.raises(LoginRefused):
        signed_in(flow, cognito)
    assert cognito.jwks_fetches == 2                      # one forced refetch, then refused


@pytest.mark.parametrize("groups,tenant,code", [
    ((), "default", "no_role"),
    (("everyone",), "default", "no_role"),
    (("operator",), None, "no_tenant"),
])
def test_an_account_with_no_role_or_tenant_cannot_sign_in(provider, groups, tenant, code):
    flow, cognito, sessions, audit = setup(provider, FakeCognito(groups=groups, tenant=tenant))
    with pytest.raises(LoginRefused) as e:
        signed_in(flow, cognito)
    assert e.value.code == code and sessions.rows == {} and len(cognito.revoked) == 1
    assert audit.audit[-1]["actor"] == f"user:{ADMIN_SUB}" and audit.audit[-1]["detail"]["email"] == ADMIN_EMAIL


def test_asking_for_more_scopes_would_drop_the_tenant(provider, monkeypatch):
    """The staging failure, pinned: with ``openid email`` Cognito leaves custom
    attributes out of the ID token and every sign-in is refused for no tenant."""
    import integrations.cognito as cognito_module
    flow, cognito, sessions, _ = setup(provider)
    assert signed_in(flow, cognito).session.tenant_id == "default"          # openid alone
    monkeypatch.setattr(cognito_module, "SCOPES", "openid email")
    with pytest.raises(LoginRefused) as e:
        signed_in(flow, cognito)
    assert e.value.code == "no_tenant"


def test_a_refused_exchange_says_why(provider):
    cognito = FakeCognito()
    cognito.exchange_error = "invalid_grant"
    flow, cognito, sessions, audit = setup(provider, cognito)
    with pytest.raises(LoginRefused, match="invalid_grant"):
        signed_in(flow, cognito)
    assert audit.audit[-1]["detail"]["reason"] == "exchange: invalid_grant"


def test_the_highest_group_is_the_role(provider):
    flow, cognito, _, _ = setup(provider, FakeCognito(groups=("viewer", "operator")))
    assert signed_in(flow, cognito).session.role == "operator"


@pytest.mark.parametrize("given,expected", [
    ("/integrations", "/integrations"), ("/customer/C-1?tab=plays", "/customer/C-1?tab=plays"),
    (None, "/"), ("", "/"), ("https://evil.example/", "/"), ("//evil.example/x", "/"),
    ("/\\evil.example", "/"), ("integrations", "/"),
])
def test_the_return_path_never_leaves_the_site(given, expected):
    assert safe_return(given) == expected


def test_the_pool_region_comes_from_the_pool_id_not_aws_region(monkeypatch):
    from integrations.cognito import CognitoConfig
    monkeypatch.setenv("AWS_REGION", "us-west-2")                      # the master key's region
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_sFnyqOnla")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "id")
    monkeypatch.setenv("COGNITO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("COGNITO_DOMAIN", "us-east-1sfnyqonla.auth.us-east-1.amazoncognito.com")
    c = CognitoConfig.from_env()
    assert c.region == "us-east-1"
    assert c.issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_sFnyqOnla"
    assert c.domain == "https://us-east-1sfnyqonla.auth.us-east-1.amazoncognito.com"
    monkeypatch.delenv("COGNITO_CLIENT_SECRET")
    assert CognitoConfig.from_env() is None


def test_domain_is_normalised_to_https_host():
    assert normalise_domain("churnguard.auth.us-west-2.amazoncognito.com") == \
        "https://churnguard.auth.us-west-2.amazoncognito.com"
    assert normalise_domain("https://x.auth.us-west-2.amazoncognito.com/") == "https://x.auth.us-west-2.amazoncognito.com"
    with pytest.raises(ValueError):
        normalise_domain("http://x.example")


# ---------------------------------------------------------------- recheck

def test_the_recheck_refreshes_the_groups(provider):
    flow, cognito, sessions, _ = setup(provider, FakeCognito(groups=("admin",)))
    s = signed_in(flow, cognito).session
    cognito.groups = ["viewer"]                            # demoted in the console
    again = flow.recheck(s)
    assert again.role == "viewer" and sessions.rows[s.id].groups == ("viewer",)


def test_a_disabled_person_is_cut_off_at_the_next_action(provider):
    flow, cognito, sessions, audit = setup(provider)
    done = signed_in(flow, cognito)
    cognito.disabled = True
    with pytest.raises(SessionEnded):
        flow.recheck(done.session)
    assert sessions.rows == {} and sessions.get(done.cookie_value) is None
    row = audit.audit[-1]
    assert row["event"] == "session_revoked" and row["detail"]["user"] == f"user:{ADMIN_SUB}"


def test_an_idle_or_expired_session_is_gone(provider):
    flow, cognito, sessions, _ = setup(provider)
    done = signed_in(flow, cognito)
    t0 = done.session.created_at
    assert sessions.get(done.cookie_value, now=t0 + timedelta(minutes=59)) is not None
    assert sessions.get(done.cookie_value, now=t0 + timedelta(minutes=61)) is None       # idle
    done = signed_in(flow, cognito)
    assert sessions.get(done.cookie_value, now=done.session.created_at + timedelta(hours=9)) is None


# ----------------------------------------------------------------- logout

def test_sign_out_revokes_deletes_and_ends_the_cognito_session(provider):
    flow, cognito, sessions, audit = setup(provider)
    done = signed_in(flow, cognito)
    refresh = crypto.unseal(provider, done.session.id, done.session.sealed_refresh,
                            purpose=crypto.SESSION_PURPOSE)
    target = flow.logout(done.session)
    assert cognito.revoked == [refresh] and sessions.rows == {}
    q = {k: v[0] for k, v in parse_qs(urlsplit(target).query).items()}
    assert target.startswith(f"{DOMAIN}/logout?") and q == {"client_id": CLIENT_ID, "logout_uri": f"{SITE}/"}
    assert audit.audit[-1]["event"] == "logout" and audit.audit[-1]["detail"]["revoke_status"] == 200


# ------------------------------------------------------------------- HTTP

class State:
    def __init__(self, flow=None, sessions=None):
        self.login = flow
        self.sessions = sessions
        self.errors = {}
        self.not_applicable = {} if flow else {"login": "no user pool configured"}


def make_app(state) -> TestClient:
    app = FastAPI()
    app.include_router(auth_routes.build_router(lambda: state))
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def test_routes_when_sign_in_is_off():
    client = make_app(State())
    assert client.get("/auth/me").json() == {"enabled": False, "reason": "no user pool configured", "user": None,
                                             "signup": False}
    assert client.get("/auth/login").status_code == 404
    assert client.get("/auth/signup").status_code == 404
    assert client.get("/auth/callback?code=x&state=y").status_code == 404


def test_sign_in_and_out_through_the_routes(provider, monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    flow, cognito, sessions, audit = setup(provider)
    client = make_app(State(flow, sessions))
    monkeypatch.delenv("CONTACT_LINKEDIN", raising=False)
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    assert client.get("/auth/me").json() == {"enabled": True, "reason": None, "user": None, "signup": False,
                                             "contact": {"linkedin": None, "email": None}}
    assert client.get("/auth/signup").status_code == 404                     # sign-up is off here

    r = client.get("/auth/login?return=/integrations")
    assert r.status_code == 302 and r.headers["location"].startswith(f"{DOMAIN}/oauth2/authorize?")
    code, state = cognito.sign_in(r.headers["location"])

    r = client.get(f"/auth/callback?code={code}&state={state}")
    assert r.status_code == 303 and r.headers["location"] == "/integrations"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-cg_session=")
    for attribute in ("HttpOnly", "Secure", "Path=/", "SameSite=lax", "Max-Age=28800"):
        assert attribute.lower() in cookie.lower()
    assert "Domain" not in cookie

    me = client.get("/auth/me").json()
    assert me["user"]["email"] == ADMIN_EMAIL and me["user"]["role"] == "admin"
    assert "rt-" not in str(me) and find_secret(str(me)) is None

    assert client.post("/auth/logout").status_code == 403                       # no Origin
    assert client.post("/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    r = client.post("/auth/logout", headers={"Origin": SITE})
    assert r.status_code == 303 and r.headers["location"].startswith(f"{DOMAIN}/logout?")
    assert 'Max-Age=0' in r.headers["set-cookie"] or "expires=" in r.headers["set-cookie"].lower()
    assert sessions.rows == {} and audit.audit[-1]["event"] == "logout"


def test_a_failed_callback_shows_a_page_and_sets_no_cookie(provider, monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    flow, cognito, sessions, _ = setup(provider)
    client = make_app(State(flow, sessions))
    r = client.get("/auth/callback?code=x&state=never-issued")
    assert r.status_code == 400 and "expired or was already used" in r.text
    assert "set-cookie" not in r.headers
    r = client.get("/auth/callback?error=access_denied&error_description=User+cancelled")
    assert r.status_code == 400 and "access_denied" in r.text


# ------------------------------------------------------------ sign-up (ADR-0017)

class Enroller:
    """TeamAdmin.enroll_demo against the fake Cognito: sets what the admin API would."""

    def __init__(self, cognito, fail=False):
        self.cognito, self.fail, self.calls = cognito, fail, []

    def __call__(self, username, tenant):
        self.calls.append((username, tenant))
        if self.fail:
            raise RuntimeError("AccessDenied")
        self.cognito.groups, self.cognito.tenant = ["viewer", "demo"], tenant


def signup_setup(provider, fail=False, groups=(), tenant=None):
    cognito = FakeCognito(groups=groups, tenant=tenant)
    audit_store = FakeStore()
    sessions = FakeSessionStore(audit_store.db)
    enroll = Enroller(cognito, fail)
    flow = LoginFlow(sessions, provider, cognito.client(), site_origin=SITE, enroll=enroll, signup_tenant="default")
    return flow, cognito, sessions, audit_store, enroll


def test_the_signup_url_is_cognitos_signup_page_with_the_same_guards(provider):
    flow, *_ = signup_setup(provider)
    url = flow.start("/", signup=True)
    assert url.startswith(f"{DOMAIN}/signup?")
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert {"state", "nonce", "code_challenge"} <= set(q) and q["code_challenge_method"] == "S256"
    off, *_ = setup(provider)
    assert off.start("/", signup=True).startswith(f"{DOMAIN}/oauth2/authorize?")   # off: never the sign-up page


def test_a_new_signup_becomes_a_demo_viewer_set_by_the_backend(provider):
    flow, cognito, sessions, audit, enroll = signup_setup(provider)
    done = signed_in(flow, cognito, "/")
    s = done.session
    assert enroll.calls == [(ADMIN_SUB, "default")]
    assert (s.role, s.demo, s.tenant_id) == ("viewer", True, "default")
    assert [a["event"] for a in audit.audit[-2:]] == ["signup", "login"]
    assert audit.audit[-2]["detail"] == {"email": ADMIN_EMAIL, "role": "viewer", "demo": True}
    assert s.public()["demo"] is True


@pytest.mark.parametrize("groups,tenant", [((), "acme"), (("operator",), None), (("viewer",), "default")])
def test_only_an_account_with_neither_tenant_nor_role_is_enrolled(provider, groups, tenant):
    flow, cognito, sessions, audit, enroll = signup_setup(provider, groups=groups, tenant=tenant)
    try:
        signed_in(flow, cognito)
    except LoginRefused:
        pass
    assert enroll.calls == []                                                   # never touched


def test_a_failed_enrolment_creates_nothing_and_says_so(provider):
    flow, cognito, sessions, audit, enroll = signup_setup(provider, fail=True)
    with pytest.raises(LoginRefused) as e:
        signed_in(flow, cognito)
    assert e.value.code == "signup" and sessions.rows == {} and len(cognito.revoked) == 1
    assert audit.audit[-1]["event"] == "login_refused" and "AccessDenied" not in str(e.value.reason)


def test_signup_through_the_routes(provider, monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    monkeypatch.setenv("CONTACT_LINKEDIN", "https://www.linkedin.com/in/someone")
    monkeypatch.setenv("CONTACT_EMAIL", "someone@example.test")
    flow, cognito, sessions, audit, enroll = signup_setup(provider)
    client = make_app(State(flow, sessions))
    me = client.get("/auth/me").json()
    assert me["signup"] is True and me["contact"]["email"] == "someone@example.test"
    r = client.get("/auth/signup?return=/")
    assert r.status_code == 302 and r.headers["location"].startswith(f"{DOMAIN}/signup?")
    code, state = cognito.sign_in(r.headers["location"])
    assert client.get(f"/auth/callback?code={code}&state={state}").status_code == 303
    user = client.get("/auth/me").json()["user"]
    assert user["role"] == "viewer" and user["demo"] is True
