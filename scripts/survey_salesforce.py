"""
Every figure in docs/SITE_SURVEY_SALESFORCE.md, measured from one Salesforce pull.

Reads the canonical CSVs in data/salesforce/ (tracked) and the raw pull the connector
saves with --raw-dump (gitignored, because it is a full copy of the org). Recreate the
raw pull with:

    uv run --extra salesforce python -m src.connectors.salesforce \\
        --out data/salesforce --as-of 2026-10-04 \\
        --raw-dump data/salesforce/raw/raw_2026-10-04.json

Usage:
    uv run python scripts/survey_salesforce.py [--raw data/salesforce/raw/raw_2026-10-04.json]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.connectors.salesforce import normalise_name  # noqa: E402

OUT = ROOT / "data" / "salesforce"
FIELDS = ["ARR__c", "Seats__c", "Segment__c", "Region__c", "Industry", "Customer_Status__c",
          "Contract_Start_Date__c", "Contract_End_Date__c", "Active_Users__c",
          "Feature_Adoption__c", "Last_Login_Date__c", "NPS_Score__c"]


def _d(x) -> date | None:
    return date.fromisoformat(str(x)[:10]) if x else None


def _pct(n: int, t: int) -> str:
    return f"{n}/{t} ({100 * n / t:.0f}%)" if t else "0/0"


def _rows(name: str) -> list[dict]:
    with open(OUT / f"{name}.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=OUT / "raw" / "raw_2026-10-04.json")
    args = ap.parse_args()

    raw = json.loads(args.raw.read_text())
    report = json.loads((OUT / "ingest_report.json").read_text())
    accounts = raw["accounts"]
    by_number = {a.get("AccountNumber"): a for a in accounts}
    customers = _rows("customers")
    cust_raw = [by_number[c["customer_id"]] for c in customers if c["customer_id"] in by_number]
    tickets, interactions = _rows("support_tickets"), _rows("customer_interactions")
    engagement = _rows("engagement_snapshots")
    as_of = _d(report["as_of"])

    opps = defaultdict(list)
    for o in raw["opportunities"]:
        opps[o.get("AccountId")].append(o)

    print(f"as_of {as_of}")
    print("objects pulled", {k: len(v) for k, v in raw.items()})
    print("account number series", dict(Counter(
        (a.get("AccountNumber") or "none")[:5] for a in accounts).most_common(3)))
    print(f"customers {len(customers)}, prospects excluded {report['prospects_excluded']}")

    print("\n-- D1 completeness, customers --")
    for f in FIELDS:
        print(f"  {f:24} {_pct(sum(1 for a in cust_raw if a.get(f) not in (None, '')), len(cust_raw))}")
    non_seeded = [c for c in customers if not c["customer_id"].startswith("ACC-")]
    print(f"  Salesforce sample accounts written as customers: {len(non_seeded)}")
    arr = sum(int(c["arr"]) for c in customers)
    active = sum(int(c["arr"]) for c in customers if c["is_churned"] == "0")
    print(f"  ARR total {arr:,}, active {active:,}")

    print("\n-- D2 / V1 engagement history --")
    weeks = Counter(e["customer_id"] for e in engagement)
    print(f"  rows {len(engagement)}, customers with a row {_pct(len(weeks), len(customers))}, "
          f"max weeks per customer {max(weeks.values())}")

    print("\n-- D3 churn label and dates --")
    churned = [c for c in customers if c["is_churned"] == "1"]
    print(f"  churned {len(churned)}, censored {len(customers) - len(churned)}")
    print("  label source", {k.split(':')[1]: v for k, v in report["decisions"].items()
                             if k.startswith("churn_label_source")})
    gaps = []
    for a in cust_raw:
        if (a.get("Customer_Status__c") or "").lower() != "churned":
            continue
        lost = sorted(_d(o["CloseDate"]) for o in opps[a["Id"]] if o["IsClosed"] and not o["IsWon"])
        if a.get("Churn_Date__c") and lost:
            gaps.append((_d(a["Churn_Date__c"]) - lost[-1]).days)
    print(f"  Churn_Date__c minus last lost close date: n={len(gaps)}, "
          f"median {statistics.median(gaps) if gaps else '-'}, nonzero {sum(1 for g in gaps if g)}")
    dates = sorted(_d(c["churn_date"]) for c in churned)
    print(f"  churn dates {dates[0]} .. {dates[-1]}, by year {dict(Counter(d.year for d in dates))}")
    lost = [o for o in raw["opportunities"] if o["IsClosed"] and not o["IsWon"]]
    print(f"  loss reason on Closed Lost {_pct(sum(1 for o in lost if o.get('Loss_Reason__c')), len(lost))}")
    stale = sum(1 for o in raw["opportunities"] if not o["IsClosed"] and _d(o["CloseDate"]) < as_of)
    print(f"  open opportunities past close date {stale}")
    status = Counter(a.get("Customer_Status__c") for a in cust_raw)
    print(f"  status on customers {dict(status)}")

    print("\n-- D4 support --")
    fallback = {c["CaseNumber"] for c in raw["cases"] if not c.get("Legacy_Created_Date__c")}
    load_dated = [t for t in tickets if t["ticket_id"] in fallback]
    print(f"  cases {len(raw['cases'])}, tickets kept {len(tickets)}, "
          f"CSAT present {_pct(sum(1 for t in tickets if t['csat_score']), len(tickets))}")
    print(f"  distinct CreatedDate values {len({_d(c['CreatedDate']) for c in raw['cases']})}")
    print(f"  tickets dated by load date {len(load_dated)} on {len({t['customer_id'] for t in load_dated})} "
          f"accounts, dates {dict(Counter(t['created_date'] for t in load_dated))}")
    prospects = [a for a in accounts if a.get("Customer_Status__c") == "Prospect"]
    on_case = Counter(c.get("AccountId") for c in raw["cases"])
    print(f"  prospects with a case {_pct(sum(1 for a in prospects if on_case[a['Id']]), len(prospects))}, "
          f"with a Closed Won opp {sum(1 for a in prospects if any(o['IsWon'] for o in opps[a['Id']]))}")

    print("\n-- D5 interactions --")
    with_text = sum(1 for t in raw["tasks"] if t.get("Description"))
    print(f"  tasks {len(raw['tasks'])}, with description {_pct(with_text, len(raw['tasks']))}, "
          f"subtypes {dict(Counter(t.get('TaskSubtype') for t in raw['tasks']))}")
    span = sorted(i["interaction_date"] for i in interactions if i["interaction_date"])
    print(f"  interaction span {span[0]} .. {span[-1]}, customers with one "
          f"{_pct(len({i['customer_id'] for i in interactions}), len(customers))}")

    print("\n-- D6 interventions --")
    print(f"  success_stories rows {len(_rows('success_stories'))}")

    print("\n-- I identity --")
    groups = defaultdict(list)
    for a in accounts:
        groups[normalise_name(a["Name"])].append(a)
    dups = [g for k, g in groups.items() if k and len(g) > 1]
    series = Counter(tuple(sorted({(m.get("AccountNumber") or "none")[:5] for m in g})) for g in dups)
    conflict = [g for g in dups if len({m.get("Customer_Status__c") or "" for m in g}) > 1]
    both = [g for g in dups
            if any(m.get("Customer_Status__c") == "Churned" for m in g)
            and any(m.get("Customer_Status__c") in ("Active", "At Risk") for m in g)]
    print(f"  duplicate groups {len(dups)}, by series {dict(series)}, sizes "
          f"{dict(Counter(len(g) for g in dups))}")
    print(f"  groups with conflicting status {len(conflict)}, with Churned and Active/At Risk {len(both)}")


if __name__ == "__main__":
    main()
