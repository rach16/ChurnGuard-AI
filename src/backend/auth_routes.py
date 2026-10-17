"""
HTTP for operator sign-in (2.6, ADR-0013).

Four routes, all reached through the site's own ``/api`` rewrite, so the cookie
the backend sets belongs to the site's origin:

- ``GET  /auth/login?return=/path``   start: redirect to Cognito's managed login.
- ``GET  /auth/callback``             Cognito's redirect back. Creates the session,
                                      sets the cookie, returns to the page.
- ``POST /auth/logout``               revoke, delete, clear the cookie, and end the
                                      Cognito session too.
- ``GET  /auth/me``                   who is signed in, for the header. Never a token.

A deployment with no user pool configured answers ``/auth/me`` with
``enabled: false`` and 404 on the rest: sign-in is off there, and says so.
"""

from __future__ import annotations

import html
import logging
import os
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse

from integrations.login import LoginFlow, LoginRefused
from integrations.sessions import ABSOLUTE_LIFETIME, Session

logger = logging.getLogger(__name__)

COOKIE = "__Host-cg_session"


def site_origin() -> str:
    return os.getenv("PUBLIC_SITE_ORIGIN", "").rstrip("/")


def set_session_cookie(response, value: str) -> None:
    response.set_cookie(COOKIE, value, max_age=int(ABSOLUTE_LIFETIME.total_seconds()),
                        path="/", secure=True, httponly=True, samesite="lax")


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")


def same_origin(request: Request) -> bool:
    """A state-changing request must come from the site's own pages (ADR-0013 §4).
    Browsers send Origin on every POST; its absence is treated as a refusal."""
    origin = request.headers.get("origin", "").rstrip("/")
    return bool(origin) and origin == site_origin()


def _page(title: str, message: str, status: int) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:15px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;height:100vh;
color:#111;background:#fff}}main{{max-width:28rem;padding:2rem;text-align:center}}
h1{{font-size:1.1rem;margin:0 0 .5rem}}p{{color:#555;margin:0}}a{{color:#111}}</style></head>
<body><main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>
<p style="margin-top:1rem"><a href="/">Back to ChurnGuard</a></p></main></body></html>""",
                        status_code=status)


def build_router(get_state: Callable[[], object]) -> APIRouter:
    router = APIRouter(tags=["auth"])

    def flow_or_404() -> LoginFlow:
        flow = getattr(get_state(), "login", None)
        if flow is None:
            raise HTTPException(status_code=404, detail="Sign-in is not enabled on this deployment.")
        return flow

    def current(request: Request) -> Optional[Session]:
        store = getattr(get_state(), "sessions", None)
        return store.get(request.cookies.get(COOKIE)) if store is not None else None

    @router.get("/auth/me")
    async def me(request: Request):
        state = get_state()
        if getattr(state, "login", None) is None:
            reason = (getattr(state, "not_applicable", {}) or {}).get("login") \
                or (getattr(state, "errors", {}) or {}).get("login") or "sign-in not initialised"
            return {"enabled": False, "reason": reason, "user": None}
        session = await run_in_threadpool(current, request)
        return {"enabled": True, "reason": None, "user": session.public() if session else None}

    @router.get("/auth/login")
    async def login(request: Request):
        flow = flow_or_404()
        url = await run_in_threadpool(flow.start, request.query_params.get("return"))
        return RedirectResponse(url, status_code=302)

    @router.get("/auth/callback")
    async def callback(code: str = "", state: str = "", error: str = "",
                       error_description: str = ""):
        flow = flow_or_404()
        if error:
            logger.warning(f"Cognito returned error={error}")
            return _page("Not signed in", f"Cognito reported: {error}. {error_description}"[:300], 400)
        try:
            done = await run_in_threadpool(flow.complete, code, state)
        except LoginRefused as e:
            return _page("Not signed in", e.reason, 400)
        except Exception as e:  # noqa: BLE001
            logger.error(f"sign-in callback failed: {type(e).__name__}: {e}", exc_info=True)
            return _page("Not signed in", f"Something failed on our side ({type(e).__name__}). "
                                          "Nothing was stored.", 500)
        response = RedirectResponse(done.return_to, status_code=303)
        set_session_cookie(response, done.cookie_value)
        return response

    @router.post("/auth/logout")
    async def logout(request: Request):
        flow = flow_or_404()
        if not same_origin(request):
            raise HTTPException(status_code=403, detail="Sign-out must come from this site.")
        session = await run_in_threadpool(current, request)
        target = await run_in_threadpool(flow.logout, session)
        response = RedirectResponse(target, status_code=303)
        clear_session_cookie(response)
        return response

    return router
