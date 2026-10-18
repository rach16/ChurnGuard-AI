"""
What sits between the route and the graph (ADR-0016 sections 8 and 12).

* **Concurrency**: at most ``MAX_CONCURRENT`` questions run at once per
  instance (the free instance has 512 MB); one more is refused with a retry hint
  rather than queued behind a slow answer.
* **Tracing**: when ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY`` are set,
  each question is one trace with a span per tool call and a generation per
  model call, with the model, tokens and duration. Every string sent is passed
  through the same scrub as tool results. Off when the keys are absent.
* **Audit**: one ``assistant_question`` row per question: who, which agents and
  tools, tokens, model calls, seconds, outcome. Never the question or the answer.
* **Suggestions**: written from the book being served, not canned.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from assistant.graph import Tracer
from assistant.model import Usage
from assistant.privacy import scrub

logger = logging.getLogger("churnguard.assistant.service")

MAX_CONCURRENT = int(os.getenv("ASSISTANT_MAX_CONCURRENT", "4"))
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


def try_acquire() -> bool:
    return _slots.acquire(blocking=False)


def release() -> None:
    try:
        _slots.release()
    except ValueError:                       # released more than acquired: a bug, never a crash
        logger.error("assistant: concurrency slot released twice")


# ------------------------------------------------------------------- tracing

_langfuse: Any = None
_langfuse_checked = False
_langfuse_lock = threading.Lock()


def langfuse_client() -> Any:
    """The Langfuse client when configured, else None. Built once."""
    global _langfuse, _langfuse_checked
    with _langfuse_lock:
        if _langfuse_checked:
            return _langfuse
        _langfuse_checked = True
        if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
            return None
        try:
            from langfuse import Langfuse

            _langfuse = Langfuse(mask=lambda data, **_: scrub(data),
                                 environment=os.getenv("LANGFUSE_ENVIRONMENT") or None)
            logger.info("✓ assistant tracing: Langfuse")
        except Exception as e:  # noqa: BLE001 - tracing must never take the assistant down
            logger.warning(f"assistant tracing off: {type(e).__name__}: {e}")
            _langfuse = None
        return _langfuse


class LangfuseTracer(Tracer):
    """One trace per question; spans for tools, generations for model calls."""

    def __init__(self, client: Any, *, question: str, user: str, tenant: str, metadata: dict[str, Any]):
        self.root = client.start_span(name="assistant_question", input={"question": question},
                                      metadata=metadata)
        self.root.update_trace(name="assistant_question", user_id=user, tags=[f"tenant:{tenant}"])
        self._lock = threading.Lock()

    def start(self, kind: str, name: str, **meta: Any) -> Any:
        try:
            with self._lock:
                if kind == "generation":
                    return self.root.start_generation(name=name, model=meta.pop("model", None), metadata=meta)
                return self.root.start_span(name=f"tool:{name}", input=meta.get("args"), metadata=meta)
        except Exception:  # noqa: BLE001
            return None

    def end(self, handle: Any, **out: Any) -> None:
        if handle is None:
            return
        try:
            usage = out.pop("usage", None)
            if isinstance(usage, Usage):
                handle.update(usage_details={"input": usage.input_tokens, "output": usage.output_tokens},
                              output=out or None)
            else:
                handle.update(output=out or None, level="ERROR" if out.get("error") else None)
            handle.end()
        except Exception:  # noqa: BLE001
            pass

    def finish(self, outcome: str, answer: Optional[dict[str, Any]]) -> None:
        try:
            summary = None
            if answer is not None:
                summary = {"answer": [c["text"] for c in answer.get("answer", [])],
                           "could_not_confirm": answer.get("could_not_confirm"),
                           "limitation": answer.get("limitation")}
            self.root.update(output=summary, metadata={"outcome": outcome})
            self.root.update_trace(output=summary)
            self.root.end()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------- audit

def audit_detail(answer: Optional[dict[str, Any]], *, outcome: str, question: str,
                 error: Optional[str] = None) -> dict[str, Any]:
    """Counts and names, never the question or the answer text."""
    detail: dict[str, Any] = {"outcome": outcome, "question_chars": len(question)}
    if error:
        detail["error"] = error[:300]
    if answer is not None:
        calls = answer.get("tool_calls", [])
        detail.update({
            "agents": answer["plan"]["agents"], "plan_source": answer["plan"]["source"],
            "tools": [c["name"] for c in calls], "tool_calls_refused": sum(1 for c in calls if c.get("refused")),
            "model_calls": answer.get("model_calls"), "input_tokens": answer["usage"]["input_tokens"],
            "output_tokens": answer["usage"]["output_tokens"], "seconds": answer.get("seconds"),
            "claims": len(answer.get("answer", [])), "removed": len(answer.get("could_not_confirm", [])),
            "revisions": answer.get("revisions"), "fallback": bool(answer.get("fallback")),
            "prompt_version": answer.get("prompt_version"), "model": answer.get("model"),
            "judge": answer.get("judge"),
        })
    return detail


def outcome_of(answer: dict[str, Any]) -> str:
    if answer.get("out_of_scope"):
        return "out_of_scope"
    if answer.get("fallback"):
        return "fallback"
    return "answered"


# --------------------------------------------------------------- suggestions

def suggestions(builder, *, account_id: Optional[int] = None, notes: bool = False) -> list[str]:
    """Questions worth asking of this book, from what is being served."""
    rows = builder.queue()
    if not rows:
        return []
    out: list[str] = []
    if account_id is not None:
        c = builder.scorer.get_customer(account_id)
        if c is not None:
            out.append(f"Why is {c['name']} at risk, and what should we do?")
            if notes:
                out.append(f"What have {c['name']}'s CSM notes and cases said recently?")
    top = rows[0]
    if account_id is None:
        out.append(f"Why is {top['name']} at risk, and what should we do?")
    out.append("Which at-risk accounts renew in the next 90 days, and how much ARR do they hold?")
    out.append("Why did customers leave, and which competitors did they go to?")
    if any(any("champion" in t.lower() or "sponsor" in t.lower() for t in r["adverse"]) for r in rows):
        out.append("Which accounts have lost a champion or sponsor, and what has worked for them?")
    if notes:
        out.append("Has any customer mentioned evaluating a competitor?")
    return out[:5]
