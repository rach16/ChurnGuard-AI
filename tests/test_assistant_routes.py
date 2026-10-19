"""
The assistant's routes (ADR-0016, 4.6.3), with a scripted model: no model is
called.

What these pin: the routes need a signed-in person; the stream is steps, then
one checked answer, then the end; an action request is refused without a tool
call; every question leaves one audit row with counts and never the text; a
fifth concurrent question is refused with a retry hint; without a model the
assistant says why instead of failing; suggestions come from the book; the AI
allowance is per person, not per address.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

import assistant_routes  # noqa: E402
from assistant import service  # noqa: E402
from integrations import crypto  # noqa: E402
from tests.cognito_fakes import SITE  # noqa: E402
from tests.salesforce_fakes import FakeStore  # noqa: E402
from tests.signed_in import ORIGIN, app_with, enable_sign_in, sign_in  # noqa: E402
from tests.test_assistant_graph import FakeModel, ref_for, starlight  # noqa: E402
from tests.test_assistant_tools import builder  # noqa: E402

SALESFORCE = "data/salesforce"


class State:
    def __init__(self):
        self.connections = FakeStore()
        self.health_scorer = builder(SALESFORCE).scorer
        self.notes_index = None
        self.errors: dict = {}
        self.not_applicable = {"survival_model": "Not enough history yet"}


def story_model() -> FakeModel:
    s = starlight()
    return FakeModel(
        plan={"agents": ["account"], "out_of_scope": False},
        calls={"account": [[("get_story", {"account_id": s["id"]})]]},
        findings={"account": lambda res: [{"text": res[ref_for(res, "headline")]["data"]["headline"],
                                           "citations": [ref_for(res, "headline")]}]},
        verdicts=lambda t: [{"index": 0, "supported": True, "reason": ""}])


@pytest.fixture
def site(monkeypatch):
    """A signed-in browser on an app with the assistant router and a scripted model."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    holder = {"model": story_model()}
    monkeypatch.setattr(assistant_routes, "LangChainModel", lambda: holder["model"])
    monkeypatch.setattr(assistant_routes, "judge_model", lambda: holder["model"])
    state = State()
    provider = crypto.LocalKeyProvider({"v1": b"k" * 32}, current="v1")
    cognito = enable_sign_in(state, provider, state.connections.db, groups=("viewer",))
    client = app_with(state, lambda gs: assistant_routes.build_router(gs, lambda: builder(SALESFORCE)))
    return sign_in(client, cognito), state, holder


def events(response) -> list[tuple[str, dict]]:
    out, kind = [], None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            out.append((kind, json.loads(line[6:])))
    return out


def test_the_routes_need_a_signed_in_person(monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", SITE)
    state = State()
    enable_sign_in(state, crypto.LocalKeyProvider({"v1": b"k" * 32}, current="v1"), state.connections.db)
    client = app_with(state, lambda gs: assistant_routes.build_router(gs, lambda: builder(SALESFORCE)))
    assert client.get("/assistant/status").status_code == 401
    assert client.post("/assistant/ask", json={"question": "Why?"}, headers=ORIGIN).status_code == 401


def test_the_stream_is_steps_then_one_checked_answer(site):
    client, state, _ = site
    r = client.post("/assistant/ask", json={"question": "Why is Starlight Freight Co at risk?"}, headers=ORIGIN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = events(r)
    kinds = [k for k, _ in ev]
    assert kinds[-1] == "answer" and kinds.count("answer") == 1 and set(kinds[:-1]) == {"step"}
    assert any(e["text"] == "Reading Starlight Freight Co's story" for k, e in ev if k == "step")
    answer = ev[-1][1]
    assert answer["answer"][0]["text"].startswith("Starlight Freight Co renews in")
    assert answer["answer"][0]["citations"][0]["tool"] == "get_story"
    row = state.connections.audit[-1]
    assert row["event"] == "assistant_question" and row["actor"].startswith("user:")
    d = row["detail"]
    assert d["outcome"] == "answered" and d["tools"] == ["get_story"] and d["agents"] == ["account"]
    assert d["question_chars"] == len("Why is Starlight Freight Co at risk?")
    assert "Starlight" not in json.dumps(d)                               # never the question or the answer


def test_an_action_request_is_refused_and_audited(site):
    client, state, holder = site
    holder["model"] = FakeModel(plan={"agents": ["book"], "out_of_scope": False}, calls={}, findings={})
    ev = events(client.post("/assistant/ask", json={"question": "Email every at-risk customer a discount"},
                            headers=ORIGIN))
    assert ev[-1][0] == "answer" and ev[-1][1]["out_of_scope"] is True and ev[-1][1]["tool_calls"] == []
    assert state.connections.audit[-1]["detail"]["outcome"] == "out_of_scope"


def test_a_failing_model_is_a_sentence_not_a_crash(site):
    client, state, holder = site

    class Down(FakeModel):
        def structured(self, messages, schema):
            raise ConnectionError("provider unreachable")

        def complete(self, messages, tools, require_tool=False):
            raise ConnectionError("provider unreachable")

    holder["model"] = Down(plan=None, calls={}, findings={})
    ev = events(client.post("/assistant/ask", json={"question": "How many accounts are at risk?"}, headers=ORIGIN))
    assert ev[-1][0] == "error" and "rest of the site is unaffected" in ev[-1][1]["detail"]
    assert state.connections.audit[-1]["detail"]["outcome"] == "error"
    assert service.try_acquire()                                          # the slot came back
    service.release()


def test_a_fifth_question_at_once_is_refused_with_a_retry_hint(site):
    client, _, _ = site
    taken = [service.try_acquire() for _ in range(service.MAX_CONCURRENT)]
    try:
        assert all(taken)
        r = client.post("/assistant/ask", json={"question": "How many accounts are at risk?"}, headers=ORIGIN)
        assert r.status_code == 429 and r.headers["retry-after"] == "5"
    finally:
        for _ in taken:
            service.release()


def test_without_a_model_it_says_why(site, monkeypatch):
    client, _, _ = site
    monkeypatch.delenv("OPENAI_API_KEY")
    status = client.get("/assistant/status").json()
    assert status["available"] is False and "OPENAI_API_KEY" in status["reason"]
    r = client.post("/assistant/ask", json={"question": "How many accounts are at risk?"}, headers=ORIGIN)
    assert r.status_code == 503 and "OPENAI_API_KEY" in r.json()["detail"]


def test_status_and_suggestions(site):
    client, _, _ = site
    status = client.get("/assistant/status").json()
    assert status["available"] is True and status["judge"] == "gpt-4o" and status["notes"] == "off"
    suggested = client.get("/assistant/suggestions").json()["suggestions"]
    assert suggested[0] == "Why is Starlight Freight Co at risk, and what should we do?"
    assert len(suggested) >= 3
    one = client.get(f"/assistant/suggestions?account_id={starlight()['id']}").json()["suggestions"]
    assert one[0].startswith("Why is Starlight Freight Co")


def test_a_question_from_another_origin_is_refused(site):
    client, _, _ = site
    r = client.post("/assistant/ask", json={"question": "How many accounts are at risk?"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_the_ai_allowance_is_per_person_not_per_address(monkeypatch):
    import security
    from fastapi import FastAPI

    monkeypatch.setenv("LLM_RATE_LIMIT_PER_HOUR", "2")
    monkeypatch.delenv("API_KEYS", raising=False)
    app = FastAPI()

    @app.post("/assistant/ask")
    def ask():
        return {"ok": True}

    security.install(app)
    client = TestClient(app, base_url="https://testserver")
    one = {security.SESSION_COOKIE: "session-one"}
    two = {security.SESSION_COOKIE: "session-two"}
    assert [client.post("/assistant/ask", cookies=one).status_code for _ in range(3)] == [200, 200, 429]
    assert client.post("/assistant/ask", cookies=two).status_code == 200      # same address, another person
