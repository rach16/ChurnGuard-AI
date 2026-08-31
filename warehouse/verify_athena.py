"""
Verify Athena returns the same answers as the local DuckDB warehouse.

Publishing the gold layer to S3 is only meaningful if the two engines agree. This
runs the same queries against both and reports any divergence.

Usage:
    AWS_PROFILE=personal python3 warehouse/verify_athena.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import duckdb

WAREHOUSE = Path(__file__).resolve().parent
DUCKDB_PATH = WAREHOUSE / "churnguard.duckdb"
BUCKET = "churnguard-warehouse-586723123589"
GLUE_DATABASE = "churnguard"

# Each query is written once and run against both engines; only the schema prefix
# differs, so the SQL itself is genuinely portable.
QUERIES = {
    "customer count": "select count(*) from {p}dim_customer",
    "churned count": "select count(*) from {p}dim_customer where is_churned",
    "at-risk (>=60)": "select count(*) from {p}customer_health_score where risk_score >= 60",
    "avg risk by segment": (
        "select segment, round(avg(risk_score), 1) from {p}customer_health_score "
        "where not is_churned group by segment order by segment"
    ),
    "total ARR at risk": (
        "select round(sum(arr)) from {p}customer_health_score "
        "where not is_churned and risk_score >= 60"
    ),
    "engagement rows": "select count(*) from {p}fct_engagement_weekly",
}


def athena(sql: str) -> list:
    """Run one query on Athena and return its rows (header stripped)."""
    qid = subprocess.run(
        ["aws", "athena", "start-query-execution",
         "--query-string", sql,
         "--query-execution-context", f"Database={GLUE_DATABASE}",
         "--result-configuration", f"OutputLocation=s3://{BUCKET}/athena-results/",
         "--query", "QueryExecutionId", "--output", "text"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    for _ in range(60):
        state = subprocess.run(
            ["aws", "athena", "get-query-execution", "--query-execution-id", qid,
             "--query", "QueryExecution.Status.State", "--output", "text"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)

    if state != "SUCCEEDED":
        reason = subprocess.run(
            ["aws", "athena", "get-query-execution", "--query-execution-id", qid,
             "--query", "QueryExecution.Status.StateChangeReason", "--output", "text"],
            capture_output=True, text=True,
        ).stdout.strip()
        raise RuntimeError(f"Athena {state}: {reason}")

    payload = json.loads(subprocess.run(
        ["aws", "athena", "get-query-results", "--query-execution-id", qid],
        capture_output=True, text=True, check=True,
    ).stdout)

    rows = payload["ResultSet"]["Rows"][1:]  # first row is the header
    return [tuple(c.get("VarCharValue") for c in r["Data"]) for r in rows]


def _cell(value) -> str:
    """Render one cell canonically so the two engines are comparable.

    Athena returns every column as a string, DuckDB returns typed values, and the
    two disagree on trailing zeros ('2234200.0' vs 2234200). Anything that parses
    as a number is compared numerically; everything else as text.
    """
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else f"{number:.6f}"


def normalise(rows) -> list:
    out = []
    for row in rows:
        row = row if isinstance(row, (list, tuple)) else (row,)
        out.append(tuple(_cell(v) for v in row))
    return out


def main() -> int:
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    failures = 0

    print(f"{'query':24} {'duckdb':>28}  {'athena':>28}")
    print("-" * 84)

    for label, template in QUERIES.items():
        duck = normalise(con.execute(template.format(p="main_gold.")).fetchall())
        athn = normalise(athena(template.format(p="")))

        ok = duck == athn
        failures += not ok
        d = str(duck[0] if len(duck) == 1 else f"{len(duck)} rows")[:28]
        a = str(athn[0] if len(athn) == 1 else f"{len(athn)} rows")[:28]
        print(f"{label:24} {d:>28}  {a:>28}  {'OK' if ok else 'MISMATCH'}")
        if not ok and len(duck) > 1:
            print(f"    duckdb: {duck}")
            print(f"    athena: {athn}")

    print("-" * 84)
    print("all queries agree" if not failures else f"{failures} query/queries diverged")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
