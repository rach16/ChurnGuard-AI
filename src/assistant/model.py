"""
The one interface the assistant's graph talks to a model through.

Two calls: ``complete`` with tools (the model may answer or ask for tool calls)
and ``structured`` with a JSON schema (the model must return an object of that
shape). The production adapter wraps whatever chat model the provider factory
builds (4.2), so the provider stays configuration; tests pass a scripted fake
with the same two methods and never call a model.

Messages are plain dicts: ``{"role": "system" | "user" | "assistant" | "tool",
"content": str, "tool_calls": [...], "tool_call_id": str}``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

MODEL_TIMEOUT_SECONDS = 20
MODEL_RETRIES = 1


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


@dataclass
class Structured:
    data: Optional[dict[str, Any]]          # None when the model's output did not fit the schema
    usage: Usage = field(default_factory=Usage)
    error: Optional[str] = None


class Model(Protocol):
    name: str

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply: ...

    def structured(self, messages: list[dict[str, Any]], schema: dict[str, Any]) -> Structured: ...


def _usage(message: Any) -> Usage:
    meta = getattr(message, "usage_metadata", None) or {}
    return Usage(int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0)))


def _to_langchain(messages: list[dict[str, Any]]) -> list[Any]:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    out: list[Any] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            out.append(SystemMessage(content=m["content"]))
        elif role == "user":
            out.append(HumanMessage(content=m["content"]))
        elif role == "assistant":
            out.append(AIMessage(content=m.get("content") or "", tool_calls=[
                {"id": c["id"], "name": c["name"], "args": c["args"]} for c in m.get("tool_calls", [])]))
        elif role == "tool":
            out.append(ToolMessage(content=m["content"], tool_call_id=m["tool_call_id"]))
    return out


class LangChainModel:
    """The provider factory's chat model behind the two calls the graph needs."""

    def __init__(self, chat_model: Any = None, name: Optional[str] = None):
        if chat_model is None:
            from core.llm import chat_model as make

            chat_model = make(temperature=0.0, timeout=MODEL_TIMEOUT_SECONDS, max_retries=MODEL_RETRIES)
        self.chat = chat_model
        self.name = name or getattr(chat_model, "model_name", None) or getattr(chat_model, "model", "model")

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        bound = self.chat.bind_tools([{"type": "function", "function": t} for t in tools]) if tools else self.chat
        message = bound.invoke(_to_langchain(messages))
        calls = [ToolCall(id=c.get("id") or f"call_{i}", name=c["name"], args=dict(c.get("args") or {}))
                 for i, c in enumerate(getattr(message, "tool_calls", None) or [])]
        content = message.content if isinstance(message.content, str) else json.dumps(message.content)
        return Reply(content=content, tool_calls=calls, usage=_usage(message))

    def structured(self, messages: list[dict[str, Any]], schema: dict[str, Any]) -> Structured:
        runnable = self.chat.with_structured_output(schema, include_raw=True)
        out = runnable.invoke(_to_langchain(messages))
        raw, parsed, error = out.get("raw"), out.get("parsed"), out.get("parsing_error")
        return Structured(data=parsed if isinstance(parsed, dict) else None,
                          usage=_usage(raw), error=str(error) if error else None)


# The judge reads meaning, which the small model did badly: on real runs
# (2026-10-18) gpt-4o-mini removed a correct claim in one of two runs and let
# a figure attached to the wrong set of accounts through. The owner chose a
# stronger model for this step only (ADR-0016, decision of 2026-10-18).
DEFAULT_OPENAI_JUDGE = "gpt-4o"


def judge_model_name() -> str:
    import os

    configured = os.getenv("ASSISTANT_JUDGE_MODEL", "").strip()
    if configured:
        return configured
    if os.getenv("LLM_PROVIDER", "openai").strip().lower() == "openai":
        return DEFAULT_OPENAI_JUDGE
    return os.getenv("LLM_MODEL", "").strip() or DEFAULT_OPENAI_JUDGE


def judge_model() -> "LangChainModel":
    from core.llm import chat_model as make

    name = judge_model_name()
    return LangChainModel(make(temperature=0.0, model=name, timeout=MODEL_TIMEOUT_SECONDS,
                               max_retries=MODEL_RETRIES), name=name)
