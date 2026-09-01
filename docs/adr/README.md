# Architecture Decision Records

Each record captures one decision, the situation that forced it, and what it cost.
They are written when the decision is made, because the reasoning is what decays —
the code shows *what* was chosen, never *why* the alternative was rejected.

| # | Decision | Status |
|---|---|---|
| [0001](0001-rebuild-dataset-with-referential-integrity.md) | Rebuild the dataset with referential integrity | Accepted |
| [0002](0002-derive-golden-set-from-data.md) | Derive the golden evaluation set from data, not an LLM | Accepted |
| [0003](0003-single-backend-with-degraded-mode.md) | One backend with a degraded mode, not two | Accepted |
| [0004](0004-delete-stale-knowledge-graph.md) | Delete the stale knowledge graph rather than load it | Accepted |
| [0005](0005-scoring-in-both-python-and-sql.md) | Keep scoring in both Python and SQL, enforce parity | Accepted |
| [0006](0006-duckdb-locally-athena-for-portability.md) | DuckDB locally, Athena for portability | Accepted |
| [0007](0007-retract-the-94-7-percent-claim.md) | Retract the 94.7% accuracy claim publicly | Accepted |
| [0008](0008-predict-churn-date-with-survival-analysis.md) | Predict a churn date with survival analysis | Superseded in part by 0009 |
| [0009](0009-predict-a-horizon-band-not-a-date.md) | Predict a likelihood band over a horizon, not a date | Accepted |
