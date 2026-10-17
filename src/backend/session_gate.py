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

One transitional allowance, for one step: a request to ``/connections`` carrying
a valid operator key passes without a session. It is how the daily sync workflow
reaches ``/connections/sync-due`` today; 2.6.3 replaces it with GitHub's identity
token and deletes the key.
"""

from __future__ import annotations

from typing import Callable

from fastapi.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth_routes import COOKIE
from connections_routes import operator_key_valid

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
            request.state.session = session
            return await call_next(request)

        if request.url.path.startswith("/connections") and operator_key_valid(request):
            request.state.machine = "operator"
            return await call_next(request)

        return JSONResponse({"detail": "Sign in required.", "sign_in": "/api/auth/login"},
                            status_code=401)
