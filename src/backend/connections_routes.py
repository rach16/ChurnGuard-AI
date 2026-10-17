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

The operator gate is the stand-in the ADR names: until operator login exists
(2.6), an operator action must carry ``X-Operator-Key``, which the site's server
attaches from its own secret on the login-gated staging preview and does not hold
anywhere else. A deployment with no ``OPERATOR_KEY`` answers 404 to every operator
route, so the live site cannot start a flow at all.
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import os
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from integrations.flow import CALLBACK_PATH, ConnectFlow, FlowRefused
from integrations.sync import RollbackRefused, SyncEngine
from integrations.tokens import ConnectionGone, NeedsReauth, RefreshFailed, TokenService

logger = logging.getLogger(__name__)

OPERATOR_HEADER = "X-Operator-Key"
ACTOR = "operator"


def operator_actions_enabled() -> bool:
    return bool(os.getenv("OPERATOR_KEY", "").strip())


def require_operator(request: Request) -> str:
    key = os.getenv("OPERATOR_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=404, detail="Operator actions are not enabled on this deployment.")
    presented = request.headers.get(OPERATOR_HEADER, "")
    if not presented or not hmac.compare_digest(presented, key):
        logger.warning(f"operator key refused on {request.url.path}")
        raise HTTPException(status_code=403, detail="Operator key required.")
    return ACTOR


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
    async def get_connection():
        """The connection, if any, as anyone may see it."""
        state = get_state()
        store = getattr(state, "connections", None)
        if store is None:
            off = (getattr(state, "not_applicable", {}) or {}).get("connections")
            err = (getattr(state, "errors", {}) or {}).get("connections")
            return {"enabled": False, "reason": off or err or "connections not initialised",
                    "operator_actions": False, "connection": None}
        connection = await run_in_threadpool(store.get)
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
        return {"enabled": True, "reason": None,
                "operator_actions": operator_actions_enabled(),
                "pinned_org_id": os.getenv("SALESFORCE_ORG_ID", "").strip() or None,
                "connection": public, "last_run": last_run, "serving": serving}

    @router.get("/connections/{connection_id}/runs")
    async def list_runs(connection_id: str, limit: int = 10):
        """Recent sync runs, newest first. Counts and timings only; no record data."""
        runs = getattr(get_state(), "sync_runs", None)
        if runs is None:
            return {"enabled": False, "runs": []}
        rows = await run_in_threadpool(runs.runs, connection_id, max(1, min(limit, 50)))
        return {"enabled": True, "runs": [r.public() for r in rows]}

    @router.post("/connections/sync-due")
    async def sync_due(wait: bool = False, actor: str = Depends(require_operator)):
        """The schedule's entry point (1.11): a run for every connection whose
        last success is older than the interval. ``wait=true`` runs inline and
        reports each outcome, so a scheduler can fail when one does."""
        engine = sync_or_503()
        outcomes = await run_in_threadpool(engine.sync_due, actor=actor, trigger="schedule", wait=wait)
        worst = "ok"
        for o in outcomes:
            run = o.get("run") or {}
            if o["action"] == "skipped" or run.get("status") in ("failed", "degraded"):
                worst = "attention"
        return {"outcome": worst, "connections": outcomes}

    @router.post("/connections/{connection_id}/sync", status_code=202)
    async def sync_connection(connection_id: str, actor: str = Depends(require_operator)):
        """Start a sync run in the background, or join the one already running.
        The caller polls the runs list for the outcome."""
        engine = sync_or_503()
        try:
            triggered = await run_in_threadpool(engine.start, connection_id, actor=actor, trigger="manual")
        except ConnectionGone as e:
            raise HTTPException(status_code=404, detail=str(e))
        except NeedsReauth as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"run": triggered.run.public(), "joined": triggered.joined}

    @router.post("/connections/{connection_id}/rollback")
    async def rollback_serving(connection_id: str, body: Optional[RollbackRequest] = None,
                               actor: str = Depends(require_operator)):
        """Move the serving pointer to an earlier snapshot and serve it (1.10.4)."""
        engine = sync_or_503()
        store = get_state().connections
        c = await run_in_threadpool(store.get_by_id, connection_id)
        if c is None:
            raise HTTPException(status_code=404, detail="This connection no longer exists.")
        try:
            pointer, served = await run_in_threadpool(
                engine.rollback, actor=actor, tenant_id=c.tenant_id,
                to_snapshot_id=body.snapshot_id if body else None)
        except RollbackRefused as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"serving": pointer.public(), "served": served}

    @router.post("/connections/start")
    async def start_connection(body: StartRequest, actor: str = Depends(require_operator)):
        flow = flow_or_503()
        started = await run_in_threadpool(flow.start, body.environment)
        # The state is not returned: the browser needs only the URL, and the
        # callback is matched on the state Salesforce carries back.
        return {"authorize_url": started.authorize_url}

    @router.post("/connections/{connection_id}/verify")
    async def verify_connection(connection_id: str, actor: str = Depends(require_operator)):
        """Refresh the token and ask the org who it is. Proves the stored token works."""
        tokens = tokens_or_503()
        try:
            return await run_in_threadpool(tokens.verify, connection_id)
        except ConnectionGone as e:
            raise HTTPException(status_code=404, detail=str(e))
        except NeedsReauth as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RefreshFailed as e:
            raise HTTPException(status_code=502, detail=str(e))

    @router.post("/connections/{connection_id}/disconnect")
    async def disconnect_connection(connection_id: str, actor: str = Depends(require_operator)):
        """Revoke at Salesforce, delete the stored token, audit."""
        tokens = tokens_or_503()
        try:
            return await run_in_threadpool(tokens.disconnect, connection_id, actor)
        except ConnectionGone as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get(CALLBACK_PATH.removeprefix("/api"), response_class=HTMLResponse)
    async def salesforce_callback(code: str = "", state: str = "", error: str = "",
                                  error_description: str = ""):
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
            done = await run_in_threadpool(flow.complete, code, state)
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
