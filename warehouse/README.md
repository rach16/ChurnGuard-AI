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

## Publishing to S3 + Athena (1.4)

The same gold models, landed in S3 as Parquet and registered in the Glue catalog,
so they are queryable from Athena without changing a single model definition.

```bash
export AWS_PROFILE=personal AWS_REGION=us-east-1
uv run --extra warehouse python warehouse/publish_to_s3.py   # export + register
uv run --extra warehouse python warehouse/verify_athena.py   # both engines agree
```

`publish_to_s3.py` derives the Athena DDL from the DuckDB schema, so the table
definitions cannot drift from the models. Generated DDL is written to
`warehouse/athena/` for review.

`verify_athena.py` runs the same six queries against both engines and fails on any
divergence — publishing to a second engine is only worth anything if the answers
match.

| | |
|---|---|
| Bucket | `s3://churnguard-warehouse-586723123589` (encrypted, public access blocked) |
| Glue database | `churnguard` |
| Published | `dim_customer`, `fct_engagement_weekly`, `fct_support_tickets`, `customer_health_score` |
| Total size | ~137 KB Parquet (ZSTD) |

Bronze and silver stay local: they are build scaffolding, not a consumer contract.

### On whether this is worth it

At this data size, no — DuckDB answers every one of these queries in milliseconds,
and Athena scanned so little that the billed cost rounds to zero. The reason to do
it is portability: it demonstrates that the models, the tests and the scoring logic
move to a cloud warehouse untouched. Treat it as a proof of portability, not an
optimisation.
