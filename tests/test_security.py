"""
Authentication, rate limiting, and the input cap.

The rate limiter is tested directly with an injected clock rather than through
the app, so the window behaviour is exact and the suite does not sleep. The
middleware is tested through a real app, because what matters there is which
paths are exempt and what status a rejection carries.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

import security  # noqa: E402
from core.llm import DEFAULT_MAX_TOKENS, chat_model, max_output_tokens  # noqa: E402


def build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/protected")
    async def protected():
        return {"ok": True}

    security.install(app)
    return app


# --------------------------------------------------------------------- limiter

def test_window_allows_exactly_the_limit():
    rl = security.RateLimiter(limit=3, window=60)
    assert [rl.check("a", now=0)[0] for _ in range(4)] == [True, True, True, False]


def test_callers_do_not_share_a_budget():
    rl = security.RateLimiter(limit=2, window=60)
    for _ in range(2):
        assert rl.check("a", now=0)[0]
    assert rl.check("a", now=0)[0] is False
    assert rl.check("b", now=0)[0] is True, "one caller exhausted another's quota"


def test_keys_sharing_a_prefix_get_separate_budgets():
    """The bug this module shipped with: an 8-char prefix as the caller id.

    Two keys from the same generator commonly share a prefix, and did here.
    """
    assert security.caller_id("test-key-alpha") != security.caller_id("test-key-beta")


def test_caller_id_does_not_contain_the_key():
    """It reaches logs and the limiter's dict, so it must not be reversible."""
    key = "sk-super-secret-value"
    ident = security.caller_id(key)
    assert key not in ident and key[:8] not in ident


def test_window_slides_rather_than_resetting():
    """A fixed window lets a caller send double the limit across the boundary."""
    rl = security.RateLimiter(limit=2, window=60)
    assert rl.check("a", now=0)[0]
    assert rl.check("a", now=59)[0]
    assert rl.check("a", now=59.5)[0] is False      # both still in window
    assert rl.check("a", now=61)[0] is True         # the first has aged out


def test_retry_after_points_past_the_oldest_hit():
    rl = security.RateLimiter(limit=1, window=60)
    rl.check("a", now=10)
    allowed, _, retry = rl.check("a", now=30)
    assert allowed is False
    assert retry == pytest.approx(40)               # 10 + 60 - 30


def test_idle_callers_are_pruned():
    rl = security.RateLimiter(limit=1, window=60)
    for i in range(4200):
        rl.check(f"caller-{i}", now=0)
    rl.check("fresh", now=10_000)
    assert len(rl._hits) < 4200, "unbounded growth per distinct caller"


# ------------------------------------------------------------------ middleware

def test_open_mode_serves_without_a_key(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    client = TestClient(build_app())
    assert client.get("/protected").status_code == 200


def test_missing_key_is_401_and_names_the_header(monkeypatch):
    monkeypatch.setenv("API_KEYS", "alpha")
    client = TestClient(build_app())
    r = client.get("/protected")
    assert r.status_code == 401
    assert security.API_KEY_HEADER in r.json()["detail"]


def test_wrong_key_is_403_not_401(monkeypatch):
    """401 means 'authenticate'; 403 means 'you did, and it is not valid'."""
    monkeypatch.setenv("API_KEYS", "alpha")
    client = TestClient(build_app())
    assert client.get("/protected", headers={"X-API-Key": "beta"}).status_code == 403


def test_valid_key_passes(monkeypatch):
    monkeypatch.setenv("API_KEYS", "alpha,beta")
    client = TestClient(build_app())
    for key in ("alpha", "beta"):
        assert client.get("/protected", headers={"X-API-Key": key}).status_code == 200


def test_probes_are_exempt(monkeypatch):
    """A health check cannot present a credential, and requiring one turns every
    deployment into a crash loop that looks like an application fault."""
    monkeypatch.setenv("API_KEYS", "alpha")
    client = TestClient(build_app())
    assert client.get("/health").status_code == 200
    assert "/health" in security.EXEMPT_PATHS and "/ready" in security.EXEMPT_PATHS


def test_rate_limit_headers_are_present(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    r = TestClient(build_app()).get("/protected")
    assert r.headers["X-RateLimit-Limit"]
    assert r.headers["X-RateLimit-Remaining"]


def test_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    client = TestClient(build_app())
    codes = [client.get("/protected").status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]
    assert client.get("/protected").headers["Retry-After"]


def test_status_summary_leaks_nothing(monkeypatch):
    monkeypatch.setenv("API_KEYS", "super-secret-key")
    summary = security.status_summary()
    assert summary["auth"] == "enabled"
    assert summary["keys_configured"] == 1
    assert "super-secret-key" not in str(summary)
    assert "replicas" in summary["rate_limit_scope"], "the per-process limit must be stated"


# ------------------------------------------------------------------ token cap

def test_output_cap_is_applied_by_default(monkeypatch):
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    assert max_output_tokens() == DEFAULT_MAX_TOKENS
    model = chat_model()                      # constructed, never called
    assert model.max_tokens == DEFAULT_MAX_TOKENS


def test_cap_can_be_overridden_by_a_caller(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    assert chat_model(max_tokens=64).max_tokens == 64


def test_zero_means_uncapped(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    assert max_output_tokens() == 0
    assert chat_model().max_tokens is None


def test_a_nonsense_cap_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "lots")
    assert max_output_tokens() == DEFAULT_MAX_TOKENS


def test_ollama_uses_its_own_parameter_name():
    """Providers spell the cap differently; a wrong name is silently ignored."""
    from core.llm import PROVIDERS
    assert PROVIDERS["ollama"].max_tokens_arg == "num_predict"
    assert PROVIDERS["openai"].max_tokens_arg == "max_tokens"
