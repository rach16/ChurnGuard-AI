"""
Validate the ChurnGuard dataset's referential integrity and internal consistency.

These are the contracts the generator is supposed to guarantee. They are written as
plain assertions here so the dataset can be checked with zero dependencies; the same
rules become dbt tests once the warehouse layer lands.

Exits non-zero if any check fails, so it can gate CI.

Usage:
    python3 scripts/validate_dataset.py
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "churn_analysis_docs"
GOLDEN_PATH = ROOT / "golden-masters" / "churn_golden_master.csv"

CHILD_FILES = [
    "engagement_snapshots.csv",
    "customer_interactions.csv",
    "support_tickets.csv",
    "success_stories.csv",
    "churn_analyses.csv",
]

# Files that carry a denormalised `segment` which must agree with the dimension.
SEGMENT_CARRIERS = ["customer_interactions.csv", "support_tickets.csv",
                    "success_stories.csv", "churn_analyses.csv"]

# (file, date column) pairs that must not post-date a churned customer's churn_date.
EVENT_DATE_COLUMNS = [
    ("customer_interactions.csv", "interaction_date"),
    ("support_tickets.csv", "created_date"),
    ("engagement_snapshots.csv", "week_start"),
]


def load(name: str) -> list[dict]:
    with open(DATA_DIR / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failures.append(f"{label}: {detail}")
            print(f"  FAIL  {label}  --  {detail}")


def main() -> int:
    c = Checker()
    print("Validating dataset in", DATA_DIR)

    customers = load("customers.csv")
    by_id = {r["customer_id"]: r for r in customers}

    print("\n[dimension]")
    c.check("customers.csv is non-empty", len(customers) > 0, f"{len(customers)} rows")

    dupe_ids = [k for k, n in Counter(r["customer_id"] for r in customers).items() if n > 1]
    c.check("customer_id is unique", not dupe_ids, f"{len(dupe_ids)} duplicates")

    dupe_names = [k for k, n in Counter(r["company_name"] for r in customers).items() if n > 1]
    c.check("company_name is unique", not dupe_names, f"{len(dupe_names)} duplicates: {dupe_names[:3]}")

    missing = [r["customer_id"] for r in customers
               if not r["segment"] or not r["arr"] or not r["contract_start_date"]]
    c.check("required dimension fields are populated", not missing, f"{len(missing)} rows incomplete")

    bad_label = [r["customer_id"] for r in customers
                 if (r["is_churned"] == "1") != bool(r["churn_date"])]
    c.check("churn_date is set iff is_churned", not bad_label, f"{len(bad_label)} mismatched")

    print("\n[referential integrity]")
    for name in CHILD_FILES:
        rows = load(name)
        orphans = {r["customer_id"] for r in rows if r["customer_id"] not in by_id}
        c.check(f"{name} -> customers.csv", not orphans,
                f"{len(orphans)} unknown customer_ids: {sorted(orphans)[:3]}")

    print("\n[attribute consistency]")
    for name in SEGMENT_CARRIERS:
        conflicts = {r["customer_id"] for r in load(name)
                     if r["customer_id"] in by_id and r["segment"] != by_id[r["customer_id"]]["segment"]}
        c.check(f"{name} segment matches dimension", not conflicts,
                f"{len(conflicts)} conflicting")

    arr_conflicts = {r["customer_id"] for r in load("success_stories.csv")
                     if r["customer_id"] in by_id and int(r["arr"]) != int(by_id[r["customer_id"]]["arr"])}
    c.check("success_stories arr matches dimension", not arr_conflicts, f"{len(arr_conflicts)} conflicting")

    print("\n[temporal consistency]")
    for name, col in EVENT_DATE_COLUMNS:
        late = []
        for r in load(name):
            cust = by_id.get(r["customer_id"])
            if cust and cust["churn_date"] and r[col][:10] > cust["churn_date"]:
                late.append(r["customer_id"])
        c.check(f"no {name} rows after churn_date", not late, f"{len(late)} rows post-churn")

    early = []
    for r in load("customer_interactions.csv"):
        cust = by_id.get(r["customer_id"])
        if cust and r["interaction_date"][:10] < cust["contract_start_date"]:
            early.append(r["customer_id"])
    c.check("no interactions before contract_start_date", not early, f"{len(early)} rows pre-contract")

    print("\n[business rules]")
    churned_stories = [r["customer_id"] for r in load("success_stories.csv")
                       if by_id.get(r["customer_id"], {}).get("is_churned") == "1"]
    c.check("success stories only for retained customers", not churned_stories,
            f"{len(churned_stories)} stories on churned accounts")

    analyses = load("churn_analyses.csv")
    doc_files = {p.stem for p in DOCS_DIR.glob("*.txt")}
    missing_docs = {r["doc_id"] for r in analyses} - doc_files
    orphan_docs = doc_files - {r["doc_id"] for r in analyses}
    c.check("one .txt per churn analysis", not missing_docs and not orphan_docs,
            f"{len(missing_docs)} missing, {len(orphan_docs)} orphaned")

    churn_rate = sum(r["is_churned"] == "1" for r in customers) / len(customers)
    c.check("churn rate is in a usable band (0.10-0.50)", 0.10 <= churn_rate <= 0.50,
            f"churn rate is {churn_rate:.1%}")

    print("\n[predictive signal]")
    # The whole point of the trajectory model: features must separate the label.
    # Without this, nothing downstream can learn anything.
    final_eng: dict[str, float] = {}
    for r in load("engagement_snapshots.csv"):
        final_eng[r["customer_id"]] = float(r["engagement_score"])  # last row wins

    groups = defaultdict(list)
    for cid, score in final_eng.items():
        if cid in by_id:
            groups[by_id[cid]["is_churned"]].append(score)

    churned_mean = sum(groups["1"]) / len(groups["1"]) if groups["1"] else 0.0
    retained_mean = sum(groups["0"]) / len(groups["0"]) if groups["0"] else 0.0
    separation = retained_mean - churned_mean
    c.check("retained engagement exceeds churned by >0.10", separation > 0.10,
            f"retained={retained_mean:.3f} churned={churned_mean:.3f} delta={separation:.3f}")

    meta = json.loads((DATA_DIR / "rag_metadata.json").read_text())
    c.check("rag_metadata customer count is accurate",
            meta["statistics"]["total_customers"] == len(customers),
            f"metadata says {meta['statistics']['total_customers']}, file has {len(customers)}")

    print("\n[golden dataset]")
    # The previous golden master referenced companies that existed in no data file, so
    # RAGAS was scoring retrieval against unretrievable answers. Guard against a repeat.
    with open(GOLDEN_PATH, newline="", encoding="utf-8") as fh:
        golden = list(csv.DictReader(fh))

    doc_ids = {r["doc_id"] for r in analyses}
    names = {r["company_name"] for r in customers}

    c.check("golden dataset is non-empty", len(golden) > 0, f"{len(golden)} questions")

    blank = [r for r in golden if not r["question"].strip() or not r["ground_truth"].strip()]
    c.check("no blank questions or answers", not blank, f"{len(blank)} incomplete rows")

    bad_ctx = {tok for r in golden for tok in r["expected_context"].split(",")
               if tok and tok not in by_id and tok not in doc_ids}
    c.check("expected_context references real ids", not bad_ctx,
            f"{len(bad_ctx)} unknown: {sorted(bad_ctx)[:3]}")

    named = [r for r in golden if any(n in r["question"] for n in names)]
    c.check("golden questions are grounded in real customers", len(named) > 0,
            f"only {len(named)} of {len(golden)} name a customer in customers.csv")

    print("\n" + "=" * 60)
    if c.failures:
        print(f"FAILED: {len(c.failures)} check(s) failed, {c.passed} passed")
        for f in c.failures:
            print(f"  - {f}")
        return 1
    print(f"OK: all {c.passed} checks passed")
    print(f"    {len(customers)} customers, churn rate {churn_rate:.1%}, "
          f"engagement separation {separation:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
