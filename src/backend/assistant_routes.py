"""
The assistant's routes (4.6.3, ADR-0016 sections 8 and 12).

``POST /assistant/ask`` streams server-sent events: ``step`` events as the
agents work ("Looking up Starlight Freight…"), then one ``answer`` event with
the checked answer, or ``error``. The answer is sent whole, after the checker:
no number reaches the page unchecked. A closed page sets the cancel event and
the graph stops at its next step. ``GET /assistant/status`` says whether it can
answer here and why not; ``GET /assistant/suggestions`` gives questions written
from the book.

Signed in, any role: asking reads. It is an action for the purposes of the gate
(site origin, Cognito re-check), because it spends money. The per-person hourly
allowance and the shared daily budget are the security middleware's (2.7); the
four-at-once limit is the service's.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from access import make_require
from assistant import service
from assistant.graph import Assistant, Cancelled
from assistant.model import LangChainModel, judge_model
from assistant.tools import ToolContext, Tools
from core.datasource import serving_info
from integrations import audit
from integrations.connections import DEFAULT_TENANT
from integrations.login import actor as actor_of

logger = logging.getLogger("churnguard.assistant.routes")

MAX_QUESTION_CHARS = int(os.getenv("MAX_QUESTION_CHARS", 2000))


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=MAX_QUESTION_CHARS)
    account_id: Optional[int] = Field(None, ge=0)


def tool_context(state: Any, builder: Any, tenant: str) -> ToolContext:
    """What a caller in ``tenant`` may read: the served snapshot, its notes index.
    The assistant and the MCP server (ADR-0016 §10a) build it the same way."""
    info = serving_info() or {}
    return ToolContext(tenant=tenant, served_tenant=info.get("tenant_id", DEFAULT_TENANT), builder=builder,
                       snapshot_id=info.get("snapshot_id") or f"pull-{builder.scorer.as_of.date().isoformat()}",
                       as_of=builder.scorer.as_of.date().isoformat(), notes=getattr(state, "notes_index", None))


def _sse(kind: str, payload: Any) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"


def build_router(get_state: Callable[[], Any], get_builder: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["assistant"])
    require = make_require(get_state)
    models: dict[str, Any] = {}

    def unavailable() -> Optional[str]:
        """Why the assistant cannot answer on this deployment, or None."""
        state = get_state()
        if getattr(state, "health_scorer", None) is None:
            return "No data is being served yet."
        if not os.getenv("OPENAI_API_KEY") and os.getenv("LLM_PROVIDER", "openai") == "openai":
            return "No model is configured on this deployment (OPENAI_API_KEY is not set)."
        return None

    def get_models():
        if "agent" not in models:
            models["agent"], models["judge"] = LangChainModel(), judge_model()
        return models["agent"], models["judge"]

    def tenant_of(request: Request) -> str:
        session = getattr(request.state, "session", None)
        return session.tenant_id if session is not None else DEFAULT_TENANT

    def context(tenant: str) -> ToolContext:
        return tool_context(get_state(), get_builder(), tenant)

    def record(request: Request, tenant: str, detail: dict[str, Any]) -> None:
        db = getattr(getattr(get_state(), "connections", None), "db", None)
        session = getattr(request.state, "session", None)
        if db is None:
            logger.info(f"assistant question (no audit database): {detail}")
            return
        try:
            with db.connection() as conn:
                audit.record(conn, "assistant_question", actor=actor_of(session) if session else "anonymous",
                             tenant_id=tenant, detail=detail)
        except Exception as e:  # noqa: BLE001 - an audit failure is logged, never shown as the answer failing
            logger.error(f"assistant audit failed: {type(e).__name__}: {e}")

    @router.get("/assistant/status")
    async def status():
        reason = unavailable()
        state = get_state()
        notes = getattr(state, "notes_index", None)
        notes_state = ("off" if notes is None
                       else "failed" if "notes_index" in (getattr(state, "errors", {}) or {}) else "ready")
        agent_model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        from assistant.model import judge_model_name
        return {"available": reason is None, "reason": reason, "model": agent_model, "judge": judge_model_name(),
                "notes": notes_state, "max_concurrent": service.MAX_CONCURRENT}

    @router.get("/assistant/suggestions")
    async def suggestions(account_id: Optional[int] = None):
        if get_state().health_scorer is None:
            return {"suggestions": []}
        return {"suggestions": service.suggestions(get_builder(), account_id=account_id,
                                                   notes=getattr(get_state(), "notes_index", None) is not None)}

    @router.post("/assistant/ask")
    async def ask(request: Request, body: AskRequest, actor: str = Depends(require("viewer"))):
        reason = unavailable()
        if reason:
            raise HTTPException(status_code=503, detail=reason)
        if not service.try_acquire():
            raise HTTPException(status_code=429, detail=f"The assistant is answering {service.MAX_CONCURRENT} "
                                                        "questions already. Try again in a few seconds.",
                                headers={"Retry-After": "5"})
        tenant = tenant_of(request)
        state = get_state()
        try:
            ctx = context(tenant)
            agent_model, judge = get_models()
        except Exception as e:  # noqa: BLE001
            service.release()
            raise HTTPException(status_code=503, detail=f"The assistant could not start: {type(e).__name__}.")

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        cancel = threading.Event()
        put = lambda item: loop.call_soon_threadsafe(queue.put_nowait, item)  # noqa: E731
        client = service.langfuse_client()
        tracer = (service.LangfuseTracer(client, question=body.question, user=actor, tenant=tenant,
                                         metadata={"account_id": body.account_id, "snapshot_id": ctx.snapshot_id})
                  if client is not None else None)
        predictions = "survival_model" not in (getattr(state, "not_applicable", {}) or {})

        def work() -> None:
            answer, outcome, error = None, "error", None
            try:
                assistant = Assistant(agent_model, Tools(ctx), judge=judge, predictions_available=predictions,
                                      on_event=lambda e: put(("step", e)), cancel=cancel,
                                      tracer=tracer or None)
                answer = assistant.ask(body.question, body.account_id)
                outcome = service.outcome_of(answer)
                put(("answer", answer))
            except Cancelled:
                outcome = "cancelled"
            except Exception as e:  # noqa: BLE001 - the page gets a sentence, the log gets the trace
                error = f"{type(e).__name__}: {e}"
                logger.error(f"assistant failed: {error}", exc_info=True)
                put(("error", {"detail": "The assistant could not answer: the model provider did not respond. "
                                         "The rest of the site is unaffected; try again shortly."}))
            finally:
                service.release()
                record(request, tenant, service.audit_detail(answer, outcome=outcome, question=body.question,
                                                             error=error))
                if tracer is not None:
                    tracer.finish(outcome, answer)
                put(("done", None))

        threading.Thread(target=work, name="assistant-question", daemon=True).start()

        async def stream():
            while True:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        cancel.set()
                        return
                    yield ": keep-alive\n\n"
                    continue
                if kind == "done":
                    return
                yield _sse(kind, payload)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    return router
