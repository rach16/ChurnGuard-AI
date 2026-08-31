"""
Prove the feature table does not leak future information.

A feature is point-in-time if its value at week T depends only on data up to week
T. The direct way to verify that is to rebuild the table as if today were an
earlier date and check the surviving rows are unchanged. If any window reaches
forward, truncating the input changes the output and these tests fail.

This matters more than it sounds. Training on features computed from a customer's
latest snapshot -- which is what src/core/health_scoring.py does, correctly, for a
dashboard -- produces a model that scores near-perfectly and predicts nothing,
because for a churned customer the latest snapshot is the week before they left.
See ADR-0008.

Rebuilds the warehouse, so it is slower than the other suites.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE = ROOT / "warehouse"
DUCKDB_PATH = WAREHOUSE / "churnguard.duckdb"

CUTOFF = "2025-06-30"

# Every column produced by feat_customer_week except the keys, which are compared on.
pytestmark = pytest.mark.skipif(
    not DUCKDB_PATH.exists(),
    reason="warehouse not built; run `cd warehouse && DBT_PROFILES_DIR=$PWD dbt run`",
)


def dbt(*args: str) -> None:
    result = subprocess.run(
        ["uv", "run", "--project", "..", "dbt", *args],
        cwd=WAREHOUSE,
        env={**__import__("os").environ, "DBT_PROFILES_DIR": str(WAREHOUSE)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"dbt {' '.join(args)} failed:\n{result.stdout[-2000:]}")


@pytest.fixture(scope="module")
def comparison():
    """Build the table twice -- truncated, then full -- and return both."""
    import duckdb

    # Truncated build: pretend the data ends at CUTOFF.
    dbt("run", "--select", "feat_customer_week", "--vars", f"{{feature_cutoff_date: {CUTOFF}}}")
    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute("create or replace table main_gold._truncated as select * from main_gold.feat_customer_week")
    con.close()

    # Full build, restoring normal state.
    dbt("run", "--select", "feat_customer_week")

    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    yield con
    con.close()

    cleanup = duckdb.connect(str(DUCKDB_PATH))
    cleanup.execute("drop table if exists main_gold._truncated")
    cleanup.close()


def test_truncated_build_is_a_subset(comparison):
    """The truncated build should contain exactly the rows on or before the cutoff."""
    late = comparison.execute(
        f"select count(*) from main_gold._truncated where week_start > '{CUTOFF}'"
    ).fetchone()[0]
    assert late == 0, f"{late} rows past the cutoff survived truncation"

    truncated, full = comparison.execute(f"""
        select (select count(*) from main_gold._truncated),
               (select count(*) from main_gold.feat_customer_week where week_start <= '{CUTOFF}')
    """).fetchone()
    assert truncated == full, (
        f"truncated build has {truncated} rows, full build has {full} on or before "
        f"the cutoff -- the row sets should be identical"
    )


def test_no_feature_changes_when_the_future_is_removed(comparison):
    """The leakage test. Every feature value must be unchanged in both builds.

    Floating-point columns are compared within a tolerance rather than exactly.
    DuckDB accumulates window aggregates in a different order depending on
    partition size, so a value sitting on a rounding boundary can land either side
    of it between builds. That is an execution-plan artifact, not leakage.

    The tolerance is one and a half quantisation units of the rounded output --
    features are emitted at round(..., 3), so a boundary flip shows as exactly
    0.001 and anything larger is a real difference. Real leakage is not marginal:
    a window that includes a customer's collapse differs from one that excludes it
    by tenths, two orders of magnitude above this bound.
    """
    QUANTISATION = 1e-3          # features are rounded to 3 decimals in SQL
    FLOAT_TOLERANCE = 1.5 * QUANTISATION

    columns = comparison.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_name = 'feat_customer_week' and table_schema = 'main_gold'"
    ).fetchall()

    predicates = []
    for name, dtype in columns:
        if name in ("customer_id", "week_start"):
            continue
        if dtype.upper() in ("DOUBLE", "FLOAT", "REAL", "DECIMAL"):
            predicates.append(
                f"(f.{name} is null) != (t.{name} is null) "
                f"or abs(coalesce(f.{name}, 0) - coalesce(t.{name}, 0)) > {FLOAT_TOLERANCE}"
            )
        else:
            predicates.append(f"f.{name} is distinct from t.{name}")

    assert predicates, "no feature columns found"

    offending = comparison.execute(f"""
        select count(*) from main_gold.feat_customer_week f
        join main_gold._truncated t
          on f.customer_id = t.customer_id and f.week_start = t.week_start
        where {' or '.join(predicates)}
    """).fetchone()[0]

    assert offending == 0, (
        f"{offending} rows changed by more than {FLOAT_TOLERANCE} when data after "
        f"{CUTOFF} was removed. A feature is reading forward in time."
    )


def test_no_observation_on_or_after_its_own_churn_date(comparison):
    """A row at or past the churn date would expose the event being predicted."""
    bad = comparison.execute("""
        select count(*) from main_gold.train_survival where weeks_to_event <= 0
    """).fetchone()[0]
    assert bad == 0, f"{bad} training rows sit on or after the customer's churn date"


def test_censored_customers_are_marked_not_labelled_negative(comparison):
    """Active customers must be censored, never asserted to be non-churners."""
    censored, events = comparison.execute("""
        select sum(case when event_observed = 0 then 1 else 0 end),
               sum(event_observed)
        from main_gold.train_survival
    """).fetchone()
    assert censored > 0 and events > 0, "expected both censored and observed rows"

    # A censored row must not carry a horizon label extending past its censoring.
    wrong = comparison.execute("""
        select count(*) from main_gold.train_survival
        where event_observed = 0 and weeks_to_event < 13 and churn_within_1q is not null
    """).fetchone()[0]
    assert wrong == 0, (
        f"{wrong} rows are censored before the horizon yet carry a 1-quarter label"
    )
