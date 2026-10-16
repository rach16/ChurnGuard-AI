"""
Validate the ChurnGuard dataset's referential integrity and internal consistency.

These are the contracts the generator and the Salesforce connector are supposed
to guarantee. They live in ``src/core/contracts.py`` so the sync engine can run
the same rules on a candidate snapshot before it is served; this script runs
them on a directory of CSV files with zero dependencies. The same rules become
dbt tests in the warehouse.

Exits non-zero if any check fails, so it can gate CI.

Usage:
    python3 scripts/validate_dataset.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.contracts import run_contracts_on_dir  # noqa: E402

# Same rule as src/core/datasource.py; DATA_FOLDER is the previous name.
DATA_DIR = ROOT / (os.environ.get("CHURNGUARD_DATA_DIR") or os.environ.get("DATA_FOLDER") or "data")
GOLDEN_PATH = ROOT / "golden-masters" / "churn_golden_master.csv"


def main() -> int:
    print("Validating dataset in", DATA_DIR)
    c = run_contracts_on_dir(DATA_DIR, GOLDEN_PATH)
    stats = c.stats

    print("\n" + "=" * 60)
    if c.failures:
        print(f"FAILED: {len(c.failures)} check(s) failed, {c.passed} passed, "
              f"{len(c.skipped)} skipped")
        for f in c.failures:
            print(f"  - {f}")
        return 1
    print(f"OK: all {c.passed} checks passed" +
          (f", {len(c.skipped)} skipped (no narrative corpus)" if c.skipped else ""))
    print(f"    {stats['customers']} customers, churn rate {stats['churn_rate']:.1%}, "
          f"engagement separation {stats['separation']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
