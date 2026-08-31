# 0005 — Keep scoring in both Python and SQL, and enforce parity

**Status:** Accepted · **Date:** 2026-08-30 · **Commit:** `5ce3cbb`

## Context

Retention risk scoring lived only inside `src/core/health_scoring.py`. The number
that drives retention decisions could not be inspected, joined to, or recomputed
by anyone who was not running the API — no analyst could check it, and no query
could combine it with anything else.

Duplicating logic across two languages is ordinarily a mistake.

## Decision

Port the scoring into `warehouse/models/gold/customer_health_score.sql` as a gold
model, and accept the duplication **on the condition that something enforces
agreement**. `tests/test_warehouse_parity.py` recomputes all 200 customers both
ways and fails on any divergence beyond rounding.

Weights live in `dbt_project.yml` vars mirroring `RISK_WEIGHTS` in the Python, so
the numbers a reviewer would argue with are named in one obvious place in each.

## Consequences

The score is now queryable by any SQL client and joinable to the facts that
produced it. All 200 customers agree to within 0.1 (pure rounding), and the test
also asserts the scorer still separates real churners at AUC 0.79 — so a
refactor that quietly turns scoring into noise fails the build.

The cost is real: two implementations must be changed together, and the parity
test is the only thing making that safe. If the test is ever deleted or skipped,
this decision becomes a liability rather than a feature.

## Alternatives considered

**SQL only, API queries the warehouse.** The better end state, and where this
should go. Rejected for now because it couples the API to a warehouse build,
which Stage 2 deployment work has not yet accounted for.

**Python only.** Rejected: leaves the number unqueryable, which was the problem.
