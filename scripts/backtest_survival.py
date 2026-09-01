"""
Walk-forward backtest for the survival model.

Trains on everything up to a quarter, predicts the next one, rolls forward. A
random split would put the same customer either side of the boundary and leak the
future into the past; churn is a time process and must be evaluated as one.

Reports three things, because they fail independently:

  concordance  does it rank who leaves first correctly
  calibration  do the predicted hazards match observed frequencies
  date error   how far off the predicted churn date actually is

A model can score well on the first and be useless on the third, which is exactly
where 7.2 left it: AUC 0.877 on unseen customers, dates 196 days late. Ranking
tells you who to call; calibration is what makes a date mean anything.

Usage:
    uv run --extra model --extra warehouse python scripts/backtest_survival.py
    ... --calibrate            fit an isotonic calibrator on a held-out slice
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from model.survival import PERIOD_WEEKS, ChurnSurvivalModel, load_training_data  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")

DUCKDB = ROOT / "warehouse" / "churnguard.duckdb"

# Folds before this have almost no events -- the generator concentrates churn in
# the later window -- so training on them measures nothing.
FIRST_FOLD = "2025-Q3"


def concordance(risk: np.ndarray, time_to_event: np.ndarray, observed: np.ndarray) -> float:
    """Harrell's C: over comparable pairs, does higher risk mean an earlier event?

    A pair is comparable when we know which of the two failed first. If one is
    censored before the other's event, the order is unknown and the pair is
    skipped -- counting it either way would invent information.
    """
    concordant = tied = total = 0
    n = len(risk)
    for i in range(n):
        if not observed[i]:
            continue
        for j in range(n):
            if time_to_event[j] <= time_to_event[i]:
                continue
            total += 1
            if risk[i] > risk[j]:
                concordant += 1
            elif risk[i] == risk[j]:
                tied += 1
    return (concordant + 0.5 * tied) / total if total else float("nan")


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 5) -> pd.DataFrame:
    """Predicted hazard against observed frequency, by decile of prediction."""
    order = np.argsort(p)
    chunks = np.array_split(order, bins)
    rows = []
    for k, idx in enumerate(chunks, start=1):
        if len(idx) == 0:
            continue
        rows.append({
            "bin": k,
            "n": len(idx),
            "predicted": float(p[idx].mean()),
            "observed": float(y[idx].mean()),
        })
    out = pd.DataFrame(rows)
    out["ratio"] = out["observed"] / out["predicted"].replace(0, np.nan)
    return out


def date_error(model: ChurnSurvivalModel, test: pd.DataFrame,
               churn_dates: dict[str, pd.Timestamp]) -> dict:
    """Median absolute error of the predicted churn date, in days.

    Predicts from each churner's earliest test observation, which is the honest
    ask: how far ahead did the model see it coming.
    """
    errors, signed, beyond = [], [], 0
    for cid, group in test.groupby("customer_id"):
        actual = churn_dates.get(cid)
        if actual is None:
            continue
        row = group.sort_values("week_start").iloc[0]
        curve = model.survival_curve(row)
        crossing = model._crossing(curve, 0.50)
        if crossing is None:
            beyond += 1
            continue
        predicted = row["week_start"] + pd.Timedelta(weeks=crossing * PERIOD_WEEKS)
        delta = (predicted - actual).days   # positive = predicted too late
        errors.append(abs(delta))
        signed.append(delta)

    return {
        "n": len(errors),
        "beyond_horizon": beyond,
        "median_abs_error_days": float(np.median(errors)) if errors else float("nan"),
        "median_signed_days": float(np.median(signed)) if signed else float("nan"),
        "share_late": float(np.mean([d > 0 for d in signed])) if signed else float("nan"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest")
    ap.add_argument("--calibrate", action="store_true",
                    help="Fit an isotonic calibrator on a held-out slice of the training window")
    args = ap.parse_args()

    if not DUCKDB.exists():
        print(f"No warehouse at {DUCKDB}. Run dbt first.", file=sys.stderr)
        return 1

    df = load_training_data(DUCKDB)
    df["week_start"] = pd.to_datetime(df["week_start"])

    churn_dates = {
        cid: pd.to_datetime(g["churn_date"].iloc[0])
        for cid, g in df[df["event_observed"] == 1].groupby("customer_id")
    }

    quarters = sorted(df["observation_quarter"].unique())
    folds = [q for q in quarters if q >= FIRST_FOLD]

    print(f"walk-forward from {folds[0]}, calibration {'on' if args.calibrate else 'off'}\n")
    print(f"{'fold':9} {'train':>7} {'test':>6} {'events':>7} {'C-index':>8} "
          f"{'pred':>7} {'obs':>7} {'ratio':>6} {'date err':>9} {'signed':>8} {'%late':>6}")
    print("-" * 78)

    summary = []
    for fold in folds:
        train = df[df["observation_quarter"] < fold]
        test = df[df["observation_quarter"] == fold]
        if train.empty or test.empty or test["event_in_next_period"].sum() == 0:
            continue

        model = ChurnSurvivalModel().fit(train, calibrate=args.calibrate)

        y = test["event_in_next_period"].to_numpy()
        p = model.hazard(test)

        # Per-customer risk for concordance: their latest observation in the fold.
        latest = test.sort_values("week_start").groupby("customer_id").tail(1)
        c = concordance(
            model.hazard(latest),
            latest["weeks_to_event"].to_numpy(),
            latest["event_observed"].to_numpy().astype(bool),
        )

        de = date_error(model, test, churn_dates)
        ratio = y.mean() / p.mean() if p.mean() else float("nan")

        print(f"{fold:9} {len(train):>7,} {len(test):>6,} {int(y.sum()):>7} "
              f"{c:>8.3f} {p.mean():>7.3f} {y.mean():>7.3f} {ratio:>6.2f} "
              f"{de['median_abs_error_days']:>7.0f}d {de['median_signed_days']:>+7.0f}d "
              f"{de['share_late']*100:>5.0f}%")

        summary.append({"fold": fold, "c_index": c, "ratio": ratio,
                        "date_err": de["median_abs_error_days"],
                        "signed": de["median_signed_days"], "late": de["share_late"],
                        "beyond": de["beyond_horizon"]})

    if summary:
        s = pd.DataFrame(summary)
        print("-" * 78)
        print(f"{'mean':9} {'':>7} {'':>6} {'':>7} {s['c_index'].mean():>8.3f} "
              f"{'':>7} {'':>7} {s['ratio'].mean():>6.2f} "
              f"{s['date_err'].median():>7.0f}d {s['signed'].median():>+7.0f}d "
              f"{s['late'].mean()*100:>5.0f}%")
        print(f"\nratio = observed / predicted. Above 1 means the model underpredicts")
        print(f"hazard, so survival curves decay too slowly and dates land late.")
        print(f"\npredictions beyond the 2-year horizon: {int(s['beyond'].sum())}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
