"""
Publish the gold layer to S3 as Parquet and register it in the Glue catalog.

The dbt models are unchanged -- this reads what `dbt run` already built in DuckDB
and lands it in S3, so the same definitions serve both a local warehouse and a
cloud one. That portability is the point of the exercise; at 1.6 MB of source data
DuckDB alone is faster than Athena will ever be.

Requires an authenticated AWS profile (AWS_PROFILE), never a key in this file.

Usage:
    AWS_PROFILE=personal python3 warehouse/publish_to_s3.py
    AWS_PROFILE=personal python3 warehouse/publish_to_s3.py --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

WAREHOUSE = Path(__file__).resolve().parent
DUCKDB_PATH = WAREHOUSE / "churnguard.duckdb"

DEFAULT_BUCKET = "churnguard-warehouse-586723123589"
GLUE_DATABASE = "churnguard"

# The gold models published for external query. Bronze and silver stay local:
# they are build scaffolding, not a consumer-facing contract.
GOLD_MODELS = [
    "dim_customer",
    "fct_engagement_weekly",
    "fct_support_tickets",
    "customer_health_score",
]

# DuckDB types -> Hive/Athena types. Athena has no unsigned or nested-free BOOLEAN
# quirks here, so the mapping is small and explicit rather than clever.
TYPE_MAP = {
    "BOOLEAN": "boolean",
    "TINYINT": "tinyint", "SMALLINT": "smallint",
    "INTEGER": "int", "BIGINT": "bigint", "HUGEINT": "bigint",
    "FLOAT": "float", "DOUBLE": "double",
    "VARCHAR": "string",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
}


def athena_type(duck_type: str) -> str:
    base = duck_type.upper().split("(")[0].strip()
    if base.startswith("DECIMAL"):
        return duck_type.lower()
    if base not in TYPE_MAP:
        raise ValueError(f"No Athena mapping for DuckDB type {duck_type!r}")
    return TYPE_MAP[base]


def run(cmd: list[str], dry_run: bool = False) -> str:
    if dry_run:
        print(f"    [dry-run] {' '.join(cmd[:6])} ...")
        return ""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed:\n{result.stderr.strip()}")
    return result.stdout


def export_and_upload(con, model: str, bucket: str, tmp: Path, dry_run: bool) -> list[tuple]:
    """Write one gold model to Parquet, upload it, and return its column schema."""
    schema = con.execute(f"describe main_gold.{model}").fetchall()
    columns = [(r[0], athena_type(r[1])) for r in schema]

    local = tmp / f"{model}.parquet"
    con.execute(
        f"copy main_gold.{model} to '{local}' (format parquet, compression zstd)"
    )
    size_kb = local.stat().st_size / 1024

    dest = f"s3://{bucket}/gold/{model}/{model}.parquet"
    run(["aws", "s3", "cp", str(local), dest], dry_run)
    print(f"  {model:24} {len(columns):2} cols  {size_kb:7.1f} KB  -> {dest}")
    return columns


def ddl_for(model: str, columns: list[tuple], bucket: str) -> str:
    cols = ",\n  ".join(f"`{name}` {typ}" for name, typ in columns)
    return (
        f"CREATE EXTERNAL TABLE IF NOT EXISTS `{GLUE_DATABASE}`.`{model}` (\n"
        f"  {cols}\n"
        f")\nSTORED AS PARQUET\n"
        f"LOCATION 's3://{bucket}/gold/{model}/'\n"
        f"TBLPROPERTIES ('parquet.compression'='ZSTD')"
    )


def athena_query(sql: str, bucket: str, dry_run: bool) -> str:
    """Start an Athena query and return its execution id."""
    out = run([
        "aws", "athena", "start-query-execution",
        "--query-string", sql,
        "--result-configuration", f"OutputLocation=s3://{bucket}/athena-results/",
        "--query", "QueryExecutionId", "--output", "text",
    ], dry_run)
    return out.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish the gold layer to S3 + Glue")
    ap.add_argument("--bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DUCKDB_PATH.exists():
        print(f"No warehouse at {DUCKDB_PATH}. Run `dbt run` first.", file=sys.stderr)
        return 1

    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)

    print(f"Publishing {len(GOLD_MODELS)} gold models to s3://{args.bucket}/gold/")
    schemas = {}
    with tempfile.TemporaryDirectory() as td:
        for model in GOLD_MODELS:
            schemas[model] = export_and_upload(con, model, args.bucket, Path(td), args.dry_run)

    print(f"\nRegistering in Glue database '{GLUE_DATABASE}'")
    create_glue_database(args.dry_run)

    ddl_dir = WAREHOUSE / "athena"
    ddl_dir.mkdir(exist_ok=True)
    for model, columns in schemas.items():
        ddl = ddl_for(model, columns, args.bucket)
        (ddl_dir / f"{model}.sql").write_text(ddl + ";\n", encoding="utf-8")
        qid = athena_query(ddl, args.bucket, args.dry_run)
        print(f"  {model:24} DDL written, athena query {qid or '(dry-run)'}")

    print(f"\nDDL saved to {ddl_dir.relative_to(WAREHOUSE.parent)}/")
    return 0


def create_glue_database(dry_run: bool) -> None:
    """Glue has no create-if-not-exists, so tolerate the already-exists case."""
    if dry_run:
        print(f"    [dry-run] aws glue create-database {GLUE_DATABASE}")
        return

    result = subprocess.run(
        ["aws", "glue", "create-database", "--database-input",
         f'{{"Name":"{GLUE_DATABASE}","Description":"ChurnGuard gold layer, published from dbt"}}'],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  created database '{GLUE_DATABASE}'")
    elif "AlreadyExistsException" in result.stderr:
        print(f"  database '{GLUE_DATABASE}' already exists")
    else:
        raise RuntimeError(result.stderr.strip())


if __name__ == "__main__":
    sys.exit(main())
