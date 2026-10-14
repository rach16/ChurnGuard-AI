"""
The survival model must not depend on the order its training rows arrive in.

It did. Early stopping holds out a random slice of rows by position, so the same
table in a different order trained a different model, and each image build read
the table in whatever order DuckDB returned it. The live likelihood and exposure
figures moved on every deploy with no code or data change.
"""

from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
DUCKDB_PATH = ROOT / "warehouse" / "churnguard.duckdb"

pytestmark = pytest.mark.skipif(
    not DUCKDB_PATH.exists(),
    reason="warehouse not built; run `cd warehouse && DBT_PROFILES_DIR=$PWD dbt build`",
)


def test_shuffled_training_rows_give_the_same_model():
    pytest.importorskip("sklearn")
    from model.survival import ChurnSurvivalModel, load_training_data

    df = load_training_data(DUCKDB_PATH)
    shuffled = df.sample(frac=1, random_state=7).reset_index(drop=True)
    latest = df.groupby("customer_id").tail(1)

    ordered = ChurnSurvivalModel().fit(df, calibrate=True).churn_probabilities(latest)
    reordered = ChurnSurvivalModel().fit(shuffled, calibrate=True).churn_probabilities(latest)

    np.testing.assert_array_equal(ordered, reordered)
