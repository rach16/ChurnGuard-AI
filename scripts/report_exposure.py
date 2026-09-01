#!/usr/bin/env python3
"""
Print the cost-of-inaction figures behind docs/COST_OF_INACTION.md.

Every number in that document comes from here, so the document can be regenerated
and audited rather than maintained by hand. No API key, no network, no cost.

    uv run --extra warehouse python scripts/report_exposure.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402

from core.exposure import CALIBRATION_UNDERSTATEMENT, ExposureModel  # noqa: E402
from core.health_scoring import CustomerHealthScorer  # noqa: E402
from core.plays import PlaybookEngine  # noqa: E402
from model.survival import ChurnSurvivalModel  # noqa: E402

LATEST_ROW_PER_CUSTOMER = """
    select * from main_gold.train_survival
    qualify row_number() over (partition by customer_id order by week_start desc) = 1
"""


def money(x: float) -> str:
    return f"${x:,.0f}"


def main() -> int:
    model_path = ROOT / "models" / "survival.joblib"
    warehouse = ROOT / "warehouse" / "churnguard.duckdb"

    if not model_path.exists():
        print(f"No model at {model_path}. Run scripts/train_survival_model.py first.")
        return 1
    if not warehouse.exists():
        print(f"No warehouse at {warehouse}. Run dbt build first.")
        return 1

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        frame = con.execute(LATEST_ROW_PER_CUSTOMER).df()
    finally:
        con.close()

    scored = CustomerHealthScorer(data_folder=str(ROOT / "data")).score_active_customers()
    exposure = ExposureModel(
        ChurnSurvivalModel.load(model_path), PlaybookEngine(str(ROOT / "data"))
    )
    book = exposure.for_book(frame, scored, top_n=10)

    print("Cost of inaction — one quarter\n")
    print(f"  Active accounts        {book.accounts}")
    print(f"  ARR under management   {money(book.total_arr)}")
    print(f"  Expected loss          {money(book.expected_loss)}"
          f"  ({100 * book.expected_loss / book.total_arr:.2f}% of ARR)")
    print(f"  Upper bound            {money(book.expected_loss_upper)}"
          f"  (x{CALIBRATION_UNDERSTATEMENT} measured understatement, ADR-0009)")
    print(f"  Recoverable (upper)    {money(book.recoverable)}"
          f"  ({100 * book.recoverable / book.expected_loss:.1f}% of expected loss)")
    print(f"  Held by worst 10       {book.concentration_top_10:.1%} of expected loss\n")

    print(f"  {'Band':<11}{'Accts':>6}{'ARR':>14}{'Expected loss':>16}{'Share':>8}")
    for b in book.by_band:
        print(f"  {b.band:<11}{b.accounts:>6}{money(b.arr):>14}"
              f"{money(b.expected_loss):>16}{b.share_of_loss:>7.1%}")

    print(f"\n  {'Account':<28}{'Band':<11}{'ARR':>10}{'Loss':>10}{'Recover':>10}  Play")
    for e in book.top_accounts:
        print(f"  {e.name[:27]:<28}{e.band:<11}{money(e.arr):>10}"
              f"{money(e.expected_loss):>10}{money(e.recoverable or 0):>10}"
              f"  {(e.play or '—')[:34]}")

    print(f"\n{book.to_dict()['basis']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
