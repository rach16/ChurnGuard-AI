# 0003 — One backend with a degraded mode, not two

**Status:** Accepted · **Date:** 2026-08-30 · **Commit:** `7222ee2`

## Context

Two FastAPI applications both defined `app` and disagreed about what the product
was. `api.py` ran real RAG and LangGraph agents. `api_simple.py` served a 280-line
f-string template with a hardcoded `confidence_score` of 0.92 and made no model
call whatsoever. Roughly 200 lines of the customer detail endpoint were duplicated
between them.

`docker-compose` ran the first. The README told you to run the second. Vercel
found both and refused to build, unable to choose an entrypoint.

`api_simple.py` existed for a real reason: running the project without an API key
or a vector store. That need was legitimate; satisfying it with a second
application was not.

## Decision

Delete `api_simple.py`. Make degraded operation a **mode** of the single app.
Startup brings each subsystem up independently and records why any failed. Health
scoring reads CSV and needs no API key, so a missing `OPENAI_API_KEY` leaves the
dashboard fully working while LLM endpoints return 503 naming the exact missing
dependency.

Split liveness from readiness: `/health` always returns 200 while the process is
up but reports per-component status; `/ready` returns 503 when the service cannot
serve.

## Consequences

The readiness split matters more than the deduplication. The old `/health` was a
static string, so an ALB or ECS health check would mark a completely broken
instance healthy and route traffic to it — a failure that only appears under a
load balancer, which is to say in production and not in testing.

Consolidating also surfaced a latent bug the fork had hidden (see ADR-0004), and
incidentally fixed the Vercel build.

The cost: a single startup path is harder to reason about than two simple ones,
and the `ServiceState` object is genuine added complexity. That is the correct
trade against shipping two divergent truths.

## Alternatives considered

**Keep both, document which is which.** Rejected: the drift had already produced a
fabricated confidence score served to users as if real.

**Make `api_simple` a test fixture.** Rejected: it was reachable in production
paths, not a fixture.
