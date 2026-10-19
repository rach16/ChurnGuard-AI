"""
The lock on every route (2.6.2, ADR-0013 section 3).

Once a user pool is configured, every request needs a signed-in session except
the few that cannot have one: the probes an orchestrator calls (ADR-0010), the
sign-in routes themselves, and Salesforce's redirect back from a connect, which
is authenticated by its single-use state. Anything else without a session gets
**401**, whatever the frontend shows: the backend's own address is reachable
directly, so the redirect to a sign-in page is convenience and this is the control.

A deployment with no user pool (production until 2.6.4) is not locked and says
so on ``/health``; that is ADR-0013's stand-in, not a silent gap.

One machine caller passes without a session: the daily sync workflow, on
``POST /connections/sync-due`` only, carrying a bearer token. The route itself
verifies it as GitHub's OIDC token for this repository's workflow on main
(2.6.3, ADR-0013 §5) and refuses anything else, so the check lives in one place.
The shared operator key this replaced is gone.

MCP clients (4.6.5, ADR-0016 §10a) pass without a session on ``/mcp`` only,
carrying a personal access token; the endpoint verifies it, and refuses a
session cookie, so here too the check lives in one place.
"""

from __future__ import annotations

from typing import Callable

from fastapi.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth_routes import COOKIE
from connections_routes import bearer_token

# The scheduler's one route, reachable with a bearer token instead of a session.
SCHEDULER_PATH = "/connections/sync-due"
# MCP clients' one route, reachable with an access token instead of a session.
MCP_PATH = "/mcp"

OPEN_PATHS = frozenset({
    # Probes and documentation (ADR-0010): an orchestrator cannot sign in.
    "/", "/health", "/ready", "/docs", "/redoc", "/openapi.json", "/favicon.ico",
    # Signing in, which by definition happens without a session.
    "/auth/login", "/auth/callback", "/auth/logout", "/auth/me",
    # Salesforce's redirect after a connect: authenticated by its single-use state.
    "/integrations/salesforce/callback",
})


class SessionGate(BaseHTTPMiddleware):
    def __init__(self, app, get_state: Callable[[], object]):
        super().__init__(app)
        self.get_state = get_state

    async def dispatch(self, request: Request, call_next):
        state = self.get_state()
        login = getattr(state, "login", None)
        if login is None or request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
            return await call_next(request)

        cookie = request.cookies.get(COOKIE)
        session = await run_in_threadpool(state.sessions.get, cookie) if cookie else None
        if session is not None:
            # The role as Cognito last confirmed it, re-asked every couple of
            # minutes (2.6.6): a role changed on the Team page reaches the
            # person at their next page load, and a disabled account loses its
            # reads too. If Cognito cannot be asked, the last confirmed role
            # stands for reads; actions re-check for themselves and refuse.
            session = await run_in_threadpool(login.fresh, session)
        if session is not None:
            request.state.session = session
            return await call_next(request)

        if (request.url.path == SCHEDULER_PATH and request.method == "POST"
                and bearer_token(request) is not None):
            return await call_next(request)
        if request.url.path == MCP_PATH:
            return await call_next(request)

        return JSONResponse({"detail": "Sign in required.", "sign_in": "/api/auth/login"},
                            status_code=401)
