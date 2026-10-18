"""
HTTP for Log in with Salesforce (1.9, ADR-0012 section 1).

Five routes, three audiences:

- ``GET  /connections``                          anyone: is a connection present, which
                                                  org, which host, what state. Never the
                                                  operator's username or a token.
- ``POST /connections/start``                    operator: begin the flow, get the URL
                                                  for the popup.
- ``POST /connections/{id}/verify``              operator: refresh and ask the org who
                                                  it is. Proves the token, resets the
                                                  org's 30-day idle timer.
- ``POST /connections/{id}/disconnect``          operator: revoke, delete, audit.
- ``POST /connections/{id}/sync``                operator: start a sync run (1.10), or
                                                  join the one running. Answers 202
                                                  with the run; the outcome is polled.
- ``GET  /connections/{id}/runs``                anyone: recent runs, counts and
                                                  timings. No record data.
- ``GET  /connections/{id}/metrics``             anyone: the last 30 days of runs in
                                                  numbers.
- ``POST /connections/{id}/rollback``            operator: serve an earlier snapshot
                                                  (the previous one by default).
- ``POST /connections/sync-due``                 operator, the schedule: a run for every
                                                  connection whose last success is older
                                                  than the interval; ``?wait=true`` reports
                                                  the outcomes.
- ``GET  /integrations/salesforce/callback``     Salesforce's redirect, carrying the
                                                  one-time code and the state. Public by
                                                  necessity and authenticated by the
                                                  state, which only a flow this service
                                                  started can present. Answers with a
                                                  small page that tells the opener the
                                                  result and closes.

Who may act (2.6, ADR-0013): a signed-in person whose role allows it, re-checked
with Cognito at the moment of the action. The one machine caller, the daily sync
workflow, presents GitHub's OIDC token for its run, accepted on ``sync-due`` only.
A deployment with no user pool has no actions at all (404): nobody is signed in
there to take one. The shared operator key that stood in for all of this until
2.6 is gone (2.6.3).
"""

from __future__ import annotations

import html
import json
import logging
import os
from typing import Callable, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from auth_routes import COOKIE, same_origin
from integrations.connections import DEFAULT_TENANT
from integrations.flow import CALLBACK_PATH, ConnectFlow, FlowRefused
from integrations.github_oidc import SchedulerRefused
from integrations.login import SessionEnded, actor as actor_of
from integrations.sync import RollbackRefused, SyncEngine, attention
from integrations.tokens import ConnectionGone, NeedsReauth, RefreshFailed, TokenService

logger = logging.getLogger(__name__)

ACTOR = "operator"       # the connect audit where nobody is signed in


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    return header[7:].strip() or None if header[:7].lower() == "bearer " else None


# Who may do what (ADR-0013 section 3). Higher includes lower.
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


def role_rank(role) -> int:
    return ROLE_RANK.get(role or "", 0)


class StartRequest(BaseModel):
    environment: str = Field("production", pattern="^(production|sandbox)$")


class RollbackRequest(BaseModel):
    """Empty means the snapshot served before the current one."""
    snapshot_id: Optional[str] = Field(None, pattern="^[A-Za-z0-9-]{8,64}$")


def _popup_page(site_origin: str, ok: bool, title: str, message: str, event: str) -> str:
    """What the popup shows after the callback, and the one message it sends home.

    No token, code or state appears here. The opener learns only the outcome and
    refetches the connection from the API like any other reader.
    """
    payload = json.dumps({"type": "churnguard:salesforce-connect", "ok": ok, "event": event})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;height:100vh;
color:#111;background:#fff}}main{{max-width:28rem;padding:2rem;text-align:center}}
h1{{font-size:1.1rem;margin:0 0 .5rem}}p{{color:#555;margin:0}}</style></head>
<body><main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>
<p style="margin-top:1rem;font-size:13px">You can close this window.</p></main>
<script>
try {{ if (window.opener) window.opener.postMessage({payload}, {json.dumps(site_origin)}); }} catch (e) {{}}
setTimeout(function () {{ window.close(); }}, {1500 if ok else 6000});
</script></body></html>"""


def build_router(get_state: Callable[[], object]) -> APIRouter:
    router = APIRouter(tags=["connections"])

    def require(min_role: str):
        """The gate on a state-changing route (ADR-0013 section 4).

        Signed in: the request must come from the site's own pages, the person is
        re-checked with Cognito at this moment (a disabled or signed-out account
        is refused here and its session deleted), and their role must be at least
        ``min_role``. A deployment with sign-in off has no actions: 404.
        """
        def dependency(request: Request) -> str:
            state = get_state()
            login = getattr(state, "login", None)
            session = getattr(request.state, "session", None)
            if login is None:
                raise HTTPException(status_code=404,
                                    detail="Actions need sign-in, which is not enabled on this deployment.")
            if session is None:
                raise HTTPException(status_code=401, detail="Sign in required.")
            if not same_origin(request):
                raise HTTPException(status_code=403, detail="This action must come from the site's own pages.")
            try:
                session = login.recheck(session)
            except SessionEnded as e:
                raise HTTPException(status_code=401, detail=str(e))
            if session is None:
                raise HTTPException(status_code=401, detail="Your sign-in ended. Sign in again.")
            if role_rank(session.role) < ROLE_RANK[min_role]:
                raise HTTPException(status_code=403, detail=f"This needs the {min_role} role.")
            request.state.session = session
            return actor_of(session)
        return dependency

    def tenant_of(request: Request) -> str:
        session = getattr(request.state, "session", None)
        return session.tenant_id if session is not None else DEFAULT_TENANT

    def own_connection(request: Request, connection_id: str):
        """The connection, if it belongs to the caller's tenant. Another tenant's
        connection is reported as not found, never as forbidden: its existence is
        not the caller's business."""
        store = getattr(get_state(), "connections", None)
        c = store.get_by_id(connection_id) if store is not None else None
        if c is None or c.tenant_id != tenant_of(request):
            raise HTTPException(status_code=404, detail="This connection no longer exists.")
        return c

    def flow_or_503() -> ConnectFlow:
        state = get_state()
        store = getattr(state, "connections", None)
        provider = getattr(state, "key_provider", None)
        if store is None or provider is None:
            reason = (getattr(state, "errors", {}) or {}).get("connections", "connections not initialised")
            raise HTTPException(status_code=503, detail=f"Connections are unavailable: {reason}")
        missing = [k for k in ("SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET", "PUBLIC_SITE_ORIGIN")
                   if not os.getenv(k, "").strip()]
        if missing:
            raise HTTPException(status_code=503, detail=f"Connections are not configured: {', '.join(missing)} not set")
        return ConnectFlow.from_env(store, provider)

    def tokens_or_503() -> TokenService:
        state = get_state()
        tokens = getattr(state, "tokens", None)
        if tokens is None:
            store = getattr(state, "connections", None)
            reason = ((getattr(state, "errors", {}) or {}).get("connections") if store is None
                      else "SALESFORCE_CLIENT_ID / SALESFORCE_CLIENT_SECRET not set")
            raise HTTPException(status_code=503, detail=f"Connections are unavailable: {reason}")
        return tokens

    def sync_or_503() -> SyncEngine:
        state = get_state()
        engine = getattr(state, "sync", None)
        if engine is None:
            tokens_or_503()           # names the missing piece
            raise HTTPException(status_code=503, detail="Sync is unavailable: engine not initialised")
        return engine

    def site_origin() -> str:
        return os.getenv("PUBLIC_SITE_ORIGIN", "").rstrip("/")

    @router.get("/connections")
    async def get_connection(request: Request):
        """The connection, if any, as any signed-in reader may see it."""
        state = get_state()
        store = getattr(state, "connections", None)
        if store is None:
            off = (getattr(state, "not_applicable", {}) or {}).get("connections")
            err = (getattr(state, "errors", {}) or {}).get("connections")
            return {"enabled": False, "reason": off or err or "connections not initialised",
                    "operator_actions": False, "connection": None}
        connection = await run_in_threadpool(store.get, tenant_of(request))
        public: Optional[dict] = None
        last_run: Optional[dict] = None
        if connection is not None:
            public = connection.public()
            public.pop("username", None)      # the operator's login is not for every reader
        serving: Optional[dict] = None
        runs = getattr(state, "sync_runs", None)
        if runs is not None:
            if connection is not None:
                latest = await run_in_threadpool(runs.last_run, connection.id)
                last_run = latest.public() if latest else None
            pointer = await run_in_threadpool(runs.get_serving)
            if pointer is not None:
                head = await run_in_threadpool(runs.get_snapshot, pointer.snapshot_id, None, with_tables=False)
                serving = {**pointer.public(), "as_of": head.as_of if head else None,
                           "created_at": head.created_at if head else None,
                           "row_counts": head.row_counts if head else None}
        needs = await run_in_threadpool(attention, store, runs) if runs is not None else []
        session = getattr(request.state, "session", None)
        rank = role_rank(session.role) if session is not None else 0
        operate, administer = rank >= ROLE_RANK["operator"], rank >= ROLE_RANK["admin"]
        return {"enabled": True, "reason": None,
                "operator_actions": operate, "admin_actions": administer,
                "role": session.role if session is not None else None,
                "pinned_org_id": os.getenv("SALESFORCE_ORG_ID", "").strip() or None,
                "connection": public, "last_run": last_run, "serving": serving,
                "attention": needs}

    @router.get("/connections/{connection_id}/metrics")
    async def connection_metrics(request: Request, connection_id: str, days: int = 30):
        """Run metrics for the page (ADR-0012 section 8): counts, records, API calls, durations."""
        runs = getattr(get_state(), "sync_runs", None)
        if runs is None:
            return {"enabled": False, "metrics": None}
        await run_in_threadpool(own_connection, request, connection_id)
        metrics = await run_in_threadpool(runs.metrics, connection_id, max(1, min(days, 365)))
        return {"enabled": True, "metrics": metrics}

    @router.get("/connections/{connection_id}/runs")
    async def list_runs(request: Request, connection_id: str, limit: int = 10):
        """Recent sync runs, newest first. Counts and timings only; no record data."""
        runs = getattr(get_state(), "sync_runs", None)
        if runs is None:
            return {"enabled": False, "runs": []}
        try:
            await run_in_threadpool(own_connection, request, connection_id)
        except HTTPException:
            return {"enabled": True, "runs": []}
        rows = await run_in_threadpool(runs.runs, connection_id, max(1, min(limit, 50)))
        return {"enabled": True, "runs": [r.public() for r in rows]}

    require_operator_role = require("operator")

    def scheduler_or_operator(request: Request) -> str:
        """The daily workflow presents GitHub's OIDC token for its run (2.6.3,
        ADR-0013 §5); anyone else is a signed-in operator."""
        token = bearer_token(request)
        if token is None:
            return require_operator_role(request)
        verifier = getattr(get_state(), "scheduler", None)
        if verifier is None:
            raise HTTPException(status_code=404, detail="No scheduler identity is configured on this deployment.")
        try:
            run = verifier.verify(token)
        except SchedulerRefused as e:
            logger.warning(f"sync-due refused: {e.check}")
            raise HTTPException(status_code=401, detail=f"Scheduler token refused ({e.check}).")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=503, detail=f"GitHub's signing keys could not be read ({type(e).__name__}).")
        request.state.scheduler_run = run
        return run.actor

    @router.post("/connections/sync-due")
    async def sync_due(request: Request, wait: bool = False,
                       actor: str = Depends(scheduler_or_operator)):
        """The schedule's entry point (1.11): a run for every connection whose
        last success is older than the interval. ``wait=true`` runs inline and
        reports each outcome, so a scheduler can fail when one does."""
        engine = sync_or_503()
        outcomes = await run_in_threadpool(engine.sync_due, actor=actor, trigger="schedule", wait=wait)
        caller_run = getattr(request.state, "scheduler_run", None)
        worst = "ok"
        for o in outcomes:
            run = o.get("run") or {}
            if o["action"] == "skipped" or run.get("status") in ("failed", "degraded"):
                worst = "attention"
        return {"outcome": worst, "connections": outcomes,
                "caller": {"actor": actor, "run_id": caller_run.run_id if caller_run else None,
                           "event": caller_run.event if caller_run else None}}

    @router.post("/connections/{connection_id}/sync", status_code=202)
    async def sync_connection(request: Request, connection_id: str,
                              actor: str = Depends(require("operator"))):
        """Start a sync run in the background, or join the one already running.
        The caller polls the runs list for the outcome."""
        engine = sync_or_503()
        await run_in_threadpool(own_connection, request, connection_id)
        try:
            triggered = await run_in_threadpool(engine.start, connection_id, actor=actor, trigger="manual")
        except ConnectionGone as e:
            raise HTTPException(status_code=404, detail=str(e))
        except NeedsReauth as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"run": triggered.run.public(), "joined": triggered.joined}

    @router.post("/connections/{connection_id}/rollback")
    async def rollback_serving(request: Request, connection_id: str,
                               body: Optional[RollbackRequest] = None,
                               actor: str = Depends(require("operator"))):
        """Move the serving pointer to an earlier snapshot and serve it (1.10.4)."""
        engine = sync_or_503()
        c = await run_in_threadpool(own_connection, request, connection_id)
        try:
            pointer, served = await run_in_threadpool(
                engine.rollback, actor=actor, tenant_id=c.tenant_id,
                to_snapshot_id=body.snapshot_id if body else None)
        except RollbackRefused as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"serving": pointer.public(), "served": served}

    @router.post("/connections/start")
    async def start_connection(body: StartRequest, actor: str = Depends(require("admin"))):
        flow = flow_or_503()
        started = await run_in_threadpool(flow.start, body.environment)
        # The state is not returned: the browser needs only the URL, and the
        # callback is matched on the state Salesforce carries back.
        return {"authorize_url": started.authorize_url}

    @router.post("/connections/{connection_id}/verify")
    async def verify_connection(request: Request, connection_id: str,
                                actor: str = Depends(require("operator"))):
        """Refresh the token and ask the org who it is. Proves the stored token works."""
        tokens = tokens_or_503()
        await run_in_threadpool(own_connection, request, connection_id)
        try:
            return await run_in_threadpool(tokens.verify, connection_id)
        except ConnectionGone as e:
            raise HTTPException(status_code=404, detail=str(e))
        except NeedsReauth as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RefreshFailed as e:
            raise HTTPException(status_code=502, detail=str(e))

    @router.post("/connections/{connection_id}/disconnect")
    async def disconnect_connection(request: Request, connection_id: str,
                                    actor: str = Depends(require("admin"))):
        """Revoke at Salesforce, delete the stored token, audit."""
        tokens = tokens_or_503()
        await run_in_threadpool(own_connection, request, connection_id)
        try:
            return await run_in_threadpool(tokens.disconnect, connection_id, actor)
        except ConnectionGone as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get(CALLBACK_PATH.removeprefix("/api"), response_class=HTMLResponse)
    async def salesforce_callback(request: Request, code: str = "", state: str = "",
                                  error: str = "", error_description: str = ""):
        """Salesforce redirects the popup here. Completes the flow server-side."""
        origin = site_origin()
        if error:
            # The user pressed Deny, or the app is misconfigured. Nothing to revoke.
            logger.warning(f"Salesforce callback returned error={error}")
            return HTMLResponse(_popup_page(origin, False, "Not connected",
                                            f"Salesforce reported: {error}. {error_description}"[:300],
                                            "denied"), status_code=400)
        try:
            flow = flow_or_503()
        except HTTPException as e:
            return HTMLResponse(_popup_page(origin, False, "Not connected", str(e.detail), "unavailable"),
                                status_code=e.status_code)
        try:
            # The popup is the same browser, so the session cookie came back with
            # Salesforce's redirect: the audit names the admin who connected.
            sessions = getattr(get_state(), "sessions", None)
            who = (await run_in_threadpool(sessions.get, request.cookies.get(COOKIE))
                   if sessions is not None else None)
            done = await run_in_threadpool(flow.complete, code, state, actor_of(who) if who else ACTOR)
        except FlowRefused as e:
            return HTMLResponse(_popup_page(origin, False, "Not connected", e.reason, e.event),
                                status_code=400)
        except Exception as e:
            logger.error(f"Salesforce callback failed: {type(e).__name__}: {e}", exc_info=True)
            return HTMLResponse(_popup_page(origin, False, "Not connected",
                                            f"Something failed on our side ({type(e).__name__}). "
                                            "Nothing was stored.", "failed"), status_code=500)
        c = done.connection
        return HTMLResponse(_popup_page(
            origin, True, "Connected to Salesforce",
            f"Org {c.org_id} at {c.instance_url.removeprefix('https://')}.", done.event))

    return router
