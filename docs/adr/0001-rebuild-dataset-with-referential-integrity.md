# 0001 — Rebuild the dataset with referential integrity

**Status:** Accepted · **Date:** 2026-08-30 · **Commit:** `c3ad4e9`

## Context

The generator built each file's company names independently, as a random stem plus
a random suffix, and the suffix pools differed per file. Interactions drew from
`{Systems, Solutions, Technologies, Corp}`; tickets from `{Inc, LLC, Corp}`.

The result was not a dataset. 157 distinct "companies" existed across four files,
of which only 16 appeared in both interactions and tickets, and 8 in both analyses
and interactions. 89 of 157 names carried *conflicting segments* — the same
company was SMB in one file and Enterprise in another. The only churn labels lived
in a 25-row legacy export that joined to nothing at all.

Anything built on top of this — a warehouse, a model, an evaluation — would have
been measuring noise.

## Decision

Rewrite the generator entity-first. `customers.csv` becomes the dimension; every
other table references `customer_id` and inherits segment and ARR from it rather
than re-rolling them.

Give each customer a latent health trajectory and derive engagement, ticket
volume, CSAT, adoption **and the churn label** from it, so the published features
genuinely predict the target.

## Consequences

Five features now separate churn at AUC 0.72–0.80, with tenure deliberately
uninformative at 0.51 so feature selection is a real exercise rather than theatre.
Referential integrity is enforced by 28 contract checks that later ported almost
line-for-line into dbt tests.

The cost: every derived artifact built against the old data was invalidated at
once — the golden evaluation set, the RAG corpus and the committed metrics all had
to be rebuilt. That cascade was unavoidable, but it was also the point. The
alternative was continuing to build on data where nothing joined.

## Alternatives considered

**Patch the join keys in place.** Rejected: the names were generated
independently, so there was no correct mapping to recover.

**Use a public churn dataset.** Rejected: available sets are B2C telco, carry no
documents for RAG, and would contradict the B2B SaaS framing.
