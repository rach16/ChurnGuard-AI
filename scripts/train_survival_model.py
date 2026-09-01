"""
Train the churn survival model and report held-out performance.

Splits by time, never at random: a random split puts the same customer either
side of the boundary, so the model sees its own future and every metric flatters.

Usage:
    uv run --extra model --extra warehouse python scripts/train_survival_model.py
    ... --holdout-from 2026-01-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from model.survival import ChurnSurvivalModel, load_training_data

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DUCKDB = ROOT / "warehouse" / "churnguard.duckdb"
MODEL_OUT = ROOT / "models" / "survival.joblib"


def main() -> int:
    ap = argparse.ArgumentParser(description="Train the churn survival model")
    ap.add_argument("--holdout-from", default="2026-01-01",
                    help="Observations from this date onward are held out")
    ap.add_argument("--out", type=Path, default=MODEL_OUT)
    args = ap.parse_args()

    if not DUCKDB.exists():
        print(f"No warehouse at {DUCKDB}. Run dbt first.", file=sys.stderr)
        return 1

    df = load_training_data(DUCKDB)
    df["week_start"] = pd.to_datetime(df["week_start"])
    cutoff = pd.Timestamp(args.holdout_from)

    train = df[df["week_start"] < cutoff]
    test = df[df["week_start"] >= cutoff]

    print(f"train: {len(train):,} rows to {cutoff.date()}, {int(train['event_in_next_period'].sum())} events")
    print(f"test:  {len(test):,} rows from {cutoff.date()}, {int(test['event_in_next_period'].sum())} events\n")

    # Calibration is on: it is the probability we now surface, and the date it
    # used to harm is no longer produced. See ADR-0009.
    model = ChurnSurvivalModel().fit(train, calibrate=True)

    print("\nheld-out by time (the realistic case: predicting forward for known customers)")
    for k, v in model.score(test).items():
        print(f"  {k:16} {v:.4f}" if isinstance(v, float) else f"  {k:16} {v}")

    print("\nin-sample (a large gap against held-out means memorisation)")
    for k, v in model.score(train).items():
        if k in ("auc", "brier"):
            print(f"  {k:16} {v:.4f}")

    # Rows are not independent: one customer contributes ~100 near-identical
    # observations, and 156 of 183 training customers also appear after the time
    # cutoff. A held-out-customers split is the only one that shows whether the
    # model learned a pattern or a roster.
    customers = sorted(df["customer_id"].unique())
    held = set(customers[::4])
    gtrain = df[~df["customer_id"].isin(held)]
    gtest = df[df["customer_id"].isin(held)]
    grouped = ChurnSurvivalModel().fit(gtrain, calibrate=True)

    print(f"\nheld-out by customer ({len(held)} unseen customers)")
    for k, v in grouped.score(gtest).items():
        print(f"  {k:16} {v:.4f}" if isinstance(v, float) else f"  {k:16} {v}")

    model.save(args.out)
    print(f"\nsaved to {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
