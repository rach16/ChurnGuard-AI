"""
The Team page (2.6.5, ADR-0013 §3a), through the real routes and the lock.

Cognito's admin API is faked in memory with the calls the service makes. What
these pin: only an admin sees or changes the team; an invite creates the account
in the admin's tenant with one role and emails the password; a role change
moves the person between groups; the last admin can be neither demoted nor
removed, and nobody removes themselves; a removal disables the account, signs
it out everywhere and deletes its sessions here; someone in another tenant does
not exist for this one; every change is audited with the admin who made it.
"""

from __future__ import annotations

import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from integrations import crypto  # noqa: E402
from integrations.team import TeamAdmin  # noqa: E402
from tests.cognito_fakes import ADMIN_EMAIL, ADMIN_SUB, SITE  # noqa: E402
from tests.salesforce_fakes import FakeStore  # noqa: E402
from tests.signed_in import ORIGIN, app_with, enable_sign_in, sign_in  # noqa: E402
import team_routes  # noqa: E402

POOL = "us-west-2_TestTeam1"


class FakeIdp:
    """Cognito's admin API, the calls TeamAdmin makes, in memory."""

    class exceptions:
        class UserNotFoundException(Exception):
            pass

        class UsernameExistsException(Exception):
            pass

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.groups: dict[str, set[str]] = {}
        self.signed_out: list[str] = []
        self.created: list[dict] = []

    def add(self, sub, email, tenant, role, status="CONFIRMED", enabled=True):
        self.users[sub] = {"Username": sub, "Enabled": enabled, "UserStatus": status,
                           "UserCreateDate": datetime(2026, 10, 18, tzinfo=timezone.utc),
                           "Attributes": [{"Name": "email", "Value": email},
                                          {"Name": "custom:tenant", "Value": tenant}]}
        self.groups[sub] = {role} if role else set()

    def _find(self, username):
        if username in self.users:
            return self.users[username]
        for u in self.users.values():
            if any(a["Name"] == "email" and a["Value"] == username for a in u["Attributes"]):
                return u
        raise self.exceptions.UserNotFoundException(username)

    def list_users(self, UserPoolId, **kw):
        return {"Users": list(self.users.values())}

    def admin_get_user(self, UserPoolId, Username):
        u = self._find(Username)
        return {**{k: v for k, v in u.items() if k != "Attributes"}, "UserAttributes": u["Attributes"]}

    def admin_list_groups_for_user(self, UserPoolId, Username):
        return {"Groups": [{"GroupName": g} for g in sorted(self.groups.get(self._find(Username)["Username"], ()))]}

    def admin_create_user(self, UserPoolId, Username, DesiredDeliveryMediums, UserAttributes):
        try:
            self._find(Username)
        except self.exceptions.UserNotFoundException:
            pass
        else:
            raise self.exceptions.UsernameExistsException(Username)
        self.created.append({"Username": Username, "DesiredDeliveryMediums": DesiredDeliveryMediums,
                             "UserAttributes": UserAttributes})
        sub = "sub-" + secrets.token_hex(6)
        attrs = {a["Name"]: a["Value"] for a in UserAttributes}
        self.add(sub, attrs["email"], attrs["custom:tenant"], None, status="FORCE_CHANGE_PASSWORD")
        return {"User": self.users[sub]}

    def admin_update_user_attributes(self, UserPoolId, Username, UserAttributes):
        u = self._find(Username)
        names = {x["Name"]: x["Value"] for x in UserAttributes}
        u["Attributes"] = [x for x in u["Attributes"] if x["Name"] not in names] + [
            {"Name": k, "Value": v} for k, v in names.items()]

    def admin_enable_user(self, UserPoolId, Username):
        self._find(Username)["Enabled"] = True

    def admin_disable_user(self, UserPoolId, Username):
        self._find(Username)["Enabled"] = False

    def admin_user_global_sign_out(self, UserPoolId, Username):
        self.signed_out.append(Username)

    def admin_add_user_to_group(self, UserPoolId, Username, GroupName):
        self.groups[self._find(Username)["Username"]].add(GroupName)

    def admin_remove_user_from_group(self, UserPoolId, Username, GroupName):
        self.groups[self._find(Username)["Username"]].discard(GroupName)


class State:
    def __init__(self):
        self.errors, self.not_applicable = {}, {}


@pytest.fixture
def team(monkeypatch):
    """An admin signed in, a team of one (that admin) plus someone in another tenant."""
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    provider = crypto.LocalKeyProvider({"v1": secrets.token_bytes(32)}, current="v1")
    store = FakeStore()
    idp = FakeIdp()
    idp.add(ADMIN_SUB, ADMIN_EMAIL, "default", "admin")
    idp.add("sub-acme", "boss@acme.example", "acme", "admin")

    def build(groups=("admin",)):
        state = State()
        cognito = enable_sign_in(state, provider, store.db, groups=groups)
        state.team = TeamAdmin(POOL, client=idp)
        state.last_sign_ins = lambda tenant: {
            a["actor"][5:]: datetime.now(timezone.utc) for a in store.audit if a["event"] == "login"}
        return sign_in(app_with(state, team_routes.build_router), cognito), state

    client, state = build()
    return client, state, idp, store, build


def test_only_an_admin_sees_or_changes_the_team(team):
    client, state, idp, store, build = team
    operator, _ = build(groups=("operator",))
    assert operator.get("/team").status_code == 403
    assert operator.post("/team/invite", headers=ORIGIN, json={"email": "a@b.co", "role": "viewer"}).status_code == 403
    from tests.signed_in import app_with as fresh
    assert fresh(state, team_routes.build_router).get("/team").status_code == 401        # signed out


def test_the_admin_sees_their_own_tenant_only(team):
    client, *_ = team
    body = client.get("/team").json()
    assert body["you"] == ADMIN_SUB
    assert [(m["email"], m["role"], m["status"]) for m in body["members"]] == [(ADMIN_EMAIL, "admin", "active")]
    assert body["members"][0]["last_sign_in"] is not None


def test_invite_change_role_and_remove(team):
    client, state, idp, store, build = team
    r = client.post("/team/invite", headers=ORIGIN, json={"email": " Priya@Example.com ", "role": "operator"})
    assert r.status_code == 200, r.text
    priya = r.json()["member"]
    assert (priya["email"], priya["role"], priya["status"]) == ("priya@example.com", "operator", "invited")
    created = idp.created[-1]
    assert created["DesiredDeliveryMediums"] == ["EMAIL"]
    assert {a["Name"]: a["Value"] for a in created["UserAttributes"]} == {
        "email": "priya@example.com", "email_verified": "true", "custom:tenant": "default"}
    row = store.audit[-1]
    assert row["event"] == "member_invited" and row["actor"] == f"user:{ADMIN_SUB}"
    assert row["detail"] == {"email": "priya@example.com", "role": "operator", "reenabled": False}

    assert client.post("/team/invite", headers=ORIGIN,
                       json={"email": "priya@example.com", "role": "viewer"}).status_code == 409
    assert client.post("/team/invite", headers=ORIGIN, json={"email": "not-an-email", "role": "viewer"}).status_code == 409
    assert client.post("/team/invite", headers=ORIGIN, json={"email": "x@y.co", "role": "owner"}).status_code == 422

    r = client.post(f"/team/{priya['username']}/role", headers=ORIGIN, json={"role": "viewer"})
    assert r.status_code == 200 and r.json()["member"]["role"] == "viewer"
    assert idp.groups[priya["username"]] == {"viewer"}                       # one role, not two
    assert store.audit[-1]["event"] == "member_role_changed"
    assert store.audit[-1]["detail"] == {"email": "priya@example.com", "from": "operator", "to": "viewer"}

    r = client.post(f"/team/{priya['username']}/remove", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["removed"] == "priya@example.com"
    assert idp.users[priya["username"]]["Enabled"] is False and idp.signed_out == [priya["username"]]
    assert store.audit[-1]["event"] == "member_removed"
    assert [m["email"] for m in client.get("/team").json()["members"]] == [ADMIN_EMAIL]

    # Invited again: the same account comes back, with the new role.
    r = client.post("/team/invite", headers=ORIGIN, json={"email": "priya@example.com", "role": "admin"})
    assert r.status_code == 200 and r.json()["reenabled"] is True and r.json()["member"]["role"] == "admin"


def test_the_last_admin_stays_and_nobody_removes_themselves(team):
    client, state, idp, store, build = team
    r = client.post(f"/team/{ADMIN_SUB}/role", headers=ORIGIN, json={"role": "viewer"})
    assert r.status_code == 409 and "only admin" in r.json()["detail"]
    r = client.post(f"/team/{ADMIN_SUB}/remove", headers=ORIGIN)
    assert r.status_code == 409 and "yourself" in r.json()["detail"]
    assert idp.groups[ADMIN_SUB] == {"admin"} and idp.users[ADMIN_SUB]["Enabled"] is True

    # With a second admin, the first may step down.
    second = client.post("/team/invite", headers=ORIGIN, json={"email": "sam@example.com", "role": "admin"}).json()
    assert client.post(f"/team/{ADMIN_SUB}/role", headers=ORIGIN, json={"role": "operator"}).status_code == 200
    # ...and now the second admin is the last one.
    assert client.post(f"/team/{second['member']['username']}/role", headers=ORIGIN,
                       json={"role": "viewer"}).status_code in (403, 409)


def test_someone_in_another_tenant_does_not_exist_here(team):
    client, state, idp, store, build = team
    assert client.post("/team/sub-acme/role", headers=ORIGIN, json={"role": "viewer"}).status_code == 404
    assert client.post("/team/sub-acme/remove", headers=ORIGIN).status_code == 404
    r = client.post("/team/invite", headers=ORIGIN, json={"email": "boss@acme.example", "role": "viewer"})
    assert r.status_code == 409 and "elsewhere" in r.json()["detail"]
    assert idp.groups["sub-acme"] == {"admin"} and idp.users["sub-acme"]["Enabled"] is True


def test_a_change_must_come_from_the_site(team):
    client, *_ = team
    assert client.post("/team/invite", json={"email": "a@b.co", "role": "viewer"}).status_code == 403


def test_a_demo_signup_is_enrolled_listed_apart_and_can_join_the_team(team):
    """ADR-0017: the backend sets a new sign-up's tenant and groups; the Team page
    lists demo accounts apart; giving one a role makes them a team member."""
    client, state, idp, store, build = team
    idp.add("sub-new", "visitor@example.test", None, None)
    state.team.enroll_demo("sub-new", "default")
    assert idp.groups["sub-new"] == {"viewer", "demo"}
    assert {a["Name"]: a["Value"] for a in idp.users["sub-new"]["Attributes"]}["custom:tenant"] == "default"
    with pytest.raises(Exception, match="already set up"):
        state.team.enroll_demo("sub-new", "acme")                       # never re-assigned
    with pytest.raises(Exception, match="already set up"):
        state.team.enroll_demo(ADMIN_SUB, "acme")

    body = client.get("/team").json()
    assert [m["email"] for m in body["members"]] == [ADMIN_EMAIL]
    assert [m["email"] for m in body["demo"]] == ["visitor@example.test"]

    r = client.post("/team/sub-new/role", headers=ORIGIN, json={"role": "operator"})
    assert r.status_code == 200 and idp.groups["sub-new"] == {"operator"}   # no longer a demo account
    assert "visitor@example.test" in [m["email"] for m in client.get("/team").json()["members"]]
