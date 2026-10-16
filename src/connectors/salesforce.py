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

#
# One field list per object, shared by the laptop pull below and the sync engine
# (src/integrations/sync.py), so the two cannot drift. Nothing here is contact
# PII (ADR-0012 section 7); adding a field is a recorded decision.

FIELDS: dict[str, tuple[str, ...]] = {
    "Account": (
        "Id", "AccountNumber", "Name", "Industry", "Type", "AnnualRevenue", "NumberOfEmployees",
        "ARR__c", "Seats__c", "Segment__c", "Region__c", "Customer_Status__c",
        "Contract_Start_Date__c", "Contract_End_Date__c", "Churn_Date__c", "Churn_Category__c",
        "Churn_Reason__c", "Competitor__c", "Active_Users__c", "Feature_Adoption__c",
        "Last_Login_Date__c", "NPS_Score__c", "CreatedDate",
    ),
    "Opportunity": (
        "Id", "AccountId", "Name", "StageName", "IsClosed", "IsWon", "Amount", "CloseDate", "Type",
        "Description", "Loss_Reason__c",
    ),
    "OpportunityCompetitor": ("Id", "OpportunityId", "CompetitorName"),
    "Case": (
        "Id", "CaseNumber", "AccountId", "Subject", "Description", "Status", "IsClosed", "Priority",
        "Type", "Reason", "CreatedDate", "Legacy_Created_Date__c", "Resolution_Hours__c", "CSAT__c",
    ),
    "Task": ("Id", "WhatId", "AccountId", "Subject", "Description", "ActivityDate", "TaskSubtype",
             "CreatedDate"),
}

# Which object fills which key of the raw dict transform() reads.
RAW_KEYS: dict[str, str] = {
    "Account": "accounts", "Opportunity": "opportunities",
    "OpportunityCompetitor": "competitors", "Case": "cases", "Task": "tasks",
}


def soql(sobject: str, extra: tuple[str, ...] = ()) -> str:
    return f"SELECT {', '.join(FIELDS[sobject] + extra)} FROM {sobject}"


ACCOUNT_SOQL = soql("Account")
OPPORTUNITY_SOQL = soql("Opportunity")
COMPETITOR_SOQL = soql("OpportunityCompetitor")
CASE_SOQL = soql("Case")
TASK_SOQL = soql("Task")

# --------------------------------------------------------------------------------------
# Canonical schema. Column order matches data/*.csv exactly.
# --------------------------------------------------------------------------------------

CUSTOMER_COLUMNS = [
    "customer_id", "company_name", "segment", "industry", "region", "arr", "seats",
    "contract_start_date", "contract_end_date", "tenure_months", "is_churned",
    "churn_date", "churn_category", "specific_reason", "competitor",
    # When the account was created in the source system. Separate from when it was
    # pulled: every sync re-reads every record, so the pull date says nothing about
    # which batch an account came from.
    "crm_created_date",
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
INTERVENTION_COLUMNS = [
    "customer_id", "company_name", "segment", "play", "challenge_category",
    "play_date", "outcome", "renewal_close_date",
]

# A save play is a Task whose subject starts "Save play:" -- a convention, so CSMs
# bend it ("SAVE PLAY - ...", "save-play:..."). The play itself is free text, matched
# to a fixed vocabulary by keyword; anything that matches nothing is dropped and
# counted rather than guessed at.
SAVE_PLAY = re.compile(r"^\s*save[\s_-]*play\b\s*[:\-\u2013]?\s*(.*)$", re.I)
PLAYS = {
    "Created internal champions program": ("champion",),
    "Ran dedicated onboarding sessions": ("onboarding",),
    "Provided API access for customization": ("api",),
    "Held executive business review": ("executive", "exec ", "business review", "ebr"),
    "Offered custom pricing tier": ("pricing", "discount"),
}
CHALLENGE = re.compile(r"challenge:\s*([^.\n]+)", re.I)
CHALLENGE_CATEGORIES = ("Adoption Challenges", "Product Fit", "Competition",
                        "Support Issues", "Pricing Concerns")

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


# What each decision means, in the words an operator would use. Kept beside the code
# that makes the decisions so a new one cannot ship without saying what it is:
# tests/test_integrations_report.py reads every key transform() can record and
# fails on any that has no entry here.
#
# Groups: "excluded" removed a record before it became a customer, "dropped" removed
# a child record, "inferred" filled or corrected a value, "label" is how churn was
# decided, "plays" is what was read from CSM save plays and how each turned out,
# "limit" is a property of the source the connector cannot fix.
DECISION_LABELS: dict[str, tuple[str, str]] = {
    "salesforce_sample_account_excluded": ("excluded", "Salesforce demo records excluded"),
    "no_type_or_status_treated_as_prospect": ("excluded", "Accounts with no type or status treated as prospects"),
    "duplicate_account_groups": ("inferred", "Duplicate company groups found"),
    "company_name_disambiguated": ("inferred", "Company names told apart by account number"),
    "churn_date_inferred_from_opportunity": ("inferred", "Churn dates worked out from a lost renewal"),
    "status_stale_vs_lost_renewal": ("inferred", "Status still active despite a lost renewal"),
    "arr_from_latest_won_opportunity": ("inferred", "ARR taken from the latest won deal"),
    "arr_missing_set_zero": ("inferred", "ARR missing, set to zero"),
    "segment_inferred_from_arr": ("inferred", "Segment worked out from ARR"),
    "contract_start_from_first_won_opportunity": ("inferred", "Contract start taken from the first won deal"),
    "contract_start_missing": ("inferred", "Contract start missing"),
    "case_date_from_createddate_not_legacy": ("inferred", "Case dated by load date, no original date"),
    "csat_out_of_range_blanked": ("inferred", "Out-of-range CSAT scores blanked"),
    "case_priority_unmapped_defaulted_medium": ("inferred", "Unknown case priority set to medium"),
    "opportunity_without_account_dropped": ("dropped", "Deals not linked to any account dropped"),
    "churned_without_any_date_dropped": ("dropped", "Churned accounts with no date dropped"),
    "case_not_on_a_customer_dropped": ("dropped", "Support cases not linked to a customer dropped"),
    "case_after_churn_date_dropped": ("dropped", "Support cases after the customer left dropped"),
    "activity_not_on_a_customer_dropped": ("dropped", "Activities not linked to a customer dropped"),
    "activity_after_churn_date_dropped": ("dropped", "Activities after the customer left dropped"),
    "no_usage_fields_engagement_skipped": ("dropped", "Customers with no usage fields, engagement skipped"),
    "engagement_after_churn_date_dropped": ("dropped", "Usage readings after the customer left dropped"),
    "churn_label_source:status": ("label", "Churned by status only"),
    "churn_label_source:lost_renewal": ("label", "Churned by lost renewal only"),
    "churn_label_source:status+lost_renewal": ("label", "Churned by status and lost renewal"),
    "churn_label_source:active": ("label", "Active"),
    "engagement_weeks_of_history_per_customer": ("limit", "Weeks of usage history per customer"),
    "save_play_recorded": ("plays", "Save plays recorded"),
    "save_play_outcome:renewed": ("plays", "Renewed after the play"),
    "save_play_outcome:churned": ("plays", "Churned after the play"),
    "save_play_outcome:pending": ("plays", "Renewal not decided yet"),
    "save_play_unrecognised_dropped": ("plays", "Save plays naming no known play, dropped"),
    "save_play_without_challenge": ("plays", "Save plays with no challenge recorded"),
    "save_play_not_on_a_customer_dropped": ("plays", "Save plays not on a customer, dropped"),
}


def describe_report(report: dict[str, Any]) -> dict[str, Any]:
    """An ingest report as the Integrations page shows it: the funnel, then each
    decision with its label, group and examples. Pure, so the API and tests share it.

    A key with no label is passed through under group "unlabelled" rather than
    dropped, so a reader still sees it. The test is what stops that shipping.
    """
    decisions = report.get("decisions", {})
    examples = report.get("examples", {})
    rows = []
    for key, count in decisions.items():
        group, label = DECISION_LABELS.get(key, ("unlabelled", key))
        rows.append({"key": key, "group": group, "label": label,
                     "count": count, "examples": examples.get(key, [])[:3]})
    order = {"excluded": 0, "inferred": 1, "dropped": 2, "label": 3, "plays": 4, "limit": 5,
             "unlabelled": 6}
    rows.sort(key=lambda r: (order[r["group"]], -r["count"], r["label"]))
    return {
        "source": "salesforce",
        "as_of": report.get("as_of"),
        "funnel": {
            "accounts_read": report.get("accounts_read", 0),
            "prospects_excluded": report.get("prospects_excluded", 0),
            "sample_records_excluded": decisions.get("salesforce_sample_account_excluded", 0),
            "customers_written": report.get("customers_written", 0),
        },
        "decisions": rows,
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
            "crm_created_date": _iso(_d(a.get("CreatedDate"))),
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
    interventions = _save_plays(raw.get("tasks", []), by_id, opps_by_acct, report, as_of)
    engagement = _engagement(by_id, report, as_of)

    tables = {
        "customers": customers,
        "support_tickets": tickets,
        "customer_interactions": interactions,
        "interventions": interventions,
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


def canonical_play(text: str) -> str | None:
    t = f" {text.lower()} "
    for play, keywords in PLAYS.items():
        if any(k in t for k in keywords):
            return play
    return None


def _save_plays(tasks: Iterable[dict], by_id: dict, opps_by_acct: dict,
                report: IngestReport, as_of: date) -> list[dict]:
    """Each CSM save play, and whether the account renewed afterwards.

    The outcome is the first renewal deal closing on or after the play: won is
    renewed, lost is churned, still open (or none yet) is pending. Failures are kept
    alongside successes. A record of only the saves that worked is how a playbook
    ends up recommending everything.
    """
    out = []
    for t in tasks:
        m = SAVE_PLAY.match(t.get("Subject") or "")
        if not m:
            continue
        account_id = t.get("AccountId") or t.get("WhatId") or ""
        cust = by_id.get(account_id)
        if not cust:
            report.note("save_play_not_on_a_customer_dropped", t.get("Subject"))
            continue
        # The subject names the play; the description names the challenge. Reading the
        # play from both matched "Challenge: Pricing Concerns" to the pricing play on a
        # subject that named no play at all.
        play = canonical_play(m.group(1))
        if not play:
            report.note("save_play_unrecognised_dropped", t.get("Subject"))
            continue
        found = CHALLENGE.search(t.get("Description") or "")
        challenge = next((c for c in CHALLENGE_CATEGORIES
                          if found and c.lower() == found.group(1).strip().lower()), "")
        if not challenge:
            report.note("save_play_without_challenge", t.get("Subject"))

        when = _d(t.get("ActivityDate")) or _d(t.get("CreatedDate"))
        renewals = sorted(
            (o for o in opps_by_acct.get(account_id, [])
             if "renewal" in (o.get("Name") or "").lower() and _d(o.get("CloseDate"))
             and when and _d(o["CloseDate"]) >= when),
            key=lambda o: _d(o["CloseDate"]))
        renewal = renewals[0] if renewals else None
        if renewal and renewal.get("IsClosed") and _d(renewal["CloseDate"]) <= as_of:
            outcome = "renewed" if renewal.get("IsWon") else "churned"
        else:
            outcome = "pending"
        report.note("save_play_recorded", f"{cust['company_name']}: {play}")
        report.note(f"save_play_outcome:{outcome}", cust["company_name"])
        out.append({
            "customer_id": cust["customer_id"],
            "company_name": cust["company_name"],
            "segment": cust["segment"],
            "play": play,
            "challenge_category": challenge,
            "play_date": _iso(when),
            "outcome": outcome,
            "renewal_close_date": _iso(_d(renewal["CloseDate"])) if renewal else "",
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
        # A last login after the churn date is a stale or wrong field, not usage by a
        # customer who has gone. Dropped like cases and activities after churn; dating
        # it at the churn date instead would invent when the reading was taken.
        if _after_churn(when, cust):
            report.note("engagement_after_churn_date_dropped", cust["company_name"])
            continue
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


API_VERSION = "v59.0"


class CliSession:
    """The two calls the connector and seeder make, sent through the Salesforce CLI.

    The CLI holds the login and refreshes it; this class never sees a token. Newer
    CLI releases redact the token in `sf org display`, which is what broke the old
    way of borrowing it, and the redaction is right: a token copied into the
    environment outlives the reason it was copied.
    """

    def __init__(self, alias: str):
        self.alias = alias

    def _call(self, path: str, method: str = "GET", body: Any = None) -> Any:
        import subprocess

        cmd = ["sf", "api", "request", "rest", path, "-o", self.alias, "-X", method]
        if body is not None:
            cmd += ["-b", "-"]
        out = subprocess.run(cmd, input=json.dumps(body) if body is not None else None,
                             capture_output=True, text=True)
        if out.returncode != 0:
            detail = (out.stdout.strip() or out.stderr.strip())[:400]
            raise RuntimeError(f"sf {method} {path.split('?')[0]} failed: {detail}")
        return json.loads(out.stdout) if out.stdout.strip() else None

    def query_all(self, soql: str) -> dict:
        from urllib.parse import quote

        page = self._call(f"/services/data/{API_VERSION}/query?q={quote(soql)}")
        records = list(page["records"])
        while not page.get("done", True):
            page = self._call(page["nextRecordsUrl"])
            records += page["records"]
        return {"totalSize": len(records), "records": records}

    def query(self, soql: str) -> dict:
        return self.query_all(soql)

    def restful(self, path: str, method: str = "GET", json: Any = None) -> Any:
        return self._call(f"/services/data/{API_VERSION}/{path}", method, json)


def connect():
    """
    Credentials come from the environment and are never logged. In order: an access
    token (SF_INSTANCE_URL + SF_ACCESS_TOKEN), username, password and security token,
    or else the Salesforce CLI login for SF_ORG_ALIAS (default churnguard-dev).
    """
    if not os.getenv("SF_ACCESS_TOKEN") and not os.getenv("SF_USERNAME"):
        return CliSession(os.getenv("SF_ORG_ALIAS", "churnguard-dev"))

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

    return {key: q(soql(sobject)) for sobject, key in RAW_KEYS.items()}


COLUMNS: dict[str, list[str]] = {
    "customers": CUSTOMER_COLUMNS,
    "support_tickets": TICKET_COLUMNS,
    "customer_interactions": INTERACTION_COLUMNS,
    "engagement_snapshots": ENGAGEMENT_COLUMNS,
    "interventions": INTERVENTION_COLUMNS,
    **EMPTY_TABLES,
}


def as_csv_rows(tables: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """The tables exactly as ``write`` would put them in the CSV files: the
    canonical columns in order, every value a string, missing is ``""``.

    The data contracts and everything downstream read the CSV shape. A snapshot
    kept in the database goes through this first, so a contract that holds for
    the files holds for the snapshot, and a restored snapshot is byte-for-byte
    the file the laptop pull would have written.
    """
    return {
        name: [{col: "" if row.get(col) is None else str(row.get(col)) for col in COLUMNS[name]}
               for row in tables.get(name, [])]
        for name in COLUMNS
    }


def write(tables: dict[str, list[dict]], report: IngestReport, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS[name], extrasaction="ignore")
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
