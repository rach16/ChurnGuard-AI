# ChurnGuard Warehouse

A dbt project over the ChurnGuard dataset, laid out bronze / silver / gold.

## Why DuckDB

DuckDB is embedded — a library, not a server. There is no daemon, no port and no
credentials; the warehouse is a single file at `warehouse/churnguard.duckdb`. At
1.6 MB of source data this is also simply the right tool: the full build takes
about two seconds.

## Layers

| Layer  | Materialisation | Purpose |
|--------|-----------------|---------|
| bronze | view            | Source CSVs typed, nothing reshaped, so anything downstream can be traced back to what arrived |
| silver | table           | Conformed keys and casts; one row per entity per grain |
| gold   | table           | What consumers query: `dim_customer`, `fct_engagement_weekly`, `fct_support_tickets`, `customer_health_score` |

## The point of `customer_health_score`

Risk scoring used to live only inside `src/core/health_scoring.py`, so the number
driving retention decisions could not be inspected, joined to, or recomputed by
anyone without running the API. It is now a gold model — queryable with any SQL
client, and joinable to the facts that produced it.

Both implementations are kept in step by `tests/test_warehouse_parity.py`, which
recomputes all 200 customers each way and fails on divergence beyond rounding.
Weights live in `dbt_project.yml` vars and mirror `RISK_WEIGHTS` in the Python.

## Running it

```bash
cd warehouse
export DBT_PROFILES_DIR=$PWD
uv run --project .. dbt run     # build   (~2s)
uv run --project .. dbt test    # 52 tests
```

Then from the repo root:

```bash
uv run --extra dev --extra warehouse pytest tests/test_warehouse_parity.py
```

## Querying it

```bash
uv run python -c "
import duckdb
con = duckdb.connect('warehouse/churnguard.duckdb', read_only=True)
print(con.execute('''
    select segment, count(*) n, round(avg(risk_score),1) avg_risk
    from main_gold.customer_health_score
    where not is_churned group by segment order by avg_risk desc
''').fetchall())"
```

## Tests

Source-level contracts (uniqueness, not-null, accepted values, referential
integrity) mirror the assertions in `scripts/validate_dataset.py` — those were
written as contracts precisely so they could become dbt tests. Singular tests in
`tests/` cover score bounds, band-label consistency, engagement grain, and that no
ticket post-dates its customer's churn.
