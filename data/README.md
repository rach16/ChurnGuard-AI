# Data

Every file here is **generated**, not collected. `scripts/generate_synthetic_rag_data.py`
produces all of it deterministically from a fixed seed and a fixed `AS_OF_DATE`, so
two runs are byte-identical.

```bash
python3 scripts/generate_synthetic_rag_data.py   # regenerate
python3 scripts/validate_dataset.py              # 28 contract checks
```

## Shape

`customers.csv` is the dimension. Every other table references `customer_id` and
inherits segment and ARR from it rather than carrying its own copy.

| File | Grain | Rows |
|---|---|---|
| `customers.csv` | one per customer | 200 (71 churned, 35.5%) |
| `engagement_snapshots.csv` | customer × week | 15,840 |
| `customer_interactions.csv` | one per touchpoint | 2,953 |
| `support_tickets.csv` | one per ticket | 1,311 |
| `churn_analyses.csv` | one per analysis | 111 |
| `success_stories.csv` | one per retained account | 60 |
| `churn_analysis_docs/` | one `.txt` per analysis | 111 |

`churned_customers_cleaned.csv` is a legacy 25-row export in a different schema
(`Account Name`, Salesforce-shaped). It joins to nothing and is kept only as a
second source system to reconcile against.

## Why the generator is the way it is

Each customer carries a latent health trajectory. Engagement, ticket volume, CSAT,
adoption **and the churn label** are all derived from it, so the published features
genuinely predict the target — five separate at AUC 0.72–0.80, with tenure
deliberately uninformative at 0.51 so feature selection is a real exercise.

An earlier version built every file's company names independently from a random stem
plus a random suffix, with different suffix pools per file. That produced 157 "companies"
of which 16 appeared in two tables, and 89 carried conflicting segments. Nothing joined,
so nothing downstream could be trusted. See ADR-0001.

## Contracts

`scripts/validate_dataset.py` asserts referential integrity, attribute consistency,
temporal ordering (no event post-dates its customer's churn), business rules, and that
the features still separate the label. It exits non-zero, so it can gate CI, and the
same rules exist as dbt tests in `warehouse/`.
