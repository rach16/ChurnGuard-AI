"""
HTTP for the Access tokens page (4.6.5, ADR-0016 §10a). Any signed-in role.

- ``GET  /tokens``               your live tokens, and the MCP address to use them at
- ``POST /tokens``               {name, days}: a new token, its value shown this once
- ``POST /tokens/{id}/revoke``   your own token; anyone else's is 404

Creating and revoking are actions (site origin, Cognito re-check). Rules (a name,
30 or 90 days, at most five live) live in ``integrations.access_tokens`` and come
back as 409 with the reason.
"""

from __future__ import annotations

import os
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from access import make_require
from integrations.access_tokens import LIFETIME_DAYS, MAX_LIVE_PER_PERSON, TokenRefused


class CreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    days: int = Field(90)


def build_router(get_state: Callable[[], object]) -> APIRouter:
    router = APIRouter(tags=["tokens"])
    require = make_require(get_state)

    def tokens_or_404():
        tokens = getattr(get_state(), "tokens", None)
        if tokens is None:
            raise HTTPException(status_code=404, detail="Access tokens need sign-in and a database, "
                                                        "which are not enabled here.")
        return tokens

    def mcp_url() -> str:
        return f"{os.getenv('PUBLIC_SITE_ORIGIN', '').rstrip('/')}/api/mcp"

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except TokenRefused as e:
            raise HTTPException(status_code=409, detail=str(e))

    @router.get("/tokens")
    async def list_tokens(request: Request, actor: str = Depends(require("viewer", action=False))):
        tokens = tokens_or_404()
        session = request.state.session
        rows = await run_in_threadpool(tokens.list, session.user_sub)
        return {"tokens": [t.public() for t in rows], "mcp_url": mcp_url(),
                "lifetimes": list(LIFETIME_DAYS), "max_live": MAX_LIVE_PER_PERSON}

    @router.post("/tokens")
    async def create_token(request: Request, body: CreateRequest, actor: str = Depends(require("viewer"))):
        tokens = tokens_or_404()
        session = request.state.session
        if session.demo:
            raise HTTPException(status_code=403, detail="Access tokens are for team members, not demo accounts.")
        token, value = await run_in_threadpool(guard, tokens.create, name=body.name, days=body.days,
                                               tenant_id=session.tenant_id, user_sub=session.user_sub,
                                               email=session.email, actor=actor)
        return {"token": token.public(), "value": value, "mcp_url": mcp_url()}

    @router.post("/tokens/{token_id}/revoke")
    async def revoke_token(request: Request, token_id: str, actor: str = Depends(require("viewer"))):
        tokens = tokens_or_404()
        session = request.state.session
        try:
            token = await run_in_threadpool(tokens.revoke, token_id, user_sub=session.user_sub,
                                            tenant_id=session.tenant_id, actor=actor)
        except TokenRefused as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"revoked": token.id}

    return router
