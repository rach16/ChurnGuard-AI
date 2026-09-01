# 0010 — API key auth and per-process rate limiting

**Status:** Accepted · **Date:** 2026-09-01

## Context

The API served every route to anyone who could reach it. That is correct for a
localhost demo and unacceptable for anything else, and the gap is the kind that
gets discovered during a customer's security review rather than by us.

Three exposures, with different costs:

1. **No authentication.** Anyone reaching the port gets the full customer book,
   including per-account risk and ARR.
2. **No rate limit.** A loop against `/book/exposure` re-scores 129 accounts per
   request.
3. **No cost ceiling.** The LLM routes had no cap on input or output, so a single
   malformed question could run a completion to the model's full context.

## Decision

A single middleware doing key authentication and sliding-window rate limiting,
plus an output token cap applied inside the provider factory.

**Authentication is enabled by the presence of `API_KEYS`, and disabled by its
absence.** A required flag that defaults to off is the same thing with an extra
step; a required flag that defaults to on breaks every local checkout. Instead the
mode is *visible*: a WARNING at startup, and `security` in `/health` reporting
`auth: disabled`. An operator can see it is off without holding a key.

**Probes are exempt** — `/`, `/health`, `/ready`, `/docs`, `/redoc`,
`/openapi.json`. An ALB health check cannot present a credential, and requiring
one turns every deployment into a crash loop that reads as an application fault.
`/ready` exposing component status to an unauthenticated caller is a deliberate
trade: it is operational detail, not customer data, and it is what makes the
degraded mode diagnosable.

**The token cap lives in `core/llm.py`, not at the call sites.** Six places
construct a chat model. Capping at each one means the seventh forgets. Providers
name the parameter differently, so each declares its own (`max_tokens`;
`num_predict` for Ollama) — a wrong name is accepted silently and caps nothing.

**Rate limiting is a sliding window.** A fixed window lets a caller send double
the limit across the boundary, which is the first thing anyone testing a limiter
tries.

## Consequences

**The limit is per process and therefore per replica.** Behind two ECS tasks the
effective limit is twice the configured value. This is stated in `/health`, in
`.env.example`, and in the module docstring, because a rate limit believed to be
global and silently isn't is worse than none at all. The honest fix is shared
state — ElastiCache, or the load balancer's own limiter — not a cleverer local
algorithm. Deferred until there is more than one replica.

Middleware is installed after CORS so it runs inside it, meaning a browser can
read a 401 rather than reporting an opaque network error.

401 and 403 are distinguished: 401 means "authenticate", 403 means "you did, and
the key is not valid". Collapsing them makes a misconfigured client
indistinguishable from a revoked key.

Existing clients are unaffected. The frontend sends no key, `API_KEYS` is unset by
default, and every current route behaves exactly as before.

## What this is not

Not a substitute for a load balancer, WAF, or identity provider. Static shared
keys have no rotation story, no per-user attribution, and no revocation beyond
editing an environment variable and restarting. For a real deployment this is the
floor, and Cognito (2.6) is the ceiling.

## A bug found by its own test

Callers were first identified by the first eight characters of their key. The
tests used `test-key-alpha` and `test-key-beta`, which collide on that prefix, so
one key exhausted the other's quota. Keys issued from a common generator routinely
share a prefix, so this would have shipped as a real fault.

Caller identity is now `sha256(key)[:16]`, which also keeps the key out of logs
and out of the limiter's dict. The regression test asserts the two example keys
map to different identifiers.
