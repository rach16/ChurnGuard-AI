"""
The team: who may sign in to a tenant, and with which role (2.6.5, ADR-0013 §3a).

An admin invites, changes roles and removes people from the Team page; this
makes the same Cognito calls an admin used to make by hand in the console, so
the console is needed once, for the first admin, and never again.

Rules it enforces, each with a reason that is safe to show:

- A person belongs to exactly one tenant (``custom:tenant``) and holds exactly
  one role group. An admin sees and acts on their own tenant's people only;
  anyone else is "not on this team".
- The tenant never loses its last admin: nobody can demote or remove them, so
  the team cannot lock itself out.
- An admin cannot remove themselves.
- Removing someone disables the account and signs them out everywhere at once;
  the caller also deletes their sessions here. A removed person invited again is
  re-enabled with the new role (Cognito keeps their password).

The client is boto3's ``cognito-idp`` in the pool's own region, authenticated as
the environment's IAM user, whose policy allows these actions on this pool only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

ROLES = ("admin", "operator", "viewer")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TeamRefused(Exception):
    """A change that would break a rule. The message is safe to show."""


class NotOnTeam(Exception):
    """No such person in this tenant."""


@dataclass(frozen=True)
class Member:
    username: str          # Cognito's username: the user's sub in an email-username pool
    email: str
    role: Optional[str]
    status: str            # invited | active
    enabled: bool
    created_at: Optional[datetime]
    tenant: Optional[str]

    def public(self, last_sign_in: Optional[datetime] = None) -> dict[str, Any]:
        return {"username": self.username, "email": self.email, "role": self.role,
                "status": self.status, "created_at": self.created_at,
                "last_sign_in": last_sign_in}


def _attrs(user: dict[str, Any]) -> dict[str, str]:
    return {a["Name"]: a["Value"] for a in user.get("Attributes") or user.get("UserAttributes") or []}


class TeamAdmin:
    def __init__(self, user_pool_id: str, client=None):
        self.pool = user_pool_id
        self.region = user_pool_id.split("_", 1)[0]
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("cognito-idp", region_name=self.region)
        return self._client

    # ------------------------------------------------------------ reading

    def _member(self, user: dict[str, Any]) -> Member:
        attrs = _attrs(user)
        username = user["Username"]
        groups = [g["GroupName"] for g in self.client.admin_list_groups_for_user(
            UserPoolId=self.pool, Username=username).get("Groups", [])]
        role = next((r for r in ROLES if r in groups), None)
        status = "invited" if user.get("UserStatus") == "FORCE_CHANGE_PASSWORD" else "active"
        return Member(username=username, email=attrs.get("email", ""), role=role, status=status,
                      enabled=bool(user.get("Enabled", True)),
                      created_at=user.get("UserCreateDate"), tenant=attrs.get("custom:tenant"))

    def members(self, tenant: str) -> list[Member]:
        """Everyone enabled in this tenant, admins first, then by email."""
        out: list[Member] = []
        token = None
        while True:
            page = self.client.list_users(UserPoolId=self.pool, **({"PaginationToken": token} if token else {}))
            for user in page.get("Users", []):
                if not user.get("Enabled", True) or _attrs(user).get("custom:tenant") != tenant:
                    continue
                out.append(self._member(user))
            token = page.get("PaginationToken")
            if not token:
                break
        rank = {r: i for i, r in enumerate(ROLES)}
        return sorted(out, key=lambda m: (rank.get(m.role or "", 9), m.email))

    def get(self, tenant: str, username: str) -> Member:
        try:
            user = self.client.admin_get_user(UserPoolId=self.pool, Username=username)
        except self.client.exceptions.UserNotFoundException:
            raise NotOnTeam("That person is not on this team.") from None
        member = self._member(user)
        if member.tenant != tenant or not member.enabled:
            raise NotOnTeam("That person is not on this team.")
        return member

    def _admins(self, tenant: str) -> list[Member]:
        return [m for m in self.members(tenant) if m.role == "admin"]

    # ------------------------------------------------------------ changing

    def invite(self, tenant: str, email: str, role: str) -> tuple[Member, bool]:
        """Create the account, set its role and tenant, and have Cognito email a
        temporary password. Returns the member and whether an earlier removed
        account was re-enabled instead."""
        email = email.strip().lower()
        if not EMAIL.match(email):
            raise TeamRefused("That is not an email address.")
        if role not in ROLES:
            raise TeamRefused(f"Role must be one of {', '.join(ROLES)}.")
        try:
            created = self.client.admin_create_user(
                UserPoolId=self.pool, Username=email, DesiredDeliveryMediums=["EMAIL"],
                UserAttributes=[{"Name": "email", "Value": email},
                                {"Name": "email_verified", "Value": "true"},
                                {"Name": "custom:tenant", "Value": tenant}])["User"]
            username = created["Username"]
            reenabled = False
        except self.client.exceptions.UsernameExistsException:
            existing = self.client.admin_get_user(UserPoolId=self.pool, Username=email)
            attrs = _attrs(existing)
            if attrs.get("custom:tenant") != tenant:
                raise TeamRefused("That email already has an account elsewhere.") from None
            if existing.get("Enabled", True):
                raise TeamRefused("That person is already on the team.") from None
            username = existing["Username"]
            self.client.admin_enable_user(UserPoolId=self.pool, Username=username)
            reenabled = True
        self._set_group(username, role)
        return self.get(tenant, username), reenabled

    def set_role(self, tenant: str, username: str, role: str) -> tuple[Member, Optional[str]]:
        """Returns the member and the role they had before."""
        if role not in ROLES:
            raise TeamRefused(f"Role must be one of {', '.join(ROLES)}.")
        member = self.get(tenant, username)
        if member.role == role:
            return member, member.role
        if member.role == "admin" and len(self._admins(tenant)) <= 1:
            raise TeamRefused("This is the team's only admin. Make someone else an admin first.")
        self._set_group(username, role)
        return self.get(tenant, username), member.role

    def remove(self, tenant: str, username: str, *, by: str) -> Member:
        """Disable the account and sign it out everywhere. ``by`` is the acting
        admin's username, who cannot remove themselves."""
        member = self.get(tenant, username)
        if username == by:
            raise TeamRefused("You cannot remove yourself. Ask another admin.")
        if member.role == "admin" and len(self._admins(tenant)) <= 1:
            raise TeamRefused("This is the team's only admin. Make someone else an admin first.")
        self.client.admin_disable_user(UserPoolId=self.pool, Username=username)
        self.client.admin_user_global_sign_out(UserPoolId=self.pool, Username=username)
        return member

    def _set_group(self, username: str, role: str) -> None:
        current = [g["GroupName"] for g in self.client.admin_list_groups_for_user(
            UserPoolId=self.pool, Username=username).get("Groups", [])]
        self.client.admin_add_user_to_group(UserPoolId=self.pool, Username=username, GroupName=role)
        for other in current:
            if other in ROLES and other != role:
                self.client.admin_remove_user_from_group(UserPoolId=self.pool, Username=username,
                                                         GroupName=other)
