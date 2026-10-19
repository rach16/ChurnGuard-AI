"""
HTTP for the Team page (2.6.5, ADR-0013 §3a). Admins only.

- ``GET  /team``                      the tenant's people, role, status, last sign-in
- ``POST /team/invite``               {email, role}: create, set role and tenant, email
- ``POST /team/{username}/role``      {role}
- ``POST /team/{username}/remove``    disable, sign out everywhere, delete sessions, revoke tokens

Every change is audited with the admin who made it. Rules (last admin, no
self-removal, one tenant) live in ``integrations.team`` and come back as 409
with the reason; someone outside the tenant is 404.
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from access import make_require
from integrations import audit
from integrations.team import NotOnTeam, TeamRefused

logger = logging.getLogger(__name__)


class InviteRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    role: str = Field(..., pattern="^(admin|operator|viewer)$")


class RoleRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|operator|viewer)$")


def build_router(get_state: Callable[[], object]) -> APIRouter:
    router = APIRouter(tags=["team"])
    require = make_require(get_state)

    def team_or_404():
        team = getattr(get_state(), "team", None)
        if team is None:
            raise HTTPException(status_code=404, detail="Team management needs sign-in, which is not enabled here.")
        return team

    def record(request: Request, event: str, actor: str, detail: dict) -> None:
        session = request.state.session
        state = get_state()
        with state.sessions.db.connection() as conn:
            audit.record(conn, event, actor=actor, tenant_id=session.tenant_id, detail=detail)

    def last_sign_ins(tenant: str) -> dict:
        reader = getattr(get_state(), "last_sign_ins", None)
        return reader(tenant) if reader else {}

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotOnTeam as e:
            raise HTTPException(status_code=404, detail=str(e))
        except TeamRefused as e:
            raise HTTPException(status_code=409, detail=str(e))

    @router.get("/team")
    async def list_team(request: Request, actor: str = Depends(require("admin", action=False))):
        team = team_or_404()
        session = request.state.session
        members = await run_in_threadpool(team.members, session.tenant_id)
        seen = await run_in_threadpool(last_sign_ins, session.tenant_id)
        # Demo sign-ups (ADR-0017) are listed apart, so the team stays readable.
        return {"members": [m.public(seen.get(m.username)) for m in members if not m.demo],
                "demo": [m.public(seen.get(m.username)) for m in members if m.demo],
                "you": session.user_sub}

    @router.post("/team/invite")
    async def invite(request: Request, body: InviteRequest, actor: str = Depends(require("admin"))):
        team = team_or_404()
        session = request.state.session
        member, reenabled = await run_in_threadpool(guard, team.invite, session.tenant_id, body.email, body.role)
        await run_in_threadpool(record, request, "member_invited", actor,
                                {"email": member.email, "role": member.role, "reenabled": reenabled})
        logger.info(f"team: {member.email} invited as {member.role} by {session.email}")
        return {"member": member.public(), "reenabled": reenabled}

    @router.post("/team/{username}/role")
    async def change_role(request: Request, username: str, body: RoleRequest,
                          actor: str = Depends(require("admin"))):
        team = team_or_404()
        session = request.state.session
        member, before = await run_in_threadpool(guard, team.set_role, session.tenant_id, username, body.role)
        if before != member.role:
            await run_in_threadpool(record, request, "member_role_changed", actor,
                                    {"email": member.email, "from": before, "to": member.role})
        return {"member": member.public()}

    @router.post("/team/{username}/remove")
    async def remove(request: Request, username: str, actor: str = Depends(require("admin"))):
        team = team_or_404()
        session = request.state.session
        member = await run_in_threadpool(guard, team.remove, session.tenant_id, username, by=session.user_sub)

        def end_sessions():
            state = get_state()
            tokens = getattr(state, "tokens", None)
            with state.sessions.db.connection() as conn:
                ended = state.sessions.delete_for_user(conn, member.username)
                revoked = (tokens.revoke_for_user(conn, member.username, tenant_id=session.tenant_id, actor=actor,
                                                  reason="removed from the team") if tokens is not None else 0)
            return ended, revoked

        ended, revoked = await run_in_threadpool(end_sessions)
        await run_in_threadpool(record, request, "member_removed", actor,
                                {"email": member.email, "role": member.role, "sessions_ended": ended,
                                 "tokens_revoked": revoked})
        return {"removed": member.email, "sessions_ended": ended, "tokens_revoked": revoked}

    return router
