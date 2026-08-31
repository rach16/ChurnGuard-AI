# 0006 — DuckDB locally, Athena for portability

**Status:** Accepted · **Date:** 2026-08-30 · **Commits:** `5ce3cbb`, `9f8e2c1`

## Context

The FDE roadmap this project follows calls for a cloud data warehouse — BigQuery
in its GCP framing, so Redshift or Athena on AWS.

The entire source dataset is **1.6 MB**. Any cloud warehouse would be slower than
reading the files directly, and would add credentials, network calls and cost to a
workload that has none of those problems.

## Decision

Build the warehouse on **DuckDB**, which is embedded — a library, not a server, so
there is no daemon, no port, no credentials, and a full build takes about two
seconds.

Then publish the gold layer to **S3 as Parquet and register it in Glue** so the
same models are queryable from Athena, and verify with `verify_athena.py` that
both engines return identical answers.

## Consequences

Local development and CI need no infrastructure at all, which also means the
evaluation suite can run without a container.

The Athena path demonstrates that models, tests and scoring move to a cloud
warehouse untouched — a portability claim, proven rather than asserted. The
README says so explicitly rather than letting it read as a performance win:
Athena scanned 840 bytes on the segment query and the billed cost rounds to zero.

The honest downside: this is two systems where one would do. It is justified as a
demonstration and would not be justified in a real deployment at this scale. An
engineer who cannot say when *not* to reach for cloud infrastructure is less
useful than one who can.

## Alternatives considered

**Athena only.** Rejected: makes local development and CI depend on AWS
credentials for a 1.6 MB dataset.

**Redshift.** Rejected: a provisioned cluster is a standing cost for data that
fits in a browser tab.
