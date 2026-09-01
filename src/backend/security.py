"""
API key authentication, rate limiting, and the exemptions both need.

Three separate concerns that share one piece of state -- who is calling -- so they
live together rather than in two middlewares that each have to re-derive it.

The design constraint that shapes everything here: **this service already runs in a
degraded mode on purpose**, serving CSV-backed endpoints when the LLM stack is
unavailable. Security must not turn a degraded service into a dead one, so probes
and documentation are exempt and every rejection says exactly what was wrong.

What this is not: a substitute for a load balancer, WAF, or identity provider. It
is the floor a service should not go to production without, and the limits are
per-process -- see the note on RateLimiter.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from collections import deque
from typing import Deque, Dict, Iterable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Paths that must answer without a key.
#
# The probes are the important ones: an ALB or ECS health check cannot present a
# credential, and requiring one turns every deployment into a crash loop that
# looks like an application fault. /ready deliberately reports readiness to
# anyone -- it exposes which components are up, which is operational detail
# rather than customer data.
EXEMPT_PATHS = frozenset({
    "/", "/health", "/ready", "/docs", "/redoc", "/openapi.json", "/favicon.ico",
})

API_KEY_HEADER = "X-API-Key"

DEFAULT_RATE_LIMIT = 60          # requests per window, per caller
RATE_WINDOW_SECONDS = 60

# The routes that spend money. Everything else reads CSV, DuckDB or a fitted
# model and costs nothing per request, so the general limit above is about abuse
# rather than spend. These are different: each call is a paid API request, and a
# public deployment hands that bill to anyone who finds the URL.
LLM_PATHS = frozenset({"/ask", "/analyze-churn", "/multi-agent-analyze"})

# Per caller, per hour. Deliberately low: a person exploring the demo asks a
# handful of questions, not twenty.
DEFAULT_LLM_HOURLY_LIMIT = 10
LLM_WINDOW_SECONDS = 3600

# Across every caller, per day. This is the actual spend ceiling and the reason
# the module exists -- a per-caller limit alone caps one person, not a crowd or
# one person rotating addresses. At roughly $0.001 a request this bounds the
# whole deployment to a few cents a day.
DEFAULT_LLM_DAILY_BUDGET = 200
DAY_SECONDS = 86400


def configured_keys() -> frozenset[str]:
    """Keys from the environment. Empty means authentication is disabled."""
    raw = os.getenv("API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def auth_enabled() -> bool:
    return bool(configured_keys())


def _matches_any(presented: str, keys: Iterable[str]) -> bool:
    """Compare against every key without leaking which one matched, or how far.

    hmac.compare_digest is constant-time for a single comparison. Iterating and
    returning early on success still leaks position through timing, so every key
    is compared and the results are OR-ed at the end.
    """
    found = False
    for key in keys:
        if hmac.compare_digest(presented, key):
            found = True
    return found


def caller_id(key: str) -> str:
    """A stable, non-reversible identifier for a key.

    A prefix of the key looks adequate and is not: two keys issued from the same
    generator commonly share a prefix, and any that do would silently share one
    rate-limit budget. Found exactly that way -- "test-key-alpha" and
    "test-key-beta" collided on the first eight characters and one exhausted the
    other's quota.

    Hashing also keeps the key itself out of logs and out of the limiter's dict.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class RateLimiter:
    """Sliding-window request counter, per caller.

    **In-process and therefore per-replica.** Two ECS tasks behind a load balancer
    enforce the limit twice, each at the full rate, so the effective limit is the
    configured value times the replica count. That is a real limitation and the
    honest fix is shared state (ElastiCache or the load balancer's own limiter),
    not a cleverer local algorithm. Documented rather than hidden because a rate
    limit believed to be global and silently isn't is worse than none.

    A sliding window rather than a fixed one: fixed windows let a caller send
    double the limit across a boundary, which is the first thing anyone testing a
    rate limiter tries.
    """

    def __init__(self, limit: int = DEFAULT_RATE_LIMIT, window: int = RATE_WINDOW_SECONDS):
        self.limit = limit
        self.window = window
        self._hits: Dict[str, Deque[float]] = {}

    def check(self, caller: str, now: Optional[float] = None) -> tuple[bool, int, float]:
        """Record a request and say whether it is allowed.

        Returns (allowed, remaining, retry_after_seconds).
        """
        now = time.monotonic() if now is None else now
        cutoff = now - self.window

        hits = self._hits.setdefault(caller, deque())
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= self.limit:
            # Oldest hit falls out of the window at hits[0] + window.
            return False, 0, max(0.0, hits[0] + self.window - now)

        hits.append(now)
        # Unbounded growth is the failure mode of a dict keyed by caller. Callers
        # with no live hits are dropped opportunistically rather than on a timer.
        if len(self._hits) > 4096:
            self._prune(cutoff)
        return True, self.limit - len(hits), 0.0

    def _prune(self, cutoff: float) -> None:
        stale = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
        for k in stale:
            del self._hits[k]
        logger.debug(f"Rate limiter pruned {len(stale)} idle callers")


class SecurityMiddleware(BaseHTTPMiddleware):
    """Authenticates, rate limits, caps paid requests, then passes through."""

    def __init__(self, app, limiter: Optional[RateLimiter] = None):
        super().__init__(app)
        self.limiter = limiter or RateLimiter(
            limit=int(os.getenv("RATE_LIMIT_PER_MINUTE", DEFAULT_RATE_LIMIT))
        )
        # Separate budget for the paid routes, on top of the general limit.
        self.llm_limiter = RateLimiter(
            limit=int(os.getenv("LLM_RATE_LIMIT_PER_HOUR", DEFAULT_LLM_HOURLY_LIMIT)),
            window=LLM_WINDOW_SECONDS,
        )
        self.llm_daily = RateLimiter(
            limit=int(os.getenv("LLM_DAILY_BUDGET", DEFAULT_LLM_DAILY_BUDGET)),
            window=DAY_SECONDS,
        )

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
            # OPTIONS is the CORS preflight, which browsers send without custom
            # headers by definition. Rejecting it breaks the browser client while
            # protecting nothing -- the real request is still checked.
            return await call_next(request)

        keys = configured_keys()
        presented = request.headers.get(API_KEY_HEADER, "")

        if keys:
            if not presented:
                return JSONResponse(
                    status_code=401,
                    content={"error": "missing_api_key",
                             "detail": f"Send your key in the {API_KEY_HEADER} header."},
                )
            if not _matches_any(presented, keys):
                logger.warning(f"Rejected key on {request.url.path}")
                return JSONResponse(
                    status_code=403,
                    content={"error": "invalid_api_key", "detail": "Key not recognised."},
                )
            caller = f"key:{caller_id(presented)}"
        else:
            # Open mode. Limit by client address so an unauthenticated deployment
            # is still not trivially floodable.
            caller = f"ip:{request.client.host if request.client else 'unknown'}"

        allowed, remaining, retry_after = self.limiter.check(caller)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited",
                         "detail": f"Limit is {self.limiter.limit} requests per "
                                   f"{self.limiter.window}s.",
                         "retry_after_seconds": round(retry_after, 1)},
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )

        # Paid routes pass two more gates: this caller's hourly allowance, and a
        # single shared daily budget. The daily one is checked last so a caller
        # who is already over their own limit does not consume it.
        if request.url.path in LLM_PATHS:
            ok, left, retry = self.llm_limiter.check(f"llm:{caller}")
            if not ok:
                return JSONResponse(
                    status_code=429,
                    content={"error": "llm_rate_limited",
                             "detail": f"AI questions are limited to "
                                       f"{self.llm_limiter.limit} per hour per user.",
                             "retry_after_seconds": round(retry)},
                    headers={"Retry-After": str(max(1, int(retry) + 1))},
                )

            ok, budget_left, retry = self.llm_daily.check("llm:global")
            if not ok:
                # Deliberately not a retry-and-hope: say the budget is spent, so
                # a reader understands this is a cost ceiling and not congestion.
                return JSONResponse(
                    status_code=429,
                    content={"error": "llm_budget_exhausted",
                             "detail": f"The demo's daily AI budget "
                                       f"({self.llm_daily.limit} questions) is spent. "
                                       f"Everything else on the site still works.",
                             "retry_after_seconds": round(retry)},
                    headers={"Retry-After": str(max(1, int(retry) + 1))},
                )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limiter.limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        if request.url.path in LLM_PATHS:
            response.headers["X-LLM-Budget-Remaining"] = str(budget_left)
        return response


def install(app) -> None:
    """Attach the middleware and say clearly which mode the service came up in."""
    app.add_middleware(SecurityMiddleware)

    if auth_enabled():
        logger.info(f"✓ API key auth enabled ({len(configured_keys())} keys)")
    else:
        # Loud, because a service that silently accepts anonymous traffic is the
        # thing this module exists to prevent, and "we thought it was on" is how
        # it happens.
        logger.warning(
            "API_KEYS is not set — the API is serving UNAUTHENTICATED. "
            "Set API_KEYS before exposing this beyond localhost."
        )


def status_summary() -> dict:
    """What /health reports. Never returns a key or a prefix of one."""
    return {
        "auth": "enabled" if auth_enabled() else "disabled",
        "keys_configured": len(configured_keys()),
        "rate_limit_per_minute": int(os.getenv("RATE_LIMIT_PER_MINUTE", DEFAULT_RATE_LIMIT)),
        "rate_limit_scope": "per process — not shared across replicas",
        "llm_rate_limit_per_hour": int(
            os.getenv("LLM_RATE_LIMIT_PER_HOUR", DEFAULT_LLM_HOURLY_LIMIT)),
        "llm_daily_budget": int(os.getenv("LLM_DAILY_BUDGET", DEFAULT_LLM_DAILY_BUDGET)),
    }
