"""
Salesforce ingestion: pull a customer's CRM and land it in ChurnGuard's canonical schema.

This is the "bring-your-own-data" path from README section 11. It reads a live
Salesforce org and writes the same CSV files the synthetic generator writes, so the
dbt models, the 28 data contracts and the API run on it unchanged.

Two halves, kept apart on purpose:

- ``fetch()`` talks to Salesforce. It is the only part that needs credentials or a
  network, so it is the only part not covered by unit tests.
- ``transform()`` is pure. Raw Salesforce records in, canonical tables and an
  ingestion report out. Every judgement call about messy data lives here, and
  ``tests/test_salesforce_mapping.py`` pins each one.

Nothing is silently fixed. Every record that is dropped, inferred, renamed or
relabelled is counted in the report, because the data team (README section 1) will not
trust a number whose provenance they cannot see.

The churn label is derived, not read: Salesforce has no churn column. The rule and
why it was chosen are in docs/adr/0011-derive-churn-label-from-salesforce.md.

Usage:
    SF_USERNAME=... SF_PASSWORD=... SF_SECURITY_TOKEN=... \\
        uv run --extra salesforce python -m src.connectors.salesforce --out data/salesforce
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------------------
# SOQL. Custom fields are the ones added to the demo org; a real customer's org will
# name them differently, which is what FIELD_MAP below is for.
# --------------------------------------------------------------------------------------

ACCOUNT_SOQL = """
SELECT Id, AccountNumber, Name, Industry, Type, AnnualRevenue, NumberOfEmployees,
       ARR__c, Seats__c, Segment__c, Region__c, Customer_Status__c,
       Contract_Start_Date__c, Contract_End_Date__c, Churn_Date__c, Churn_Category__c,
       Churn_Reason__c, Competitor__c, Active_Users__c, Feature_Adoption__c,
       Last_Login_Date__c, NPS_Score__c
FROM Account
"""

OPPORTUNITY_SOQL = """
SELECT Id, AccountId, Name, StageName, IsClosed, IsWon, Amount, CloseDate, Type,
       Description, Loss_Reason__c
FROM Opportunity
"""

COMPETITOR_SOQL = "SELECT OpportunityId, CompetitorName FROM OpportunityCompetitor"

CASE_SOQL = """
SELECT Id, CaseNumber, AccountId, Subject, Description, Status, IsClosed, Priority,
       Type, Reason, CreatedDate, Legacy_Created_Date__c, Resolution_Hours__c, CSAT__c
FROM Case
"""

TASK_SOQL = """
SELECT Id, WhatId, AccountId, Subject, Description, ActivityDate, TaskSubtype, CreatedDate
FROM Task
"""

# --------------------------------------------------------------------------------------
# Canonical schema. Column order matches data/*.csv exactly.
# --------------------------------------------------------------------------------------

CUSTOMER_COLUMNS = [
    "customer_id", "company_name", "segment", "industry", "region", "arr", "seats",
    "contract_start_date", "contract_end_date", "tenure_months", "is_churned",
    "churn_date", "churn_category", "specific_reason", "competitor",
]
TICKET_COLUMNS = [
    "ticket_id", "customer_id", "company_name", "segment", "category", "issue_type",
    "severity", "created_date", "resolved_date", "resolution_hours", "description",
    "resolution_notes", "csat_score",
]
INTERACTION_COLUMNS = [
    "customer_id", "company_name", "segment", "interaction_date", "interaction_type",
    "content", "customer_tenure_months",
]
ENGAGEMENT_COLUMNS = [
    "customer_id", "week_start", "active_users", "sessions", "feature_adoption_rate",
    "engagement_score",
]
# Present so the validator's file list resolves. Salesforce holds no equivalent of
# either, and inventing rows for them would be exactly the synthetic-data problem
# this connector exists to get away from.
EMPTY_TABLES = {
    "success_stories": [
        "story_id", "customer_id", "company_name", "segment", "title",
        "challenge_category", "specific_challenge", "solution", "arr", "team_size",
        "adoption_before", "adoption_after", "engagement_increase",
        "support_reduction", "full_story",
    ],
    "churn_analyses": [
        "doc_id", "customer_id", "company_name", "segment", "churn_category",
        "specific_reason", "arr", "tenure_months", "risk_score", "feature_adoption",
        "support_tickets_30d", "last_engagement_days", "is_churned", "document",
    ],
}

# Every Developer Edition org is provisioned with these demo records. They carry none
# of the customer-success fields and their cases are dated at org creation, so scoring
# them would put fabricated tickets in the 30-day window (docs/SITE_SURVEY_SALESFORCE.md).
# Matched by exact name, never by pattern, so a real customer cannot be caught by it.
SALESFORCE_SAMPLE_ACCOUNTS = {
    "Edge Communications", "Burlington Textiles Corp of America", "Pyramid Construction Inc.",
    "Dickenson plc", "Grand Hotels & Resorts Ltd", "United Oil & Gas Corp.",
    "Express Logistics and Transport", "University of Arizona", "United Oil & Gas, UK",
    "United Oil & Gas, Singapore", "GenePoint", "sForce", "Sample Account for Entitlements",
}

SEGMENTS = ("SMB", "Commercial", "Enterprise")
CHURN_STATUSES = {"churned", "former customer", "cancelled", "canceled"}
PROSPECT_TYPES = {"prospect"}
SEVERITY = {"high": "High", "critical": "Critical", "medium": "Medium", "low": "Low"}

# Same competitor, typed four ways by four reps. Matching is on a normalised key.
COMPETITOR_ALIASES = {
    "vantage": "Vantage Analytics",
    "vantageanalytics": "Vantage Analytics",
    "vantageanalyticsinc": "Vantage Analytics",
    "clarity": "Clarity CRM",
    "claritycrm": "Clarity CRM",
    "northbeam": "Northbeam",
    "inhousebuild": "In-house build",
}

LEGAL_SUFFIX = re.compile(r"[,\s]+(inc\.?|llc|corp\.?|co\.?|group|holdings|ltd\.?)$", re.I)


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------


@dataclass
class IngestReport:
    """Everything transform() decided on the operator's behalf, counted."""

    as_of: str = ""
    accounts_read: int = 0
    customers_written: int = 0
    prospects_excluded: int = 0
    counts: Counter = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def note(self, key: str, example: str | None = None, limit: int = 5) -> None:
        self.counts[key] += 1
        if example is not None and len(self.examples[key]) < limit:
            self.examples[key].append(example)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "accounts_read": self.accounts_read,
            "customers_written": self.customers_written,
            "prospects_excluded": self.prospects_excluded,
            "decisions": dict(sorted(self.counts.items())),
            "examples": {k: v for k, v in sorted(self.examples.items())},
        }


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _d(value: Any) -> date | None:
    """Salesforce dates arrive as 'YYYY-MM-DD' and datetimes as ISO with offset."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _iso(d: date | None) -> str:
    return d.isoformat() if d else ""


def _months(start: date | None, end: date | None) -> int:
    if not start or not end:
        return 0
    return max(0, (end.year - start.year) * 12 + end.month - start.month)


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise_name(name: str) -> str:
    """Key for spotting the same company typed differently: case, spacing, legal suffix."""
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    while True:
        stripped = LEGAL_SUFFIX.sub("", n).strip()
        if stripped == n:
            break
        n = stripped
    return re.sub(r"[^a-z0-9]", "", n)


def canonical_competitor(raw: str | None) -> str:
    if not raw:
        return ""
    key = re.sub(r"[^a-z]", "", raw.lower())
    return COMPETITOR_ALIASES.get(key, raw.strip())


def segment_for(arr: float | None) -> str:
    if arr is None:
        return "SMB"
    if arr < 50_000:
        return "SMB"
    if arr < 200_000:
        return "Commercial"
    return "Enterprise"


# --------------------------------------------------------------------------------------
# Churn label
# --------------------------------------------------------------------------------------


@dataclass
class ChurnLabel:
    is_churned: int
    churn_date: date | None
    category: str
    reason: str
    competitor: str
    source: str  # which rule fired; reported, and asserted in tests


def derive_churn(account: dict, opps: list[dict], competitors: dict[str, str],
                 as_of: date) -> ChurnLabel:
    """
    ADR-0011. An account has churned if either:

      1. its status field says so, or
      2. its most recent closed renewal is Closed Lost and nothing was won after it.

    The status field is preferred when present because a CSM set it deliberately.
    Rule 2 exists because status fields go stale: a renewal is lost in the opportunity
    and nobody updates the account. The two disagreeing is reported, not resolved
    silently.
    """
    status = (account.get("Customer_Status__c") or "").strip().lower()
    closed = sorted(
        (o for o in opps if o.get("IsClosed") and _d(o.get("CloseDate"))
         and _d(o.get("CloseDate")) <= as_of),
        key=lambda o: _d(o["CloseDate"]),
    )
    last = closed[-1] if closed else None
    lost_last = bool(last and not last.get("IsWon"))

    by_status = status in CHURN_STATUSES
    if not by_status and not lost_last:
        return ChurnLabel(0, None, "", "", "", "active")

    source = "status" if by_status else "lost_renewal"
    if by_status and lost_last:
        source = "status+lost_renewal"

    churn_date = (
        _d(account.get("Churn_Date__c"))
        or (_d(last["CloseDate"]) if lost_last else None)
        or _d(account.get("Contract_End_Date__c"))
    )
    category = (account.get("Churn_Category__c")
                or (last.get("Loss_Reason__c") if lost_last else None) or "Other")
    reason = (account.get("Churn_Reason__c")
              or (last.get("Description") if lost_last else None) or "")
    competitor = canonical_competitor(
        account.get("Competitor__c") or (competitors.get(last["Id"]) if lost_last else None)
    )
    return ChurnLabel(1, churn_date, category, reason, competitor, source)


# --------------------------------------------------------------------------------------
# Transform
# --------------------------------------------------------------------------------------


def transform(raw: dict[str, list[dict]], as_of: date) -> tuple[dict[str, list[dict]],
                                                                IngestReport]:
    """Raw Salesforce records -> canonical ChurnGuard tables plus an ingestion report."""
    report = IngestReport(as_of=as_of.isoformat())
    accounts = raw.get("accounts", [])
    report.accounts_read = len(accounts)

    opps_by_acct: dict[str, list[dict]] = defaultdict(list)
    for o in raw.get("opportunities", []):
        if o.get("AccountId"):
            opps_by_acct[o["AccountId"]].append(o)
        else:
            report.note("opportunity_without_account_dropped", o.get("Name"))
    competitors = {c["OpportunityId"]: c.get("CompetitorName", "")
                   for c in raw.get("competitors", [])}

    # ---- duplicates: flagged, not merged. Merging is a customer decision.
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in accounts:
        groups[normalise_name(a.get("Name", ""))].append(a)
    dup_ids = set()
    for key, members in groups.items():
        if key and len(members) > 1:
            names = " | ".join(sorted(m.get("Name", "").strip() for m in members))
            report.note("duplicate_account_groups", names)
            dup_ids.update(m["Id"] for m in members)

    customers: list[dict] = []
    seen_names: Counter = Counter()
    by_id: dict[str, dict] = {}

    for a in accounts:
        if (a.get("Name") or "").strip() in SALESFORCE_SAMPLE_ACCOUNTS:
            report.note("salesforce_sample_account_excluded", a.get("Name"), limit=20)
            continue
        acct_type = (a.get("Type") or "").strip().lower()
        status = (a.get("Customer_Status__c") or "").strip().lower()
        if status == "prospect" or (acct_type in PROSPECT_TYPES and not status):
            report.prospects_excluded += 1
            continue
        if not status and not acct_type:
            report.prospects_excluded += 1
            report.note("no_type_or_status_treated_as_prospect", a.get("Name"))
            continue

        opps = opps_by_acct.get(a["Id"], [])
        won = sorted((o for o in opps if o.get("IsWon") and _d(o.get("CloseDate"))),
                     key=lambda o: _d(o["CloseDate"]))

        label = derive_churn(a, opps, competitors, as_of)
        report.note(f"churn_label_source:{label.source}")
        if label.source == "lost_renewal":
            report.note("status_stale_vs_lost_renewal", a.get("Name"))
        if label.is_churned and not label.churn_date:
            report.note("churned_without_any_date_dropped", a.get("Name"))
            continue
        if label.is_churned and not _d(a.get("Churn_Date__c")):
            report.note("churn_date_inferred_from_opportunity", a.get("Name"))

        arr = _num(a.get("ARR__c"))
        if arr is None:
            arr = _num(won[-1].get("Amount")) if won else None
            report.note("arr_from_latest_won_opportunity" if arr is not None
                        else "arr_missing_set_zero", a.get("Name"))
        arr = arr or 0.0

        segment = a.get("Segment__c")
        if segment not in SEGMENTS:
            segment = segment_for(arr)
            report.note("segment_inferred_from_arr", a.get("Name"))

        start = _d(a.get("Contract_Start_Date__c")) or (_d(won[0]["CloseDate"]) if won else None)
        if not _d(a.get("Contract_Start_Date__c")):
            report.note("contract_start_from_first_won_opportunity" if start
                        else "contract_start_missing", a.get("Name"))
        end_ref = label.churn_date if label.is_churned else as_of

        # company_name is a unique key in the contracts. Duplicates keep their real
        # name with the source account number appended, so nobody mistakes a
        # disambiguation for a merge.
        name = re.sub(r"\s+", " ", (a.get("Name") or "").strip())
        seen_names[name.lower()] += 1
        if seen_names[name.lower()] > 1 or a["Id"] in dup_ids:
            suffix = a.get("AccountNumber") or a["Id"][-6:]
            name = f"{name} [{suffix}]"
            report.note("company_name_disambiguated", name)

        row = {
            "customer_id": a.get("AccountNumber") or f"SF-{a['Id']}",
            "company_name": name,
            "segment": segment,
            "industry": a.get("Industry") or "Unknown",
            "region": a.get("Region__c") or "Unknown",
            "arr": int(round(arr)),
            "seats": int(_num(a.get("Seats__c")) or 0),
            "contract_start_date": _iso(start),
            "contract_end_date": _iso(_d(a.get("Contract_End_Date__c"))),
            "tenure_months": _months(start, end_ref),
            "is_churned": label.is_churned,
            "churn_date": label.churn_date.isoformat() if label.churn_date else "",
            "churn_category": label.category if label.is_churned else "",
            "specific_reason": label.reason if label.is_churned else "",
            "competitor": label.competitor if label.is_churned else "",
        }
        customers.append(row)
        by_id[a["Id"]] = {**row, "_churn": label.churn_date, "_acct": a}

    report.customers_written = len(customers)

    tickets = _tickets(raw.get("cases", []), by_id, report)
    interactions = _interactions(raw.get("tasks", []), by_id, report, as_of)
    engagement = _engagement(by_id, report, as_of)

    tables = {
        "customers": customers,
        "support_tickets": tickets,
        "customer_interactions": interactions,
        "engagement_snapshots": engagement,
        **{name: [] for name in EMPTY_TABLES},
    }
    return tables, report


def _after_churn(event: date | None, cust: dict) -> bool:
    return bool(event and cust["_churn"] and event > cust["_churn"])


def _tickets(cases: Iterable[dict], by_id: dict, report: IngestReport) -> list[dict]:
    out = []
    for c in cases:
        cust = by_id.get(c.get("AccountId") or "")
        if not cust:
            report.note("case_not_on_a_customer_dropped", c.get("CaseNumber"))
            continue
        created = _d(c.get("Legacy_Created_Date__c"))
        if not created:
            created = _d(c.get("CreatedDate"))
            report.note("case_date_from_createddate_not_legacy", c.get("CaseNumber"))
        if _after_churn(created, cust):
            report.note("case_after_churn_date_dropped", c.get("CaseNumber"))
            continue
        hours = _num(c.get("Resolution_Hours__c"))
        resolved = ""
        if c.get("IsClosed") and hours is not None:
            resolved = (created + timedelta(hours=hours)).isoformat()
        csat = _num(c.get("CSAT__c"))
        if csat is not None and not 1 <= csat <= 5:
            report.note("csat_out_of_range_blanked", c.get("CaseNumber"))
            csat = None
        sev = SEVERITY.get((c.get("Priority") or "").strip().lower())
        if not sev:
            sev = "Medium"
            report.note("case_priority_unmapped_defaulted_medium", c.get("Priority"))
        subject = (c.get("Subject") or "").strip()
        out.append({
            "ticket_id": c.get("CaseNumber") or c["Id"],
            "customer_id": cust["customer_id"],
            "company_name": cust["company_name"],
            "segment": cust["segment"],
            "category": c.get("Type") or "Other",
            "issue_type": c.get("Reason") or subject or "Unspecified",
            "severity": sev,
            "created_date": created.isoformat(),
            "resolved_date": resolved,
            "resolution_hours": "" if hours is None else int(hours),
            "description": (c.get("Description") or subject or "").strip(),
            "resolution_notes": "",
            "csat_score": "" if csat is None else int(csat),
        })
    return out


def _interactions(tasks: Iterable[dict], by_id: dict, report: IngestReport,
                  as_of: date) -> list[dict]:
    out = []
    for t in tasks:
        cust = by_id.get(t.get("AccountId") or t.get("WhatId") or "")
        if not cust:
            report.note("activity_not_on_a_customer_dropped", t.get("Subject"))
            continue
        when = _d(t.get("ActivityDate")) or _d(t.get("CreatedDate"))
        if _after_churn(when, cust):
            report.note("activity_after_churn_date_dropped", t.get("Subject"))
            continue
        start = _d(cust["contract_start_date"])
        text = " ".join(p for p in [(t.get("Subject") or "").strip(),
                                    (t.get("Description") or "").strip()] if p)
        out.append({
            "customer_id": cust["customer_id"],
            "company_name": cust["company_name"],
            "segment": cust["segment"],
            "interaction_date": when.isoformat() if when else "",
            "interaction_type": t.get("TaskSubtype") or "Activity",
            "content": text,
            "customer_tenure_months": _months(start, when or as_of),
        })
    return out


def _engagement(by_id: dict, report: IngestReport, as_of: date) -> list[dict]:
    """
    Salesforce holds a usage *snapshot* (active users, adoption, last login), not a
    weekly history. One row per customer is written, dated at the last login for
    churned accounts and the as-of week otherwise. Trend features need history this
    source cannot supply, and the report says so rather than padding it with zeros.
    """
    out = []
    for cust in by_id.values():
        a = cust["_acct"]
        seats = _num(a.get("Seats__c")) or 0
        users = _num(a.get("Active_Users__c"))
        adoption = _num(a.get("Feature_Adoption__c"))
        if users is None and adoption is None:
            report.note("no_usage_fields_engagement_skipped", cust["company_name"])
            continue
        when = _d(a.get("Last_Login_Date__c")) if cust["_churn"] else as_of
        when = when or as_of
        week = when - timedelta(days=when.weekday())
        utilisation = min(1.0, (users or 0) / seats) if seats else 0.0
        adoption_rate = round((adoption or 0) / 100, 3)
        out.append({
            "customer_id": cust["customer_id"],
            "week_start": week.isoformat(),
            "active_users": int(users or 0),
            "sessions": "",
            "feature_adoption_rate": adoption_rate,
            # Seat utilisation only. Adoption has its own column and its own weight in
            # RISK_WEIGHTS; folding it in here as well counted it twice (0.375, not 0.20).
            "engagement_score": round(utilisation, 3),
        })
    # Surfaced as a decision so nobody reads a one-row history as a full one.
    report.counts["engagement_weeks_of_history_per_customer"] = 1
    return out


# --------------------------------------------------------------------------------------
# Salesforce I/O
# --------------------------------------------------------------------------------------


def connect():
    """
    Credentials come from the environment and are never logged. Either an access token
    from `sf org display` (SF_INSTANCE_URL + SF_ACCESS_TOKEN) or username, password and
    security token.
    """
    from simple_salesforce import Salesforce  # optional extra: salesforce

    if os.getenv("SF_ACCESS_TOKEN"):
        return Salesforce(instance_url=os.environ["SF_INSTANCE_URL"],
                          session_id=os.environ["SF_ACCESS_TOKEN"])
    return Salesforce(
        username=os.environ["SF_USERNAME"],
        password=os.environ["SF_PASSWORD"],
        security_token=os.environ.get("SF_SECURITY_TOKEN", ""),
        domain=os.environ.get("SF_DOMAIN", "login"),
    )


def fetch(sf) -> dict[str, list[dict]]:
    def q(soql: str) -> list[dict]:
        rows = sf.query_all(" ".join(soql.split()))["records"]
        return [{k: v for k, v in r.items() if k != "attributes"} for r in rows]

    return {
        "accounts": q(ACCOUNT_SOQL),
        "opportunities": q(OPPORTUNITY_SOQL),
        "competitors": q(COMPETITOR_SOQL),
        "cases": q(CASE_SOQL),
        "tasks": q(TASK_SOQL),
    }


def write(tables: dict[str, list[dict]], report: IngestReport, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    columns = {
        "customers": CUSTOMER_COLUMNS,
        "support_tickets": TICKET_COLUMNS,
        "customer_interactions": INTERACTION_COLUMNS,
        "engagement_snapshots": ENGAGEMENT_COLUMNS,
        **EMPTY_TABLES,
    }
    for name, rows in tables.items():
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=columns[name], extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    (out_dir / "ingest_report.json").write_text(json.dumps(report.to_dict(), indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="data/salesforce", type=Path)
    ap.add_argument("--as-of", default=date.today().isoformat(),
                    help="Anchor date for tenure and 'already closed'. Fix it for "
                         "reproducible runs, as the synthetic pipeline does.")
    ap.add_argument("--raw-dump", type=Path,
                    help="Also save the raw pull as JSON, so transform() can be re-run "
                         "offline against exactly what Salesforce returned.")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(".env")

    raw = fetch(connect())
    if args.raw_dump:
        args.raw_dump.write_text(json.dumps(raw, default=str))
    tables, report = transform(raw, date.fromisoformat(args.as_of))
    write(tables, report, args.out)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
