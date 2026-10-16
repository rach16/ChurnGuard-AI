"""
The data contracts: what every dataset ChurnGuard serves must satisfy.

Written once, run in two places. ``scripts/validate_dataset.py`` runs them on a
directory of CSV files (CI, and the laptop pull). The sync engine (ADR-0012
section 5, 1.10.2) runs them on a candidate snapshot before anything is served,
so a sync that breaks a contract is kept for diagnosis and never promoted.

The rows are the CSV shape: every value a string, missing is ``""``. The engine
converts its tables with ``connectors.salesforce.as_csv_rows`` first, so a rule
written against the files means the same thing against a snapshot. Plain
assertions with no dependencies, by design; the same rules exist as dbt tests
in the warehouse.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Optional

CHILD_TABLES = [
    "engagement_snapshots",
    "customer_interactions",
    "support_tickets",
    "success_stories",
    "churn_analyses",
]

# Tables that carry a denormalised `segment` which must agree with the dimension.
SEGMENT_CARRIERS = ["customer_interactions", "support_tickets", "success_stories", "churn_analyses"]

# (table, date column) pairs that must not post-date a churned customer's churn_date.
EVENT_DATE_COLUMNS = [
    ("customer_interactions", "interaction_date"),
    ("support_tickets", "created_date"),
    ("engagement_snapshots", "week_start"),
]

CORPUS_ONLY = (
    "one .txt per churn analysis",
    "rag_metadata customer count is accurate",
    "golden dataset is non-empty",
    "no blank questions or answers",
    "expected_context references real ids",
    "golden questions are grounded in real customers",
)


class Checker:
    def __init__(self, log: Callable[[str], None] = print) -> None:
        self.failures: list[str] = []
        self.passed_labels: list[str] = []
        self.skipped: list[str] = []
        self.log = log

    @property
    def passed(self) -> int:
        return len(self.passed_labels)

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed_labels.append(label)
            self.log(f"  PASS  {label}")
        else:
            self.failures.append(f"{label}: {detail}")
            self.log(f"  FAIL  {label}  --  {detail}")

    def skip(self, label: str, reason: str) -> None:
        self.skipped.append(label)
        self.log(f"  SKIP  {label}  --  {reason}")

    def summary(self) -> dict:
        """What a run row records: counts and the failures, never the data."""
        return {"passed": self.passed, "failed": len(self.failures),
                "skipped": len(self.skipped), "failures": list(self.failures)}


def run_contracts(tables: dict[str, list[dict]], *, docs_dir: Optional[Path] = None,
                  rag_metadata: Optional[dict] = None, golden: Optional[list[dict]] = None,
                  log: Callable[[str], None] = print) -> Checker:
    """Every contract against in-memory CSV-shaped tables.

    ``docs_dir``, ``rag_metadata`` and ``golden`` are the narrative corpus the
    synthetic generator writes. A connector source has none, and those checks
    are reported as skipped: not failed and not passed. They return once that
    source's narrative is indexed (4.6).
    """
    c = Checker(log)
    has_corpus = rag_metadata is not None

    def load(name: str) -> list[dict]:
        return tables.get(name, [])

    customers = load("customers")
    by_id = {r["customer_id"]: r for r in customers}

    log("\n[dimension]")
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

    log("\n[referential integrity]")
    for name in CHILD_TABLES:
        rows = load(name)
        orphans = {r["customer_id"] for r in rows if r["customer_id"] not in by_id}
        c.check(f"{name}.csv -> customers.csv", not orphans,
                f"{len(orphans)} unknown customer_ids: {sorted(orphans)[:3]}")

    log("\n[attribute consistency]")
    for name in SEGMENT_CARRIERS:
        conflicts = {r["customer_id"] for r in load(name)
                     if r["customer_id"] in by_id and r["segment"] != by_id[r["customer_id"]]["segment"]}
        c.check(f"{name}.csv segment matches dimension", not conflicts,
                f"{len(conflicts)} conflicting")

    arr_conflicts = {r["customer_id"] for r in load("success_stories")
                     if r["customer_id"] in by_id and int(r["arr"]) != int(by_id[r["customer_id"]]["arr"])}
    c.check("success_stories arr matches dimension", not arr_conflicts, f"{len(arr_conflicts)} conflicting")

    log("\n[temporal consistency]")
    for name, col in EVENT_DATE_COLUMNS:
        late = []
        for r in load(name):
            cust = by_id.get(r["customer_id"])
            if cust and cust["churn_date"] and r[col][:10] > cust["churn_date"]:
                late.append(r["customer_id"])
        c.check(f"no {name}.csv rows after churn_date", not late, f"{len(late)} rows post-churn")

    early = []
    for r in load("customer_interactions"):
        cust = by_id.get(r["customer_id"])
        if cust and r["interaction_date"][:10] < cust["contract_start_date"]:
            early.append(r["customer_id"])
    c.check("no interactions before contract_start_date", not early, f"{len(early)} rows pre-contract")

    log("\n[business rules]")
    churned_stories = [r["customer_id"] for r in load("success_stories")
                       if by_id.get(r["customer_id"], {}).get("is_churned") == "1"]
    c.check("success stories only for retained customers", not churned_stories,
            f"{len(churned_stories)} stories on churned accounts")

    analyses = load("churn_analyses")
    if has_corpus and docs_dir is not None:
        doc_files = {p.stem for p in docs_dir.glob("*.txt")}
        missing_docs = {r["doc_id"] for r in analyses} - doc_files
        orphan_docs = doc_files - {r["doc_id"] for r in analyses}
        c.check("one .txt per churn analysis", not missing_docs and not orphan_docs,
                f"{len(missing_docs)} missing, {len(orphan_docs)} orphaned")
    else:
        c.skip("one .txt per churn analysis", "source has no narrative corpus")

    churn_rate = (sum(r["is_churned"] == "1" for r in customers) / len(customers)) if customers else 0.0
    c.check("churn rate is in a usable band (0.10-0.50)", 0.10 <= churn_rate <= 0.50,
            f"churn rate is {churn_rate:.1%}")

    log("\n[predictive signal]")
    # The whole point of the trajectory model: features must separate the label.
    # Without this, nothing downstream can learn anything.
    final_eng: dict[str, float] = {}
    for r in load("engagement_snapshots"):
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

    if has_corpus:
        c.check("rag_metadata customer count is accurate",
                rag_metadata["statistics"]["total_customers"] == len(customers),
                f"metadata says {rag_metadata['statistics']['total_customers']}, file has {len(customers)}")

        log("\n[golden dataset]")
        # The previous golden master referenced companies that existed in no data file, so
        # RAGAS was scoring retrieval against unretrievable answers. Guard against a repeat.
        golden = golden or []
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
    else:
        log("\n[golden dataset]")
        for label in CORPUS_ONLY[1:]:
            c.skip(label, "source has no narrative corpus")

    c.stats = {"customers": len(customers), "churn_rate": churn_rate, "separation": separation}  # type: ignore[attr-defined]
    return c


def run_contracts_on_dir(data_dir: Path, golden_path: Optional[Path] = None,
                         log: Callable[[str], None] = print) -> Checker:
    """The contracts against a directory of CSV files, as the script runs them."""
    def load(name: str) -> list[dict]:
        with open(data_dir / f"{name}.csv", newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    tables = {name: load(name) for name in ["customers", *CHILD_TABLES]}
    meta_path = data_dir / "rag_metadata.json"
    rag_metadata = json.loads(meta_path.read_text()) if meta_path.exists() else None
    golden = None
    if rag_metadata is not None and golden_path is not None:
        with open(golden_path, newline="", encoding="utf-8") as fh:
            golden = list(csv.DictReader(fh))
    return run_contracts(tables, docs_dir=data_dir / "churn_analysis_docs",
                         rag_metadata=rag_metadata, golden=golden, log=log)
