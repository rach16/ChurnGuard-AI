"""
The MCP server and its access tokens (4.6.5, ADR-0016 sections 10 and 10a).

Through the real routes, the lock and the MCP SDK's streamable HTTP transport,
with the token rows and Cognito's admin API in memory. What these pin: a
signed-in person makes a token, sees its value once, and only its prefix after;
the rules (a name, 30 or 90 days, five live) hold; only the owner revokes it;
``/mcp`` takes a token and nothing else, a session cookie included; the six
tools are listed read-only with the schemas the agents use; a call answers from
the served book in the token's tenant and never another's; a revoked, expired or
removed person's token stops working; each call is audited with the tool and
never its arguments; a token never reaches a log.
"""

from __future__ import annotations

import hashlib
import secrets
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Route

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

import auth_routes  # noqa: E402
import token_routes  # noqa: E402
from assistant.tools import NAMES, ToolContext  # noqa: E402
from integrations import crypto  # noqa: E402
from integrations.access_tokens import AccessToken, TokenAuth, display_prefix, hash_token  # noqa: E402
from integrations.redaction import redact  # noqa: E402
from integrations.team import TeamAdmin  # noqa: E402
from mcp_server import McpEndpoint  # noqa: E402
from session_gate import SessionGate  # noqa: E402
from tests.cognito_fakes import ADMIN_EMAIL, ADMIN_SUB, SITE  # noqa: E402
from tests.salesforce_fakes import FakeStore  # noqa: E402
from tests.signed_in import ORIGIN, enable_sign_in, sign_in  # noqa: E402
from tests.test_assistant_tools import builder  # noqa: E402
from tests.test_team import POOL, FakeIdp  # noqa: E402

SALESFORCE = "data/salesforce"
RPC = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class FakeTokenStore:
    """In memory, the same methods as TokenStore; audit rows land in ``db``'s store."""

    def __init__(self, db):
        self.db = db
        self.rows: dict[str, AccessToken] = {}
        self.hashes: dict[bytes, str] = {}

    def create(self, conn, *, value, name, tenant_id, user_sub, email, days, now):
        t = AccessToken(id=secrets.token_hex(8), prefix=display_prefix(value), name=name, tenant_id=tenant_id,
                        user_sub=user_sub, email=email, created_at=now, expires_at=now + timedelta(days=days),
                        last_used_at=None, checked_at=now, revoked_at=None, revoked_reason=None)
        self.rows[t.id] = t
        self.hashes[hash_token(value)] = t.id
        return t

    def live_for(self, conn, user_sub, now):
        return [t for t in self.rows.values() if t.user_sub == user_sub and t.live(now)]

    def by_value(self, value):
        tid = self.hashes.get(hash_token(value))
        return self.rows.get(tid) if tid else None

    def revoke(self, conn, token_id, *, user_sub, reason, now):
        t = self.rows.get(token_id)
        if t is None or t.user_sub != user_sub or t.revoked_at is not None:
            return None
        self.rows[token_id] = replace(t, revoked_at=now, revoked_reason=reason)
        return self.rows[token_id]

    def revoke_for_user(self, conn, user_sub, *, reason, now):
        doomed = [t for t in self.rows.values() if t.user_sub == user_sub and t.revoked_at is None]
        for t in doomed:
            self.rows[t.id] = replace(t, revoked_at=now, revoked_reason=reason)
        return len(doomed)

    def used(self, conn, token_id, *, now, confirmed):
        t = self.rows[token_id]
        self.rows[token_id] = replace(t, last_used_at=now, checked_at=now if confirmed else t.checked_at)


class State:
    def __init__(self):
        self.errors, self.not_applicable = {}, {}
        self.notes_index = None


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def site(monkeypatch):
    """A signed-in viewer, the token routes, the MCP endpoint; and a second
    person in another tenant with their own token."""
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    provider = crypto.LocalKeyProvider({"v1": secrets.token_bytes(32)}, current="v1")
    store = FakeStore()
    idp = FakeIdp()
    idp.add(ADMIN_SUB, ADMIN_EMAIL, "default", "viewer")
    idp.add("sub-acme", "boss@acme.example", "acme", "admin")
    state = State()
    cognito = enable_sign_in(state, provider, store.db, groups=("viewer",))
    state.team = TeamAdmin(POOL, client=idp)
    clock = Clock()
    state.tokens = TokenAuth(FakeTokenStore(store.db), state.team, clock=clock)
    book = builder(SALESFORCE)

    def context_for(tenant: str) -> ToolContext:
        return ToolContext(tenant=tenant, served_tenant="default", builder=book, snapshot_id="snap-1",
                           as_of=book.scorer.as_of.date().isoformat())

    mcp = McpEndpoint(lambda: state, context_for)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.running():
            yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionGate, get_state=lambda: state)
    app.include_router(token_routes.build_router(lambda: state))
    app.include_router(auth_routes.build_router(lambda: state))
    app.router.routes.append(Route("/mcp", endpoint=mcp, methods=["GET", "POST", "DELETE"]))
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as client:
        sign_in(client, cognito)
        _, other = state.tokens.create(name="acme laptop", days=30, tenant_id="acme", user_sub="sub-acme",
                                       email="boss@acme.example", actor="user:sub-acme")
        yield client, state, store, idp, clock, other


def rpc(client, method, params=None, token=None):
    headers = dict(RPC, **({"Authorization": f"Bearer {token}"} if token else {}))
    return client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                                      "params": params or {}})


def call(client, token, name, arguments=None):
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, token)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def make(client, name="Claude Code", days=90):
    r = client.post("/tokens", headers=ORIGIN, json={"name": name, "days": days})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- the page

def test_a_token_is_shown_once_and_stored_as_a_hash(site):
    client, state, store, *_ = site
    made = make(client)
    value = made["value"]
    assert value.startswith("cg_pat_") and len(value) > 40
    assert made["token"]["prefix"] == value[:11] and made["mcp_url"] == f"{SITE}/api/mcp"
    listed = client.get("/tokens").json()
    assert [t["name"] for t in listed["tokens"]] == ["Claude Code"]
    assert value not in str(listed) and value not in str(state.tokens.store.rows)
    assert hash_token(value) == hashlib.sha256(value.encode()).digest() and hash_token(value) in state.tokens.store.hashes
    row = [a for a in store.audit if a["event"] == "token_created"][-1]
    assert row["actor"] == f"user:{ADMIN_SUB}" and row["detail"]["days"] == 90
    assert value not in str(row)


def test_the_rules_for_making_a_token(site):
    client, *_ = site
    assert client.post("/tokens", headers=ORIGIN, json={"name": "x", "days": 365}).status_code == 409
    assert client.post("/tokens", headers=ORIGIN, json={"name": "   ", "days": 30}).status_code == 409
    assert client.post("/tokens", json={"name": "x", "days": 30}).status_code == 403     # not from the site
    for i in range(5):
        make(client, f"t{i}", 30)
    r = client.post("/tokens", headers=ORIGIN, json={"name": "sixth", "days": 30})
    assert r.status_code == 409 and "5 tokens" in r.json()["detail"]


def test_only_the_owner_revokes_a_token(site):
    client, state, store, idp, clock, other = site
    mine = make(client)
    other_id = state.tokens.store.by_value(other).id
    assert client.post(f"/tokens/{other_id}/revoke", headers=ORIGIN).status_code == 404
    assert client.post(f"/tokens/{mine['token']['id']}/revoke", headers=ORIGIN).status_code == 200
    assert client.get("/tokens").json()["tokens"] == []
    assert rpc(client, "tools/list", token=mine["value"]).status_code == 401
    assert store.audit[-1]["event"] == "token_revoked"


def test_tokens_need_a_signed_in_person(site):
    client, *_ = site
    client.cookies.clear()
    assert client.get("/tokens").status_code == 401


# ----------------------------------------------------------------- the MCP

def test_mcp_takes_a_token_and_nothing_else(site):
    client, *_ = site
    r = rpc(client, "tools/list")                                       # signed-in cookie, no token
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    assert rpc(client, "tools/list", token="cg_pat_" + "x" * 43).status_code == 401
    assert rpc(client, "tools/list", token="not-a-token").status_code == 401
    client.cookies.clear()
    assert rpc(client, "tools/list").status_code == 401


def test_the_six_tools_are_listed_read_only(site):
    client, *_ = site
    token = make(client)["value"]
    r = rpc(client, "tools/list", token=token)
    assert r.status_code == 200, r.text
    tools = r.json()["result"]["tools"]
    assert tuple(t["name"] for t in tools) == NAMES
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in tools)
    assert tools[1]["inputSchema"]["required"] == ["account_id"]


def test_a_call_answers_from_the_book_and_is_audited_without_its_arguments(site):
    client, state, store, *_ = site
    token = make(client)["value"]
    result = call(client, token, "find_accounts", {"name": "Starlight", "limit": 1})
    assert not result["isError"]
    data = result["structuredContent"]
    assert data["ok"] and data["snapshot_id"] == "snap-1"
    assert data["data"]["accounts"][0]["name"] == "Starlight Freight Co"
    row = store.audit[-1]
    assert row["event"] == "mcp_call" and row["actor"] == f"user:{ADMIN_SUB}"
    assert row["detail"]["tool"] == "find_accounts" and row["detail"]["ok"] is True
    assert "Starlight" not in str(row)                                    # no arguments, no result

    bad = call(client, token, "get_story", {"account_id": 999999})
    assert bad["isError"] and "No active account" in bad["structuredContent"]["error"]
    invalid = call(client, token, "get_story", {})
    assert invalid["isError"] and "account_id" in invalid["content"][0]["text"]


def test_another_tenants_token_reads_nothing(site):
    client, state, store, idp, clock, other = site
    result = call(client, other, "get_book_summary")
    assert result["isError"] and result["structuredContent"]["error"] == "No data is served for your organization."


def test_an_expired_token_stops(site):
    client, state, store, idp, clock, other = site
    token = make(client, days=30)["value"]
    clock.now += timedelta(days=31)
    r = rpc(client, "tools/list", token=token)
    assert r.status_code == 401 and "expired" in r.json()["detail"]


def test_a_removed_person_loses_their_tokens(site):
    client, state, store, idp, clock, other = site
    token = make(client)["value"]
    assert rpc(client, "tools/list", token=token).status_code == 200
    idp.users[ADMIN_SUB]["Enabled"] = False                                 # disabled in Cognito
    assert rpc(client, "tools/list", token=token).status_code == 200        # confirmed under two minutes ago
    clock.now += timedelta(minutes=3)
    r = rpc(client, "tools/list", token=token)
    assert r.status_code == 401 and "no longer" in r.json()["detail"]
    assert state.tokens.store.by_value(token).revoked_reason == "its owner is no longer on the team"
    idp.users[ADMIN_SUB]["Enabled"] = True
    assert rpc(client, "tools/list", token=token).status_code == 401        # revoked stays revoked


def test_cognito_unreachable_keeps_the_last_confirmation(site):
    client, state, store, idp, clock, other = site
    token = make(client)["value"]

    def down(**kw):
        raise ConnectionError("cognito down")

    idp.admin_get_user = down
    clock.now += timedelta(minutes=3)
    assert rpc(client, "tools/list", token=token).status_code == 200


def test_a_token_never_reaches_a_log():
    value = "cg_pat_" + secrets.token_urlsafe(32)
    assert value not in redact(f"Authorization: {value}") and "cg_pat_" + "abcd" in redact("prefix cg_pat_abcd")


def test_a_clients_handshake_works(site):
    """What Claude Code sends on connect: initialize, the initialized notice, a
    GET for server events (refused in stateless mode), then tools/list."""
    client, *_ = site
    token = make(client)["value"]
    auth = dict(RPC, Authorization=f"Bearer {token}")
    r = client.post("/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-code", "version": "2"}}})
    assert r.status_code == 200, r.text
    init = r.json()["result"]
    assert init["serverInfo"]["name"] == "churnguard" and "tools" in init["capabilities"]
    assert client.post("/mcp", headers=auth, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    assert client.get("/mcp", headers=auth).status_code == 405                # no idle event stream
    assert len(rpc(client, "tools/list", token=token).json()["result"]["tools"]) == 6


def test_a_demo_account_cannot_make_tokens(site):
    """ADR-0017: MCP is for team members; a demo sign-up reads the site only."""
    client, state, store, *_ = site
    state.login.cognito_fake.groups = ["viewer", "demo"]                  # what the action's re-check reads
    r = client.post("/tokens", headers=ORIGIN, json={"name": "x", "days": 30})
    assert r.status_code == 403 and "team members" in r.json()["detail"]
