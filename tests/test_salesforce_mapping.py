"""
Pins every judgement transform() makes about messy Salesforce data.

No network and no credentials: the fixtures are shaped exactly like simple-salesforce
query results with the `attributes` key stripped, which is what fetch() returns.
"""

from datetime import date

from src.connectors.salesforce import (
    canonical_competitor,
    derive_churn,
    normalise_name,
    transform,
)

AS_OF = date(2026, 10, 4)


def acct(id_, name, **kw):
    base = {
        "Id": id_, "AccountNumber": f"ACC-{id_}", "Name": name, "Type": "Customer - Direct",
        "Industry": "Retail", "Customer_Status__c": "Active", "ARR__c": 120000,
        "Seats__c": 100, "Segment__c": "Commercial", "Region__c": "NA",
        "Contract_Start_Date__c": "2024-01-15", "Contract_End_Date__c": "2027-01-15",
        "Active_Users__c": 80, "Feature_Adoption__c": 60, "Last_Login_Date__c": "2026-10-01",
    }
    base.update(kw)
    return base


def opp(id_, account_id, stage, close, amount=100000, **kw):
    won = stage == "Closed Won"
    closed = stage.startswith("Closed")
    return {"Id": id_, "AccountId": account_id, "Name": f"opp {id_}", "StageName": stage,
            "IsClosed": closed, "IsWon": won, "Amount": amount, "CloseDate": close, **kw}


# ---- churn label (ADR-0011) ----------------------------------------------------------


def test_status_churned_uses_account_fields():
    a = acct("1", "A", Customer_Status__c="Churned", Churn_Date__c="2025-06-01",
             Churn_Category__c="Budget", Churn_Reason__c="Budget cut")
    label = derive_churn(a, [], {}, AS_OF)
    assert (label.is_churned, label.churn_date, label.category, label.source) == (
        1, date(2025, 6, 1), "Budget", "status")


def test_lost_last_renewal_churns_even_when_status_says_active():
    a = acct("1", "A")
    opps = [opp("o1", "1", "Closed Won", "2024-01-15"),
            opp("o2", "1", "Closed Lost", "2025-01-15", Loss_Reason__c="Competition",
                Description="Moved to competitor")]
    label = derive_churn(a, opps, {"o2": "vantage analytics inc"}, AS_OF)
    assert label.is_churned == 1
    assert label.source == "lost_renewal"
    assert label.churn_date == date(2025, 1, 15)
    assert label.category == "Competition"
    assert label.competitor == "Vantage Analytics"


def test_lost_then_won_is_not_churn():
    opps = [opp("o1", "1", "Closed Lost", "2024-06-01"),
            opp("o2", "1", "Closed Won", "2025-06-01")]
    assert derive_churn(acct("1", "A"), opps, {}, AS_OF).is_churned == 0


def test_open_renewal_with_past_close_date_is_not_churn():
    # A stale open opportunity is a hygiene problem, not a lost customer.
    opps = [opp("o1", "1", "Closed Won", "2024-01-15"),
            opp("o2", "1", "Negotiation/Review", "2025-01-15")]
    assert derive_churn(acct("1", "A"), opps, {}, AS_OF).is_churned == 0


def test_future_closed_lost_is_ignored():
    opps = [opp("o1", "1", "Closed Lost", "2026-12-01")]
    assert derive_churn(acct("1", "A"), opps, {}, AS_OF).is_churned == 0


def test_missing_churn_date_falls_back_to_lost_renewal_date():
    a = acct("1", "A", Customer_Status__c="Churned", Churn_Date__c=None)
    opps = [opp("o2", "1", "Closed Lost", "2025-03-01")]
    label = derive_churn(a, opps, {}, AS_OF)
    assert label.churn_date == date(2025, 3, 1)
    assert label.source == "status+lost_renewal"


# ---- names and competitors -----------------------------------------------------------


def test_normalise_name_collapses_case_spacing_and_legal_suffix():
    variants = ["Acme Industrial Supply", "ACME Industrial Supply Inc.",
                "acme industrial supply, LLC", "Acme Industrial Supply  "]
    assert len({normalise_name(v) for v in variants}) == 1


def test_competitor_aliases():
    assert canonical_competitor("Vantage") == "Vantage Analytics"
    assert canonical_competitor("vantage analytics inc") == "Vantage Analytics"
    assert canonical_competitor("Somebody New") == "Somebody New"
    assert canonical_competitor(None) == ""


# ---- end-to-end transform ------------------------------------------------------------


def raw_fixture():
    return {
        "accounts": [
            acct("1", "Northwind Logistics"),
            acct("2", "Northwind Logistics, Inc", ARR__c=None, Segment__c=None),
            acct("3", "Prospect Co", Type="Prospect", Customer_Status__c="Prospect"),
            acct("4", "Gone Corp", Customer_Status__c="Churned", Churn_Date__c="2025-05-01",
                 Churn_Category__c="Champion Left", Last_Login_Date__c="2025-04-20",
                 Active_Users__c=0),
        ],
        "opportunities": [
            opp("o1", "2", "Closed Won", "2024-02-01", amount=30000),
            opp("o9", None, "Closed Won", "2024-02-01"),
        ],
        "competitors": [],
        "cases": [
            {"Id": "c1", "CaseNumber": "00001", "AccountId": "1", "Subject": "help",
             "IsClosed": True, "Priority": "High", "CreatedDate": "2026-10-04T10:00:00.000+0000",
             "Legacy_Created_Date__c": "2026-03-01", "Resolution_Hours__c": 30, "CSAT__c": 2},
            {"Id": "c2", "CaseNumber": "00002", "AccountId": "3", "Subject": "q",
             "IsClosed": False, "Priority": "Low", "CreatedDate": "2026-10-04T10:00:00.000+0000"},
            {"Id": "c3", "CaseNumber": "00003", "AccountId": "4", "Subject": "late",
             "IsClosed": False, "Priority": "Weird", "CreatedDate": "2026-10-04T10:00:00.000+0000",
             "Legacy_Created_Date__c": "2025-09-01"},
            {"Id": "c4", "CaseNumber": "00004", "AccountId": "1", "Subject": "no legacy date",
             "IsClosed": False, "Priority": "Medium", "CreatedDate": "2026-10-04T10:00:00.000+0000",
             "CSAT__c": 9},
        ],
        "tasks": [
            {"Id": "t1", "WhatId": "4", "AccountId": "4", "Subject": "Asked about cancellation",
             "ActivityDate": "2025-04-01"},
            {"Id": "t2", "WhatId": "4", "AccountId": "4", "Subject": "Logged after churn",
             "ActivityDate": "2025-08-01"},
        ],
    }


def test_transform_end_to_end():
    tables, report = transform(raw_fixture(), AS_OF)
    customers = {c["customer_id"]: c for c in tables["customers"]}
    d = report.to_dict()["decisions"]

    # Prospects are not customers.
    assert "ACC-3" not in customers
    assert report.prospects_excluded == 1

    # Duplicates are kept apart, flagged, and their names made unique.
    assert d["duplicate_account_groups"] == 1
    names = [c["company_name"] for c in tables["customers"]]
    assert len(names) == len(set(names))
    assert customers["ACC-2"]["company_name"] == "Northwind Logistics, Inc [ACC-2]"

    # Missing ARR comes from the latest won opportunity; segment follows from it.
    assert customers["ACC-2"]["arr"] == 30000
    assert customers["ACC-2"]["segment"] == "SMB"
    assert d["arr_from_latest_won_opportunity"] == 1
    assert d["segment_inferred_from_arr"] == 1

    # Churned customer: label, date, tenure up to churn not to as-of.
    gone = customers["ACC-4"]
    assert (gone["is_churned"], gone["churn_date"]) == (1, "2025-05-01")
    assert gone["tenure_months"] == 16

    # Orphan opportunity reported.
    assert d["opportunity_without_account_dropped"] == 1

    # Tickets: prospect's case dropped, post-churn case dropped, legacy date used,
    # missing legacy date falls back, bad CSAT blanked.
    tickets = {t["ticket_id"]: t for t in tables["support_tickets"]}
    assert set(tickets) == {"00001", "00004"}
    assert tickets["00001"]["created_date"] == "2026-03-01"
    assert tickets["00001"]["resolved_date"] == "2026-03-02"
    assert tickets["00004"]["created_date"] == "2026-10-04"
    assert tickets["00004"]["csat_score"] == ""
    assert d["case_not_on_a_customer_dropped"] == 1
    assert d["case_after_churn_date_dropped"] == 1
    assert d["case_date_from_createddate_not_legacy"] == 1
    assert d["csat_out_of_range_blanked"] == 1

    # Activities after churn are dropped, not kept with an impossible date.
    contents = [i["content"] for i in tables["customer_interactions"]]
    assert contents == ["Asked about cancellation"]
    assert d["activity_after_churn_date_dropped"] == 1

    # Engagement: one snapshot row per customer, churned one dated at last login week.
    eng = {e["customer_id"]: e for e in tables["engagement_snapshots"]}
    assert len(eng) == 3
    assert eng["ACC-4"]["week_start"] == "2025-04-14"
    # Seat utilisation only (80 of 100); adoption stays in its own column.
    assert eng["ACC-1"]["engagement_score"] == 0.8
    assert eng["ACC-1"]["feature_adoption_rate"] == 0.6
    assert d["engagement_weeks_of_history_per_customer"] == 1


def test_salesforce_sample_accounts_are_excluded_by_exact_name():
    raw = raw_fixture()
    raw["accounts"] += [acct("90", "GenePoint"), acct("91", "GenePoint Labs")]
    raw["cases"].append({"Id": "c9", "CaseNumber": "00009", "AccountId": "90", "Subject": "demo",
                         "IsClosed": False, "Priority": "Low",
                         "CreatedDate": "2026-09-27T10:00:00.000+0000"})
    tables, report = transform(raw, AS_OF)
    ids = {c["customer_id"] for c in tables["customers"]}
    d = report.to_dict()["decisions"]

    assert "ACC-90" not in ids                 # the demo record
    assert "ACC-91" in ids                     # a similar real name is untouched
    assert d["salesforce_sample_account_excluded"] == 1
    assert "00009" not in {t["ticket_id"] for t in tables["support_tickets"]}


def test_no_event_postdates_churn():
    """The same contract scripts/validate_dataset.py enforces on the synthetic data."""
    tables, _ = transform(raw_fixture(), AS_OF)
    churn = {c["customer_id"]: c["churn_date"] for c in tables["customers"] if c["churn_date"]}
    for t in tables["support_tickets"]:
        assert not (t["customer_id"] in churn and t["created_date"] > churn[t["customer_id"]])
    for i in tables["customer_interactions"]:
        assert not (i["customer_id"] in churn and i["interaction_date"] > churn[i["customer_id"]])
