"""
The SQL health score and the Python health score must agree.

customer_health_score.sql exists so the number driving retention decisions is
queryable by anyone with a SQL client, rather than only computable by running the
API. That is only worth anything if the two implementations stay in step -- so this
test recomputes every customer both ways and fails if they drift.

Requires the warehouse to have been built:
    cd warehouse && DBT_PROFILES_DIR=$PWD dbt run
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DUCKDB_PATH = ROOT / "warehouse" / "churnguard.duckdb"

# Both implementations round to one decimal, so scores can legitimately differ by
# a rounding step. Anything larger is a real divergence.
TOLERANCE = 0.15


pytestmark = pytest.mark.skipif(
    not DUCKDB_PATH.exists(),
    reason="warehouse not built; run `cd warehouse && DBT_PROFILES_DIR=$PWD dbt run`",
)


@pytest.fixture(scope="module")
def sql_scores() -> dict:
    import duckdb

    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        rows = con.execute(
            "select customer_id, risk_score, risk_level, risk_reason, trend "
            "from main_gold.customer_health_score"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: {"risk_score": r[1], "risk_level": r[2],
                   "risk_reason": r[3], "trend": r[4]} for r in rows}


@pytest.fixture(scope="module")
def python_scores() -> dict:
    from core.health_scoring import CustomerHealthScorer

    scorer = CustomerHealthScorer(data_folder=str(ROOT / "data"))
    out = {}
    for _, row in scorer.customers.iterrows():
        scored = scorer._score_customer(row)
        if scored:
            out[scored["customer_id"]] = scored
    return out


def test_same_customers_scored(sql_scores, python_scores):
    assert set(sql_scores) == set(python_scores), (
        f"only in SQL: {sorted(set(sql_scores) - set(python_scores))[:5]}; "
        f"only in Python: {sorted(set(python_scores) - set(sql_scores))[:5]}"
    )


def test_risk_scores_agree(sql_scores, python_scores):
    drifted = [
        (cid, python_scores[cid]["risk_score"], sql_scores[cid]["risk_score"])
        for cid in sql_scores
        if abs(python_scores[cid]["risk_score"] - sql_scores[cid]["risk_score"]) > TOLERANCE
    ]
    assert not drifted, f"{len(drifted)} customers diverge beyond {TOLERANCE}: {drifted[:5]}"


@pytest.mark.parametrize("field", ["risk_level", "risk_reason", "trend"])
def test_derived_labels_agree(sql_scores, python_scores, field):
    mismatched = [
        (cid, python_scores[cid][field], sql_scores[cid][field])
        for cid in sql_scores
        if python_scores[cid][field] != sql_scores[cid][field]
    ]
    # A label can legitimately flip when a score sits exactly on a band boundary or
    # two factors tie, so allow a small number rather than demanding exact equality.
    assert len(mismatched) <= 2, f"{len(mismatched)} mismatched {field}: {mismatched[:5]}"


def test_scorer_separates_actual_churn():
    """The weighting must beat chance at ranking customers who really churned."""
    from core.health_scoring import CustomerHealthScorer

    auc = CustomerHealthScorer(data_folder=str(ROOT / "data")).scorer_auc()
    assert auc > 0.70, f"scorer AUC {auc:.3f} is too close to chance to be useful"
