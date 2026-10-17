"""
The sync run against fakes (ADR-0012, section 5). No network, no database.

What these pin: the first run reads every object in full and the next reads only
what changed since the watermark, less an overlap; a record deleted in Salesforce
lands as a tombstone; pages are followed; a failing object fails the run, names
the object and Salesforce's code, and leaves the earlier objects' watermarks so
the next run resumes rather than restarts; a dead token fails the run once and
marks the connection; a second trigger joins the running run; a run left behind
by a dead process is swept; and the routes gate, 404, 409 and 503 for the right
reasons while showing no record data and no secret.
"""

from __future__ import annotations

import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from connectors.salesforce import FIELDS, RAW_KEYS, soql  # noqa: E402
from integrations import crypto  # noqa: E402
from integrations.flow import ConnectFlow  # noqa: E402
from integrations.redaction import find_secret  # noqa: E402
from integrations.sync import (
    BACKOFF_BASE, OBJECTS, SNAPSHOTS_KEPT, SYNC_FIELDS, SYNC_INTERVAL, RollbackRefused, SyncEngine,
)  # noqa: E402
from integrations.tokens import NeedsReauth, TokenService  # noqa: E402
from tests.salesforce_fakes import (  # noqa: E402
    CODE, HOST, ORG, REFRESH, SITE, FakeSalesforce, FakeStore, FakeSyncStore,
)
import connections_routes  # noqa: E402

T0 = "2026-10-16T10:00:00Z"
NOON = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def rec(sobject: str, sf_id: str, **fields) -> dict:
    """A record with every field the sync reads, so projection never hits a gap."""
    row = {f: None for f in FIELDS[sobject]}
    row.update({"Id": sf_id, "SystemModstamp": T0, "IsDeleted": False, **fields})
    return row


def org_data() -> dict[str, list[dict]]:
    """A small org that satisfies every data contract: three customers, one of
    them churned (rate 33%), usage that separates the label, children dated
    inside each contract. Tests that want a contract to fail bend one field."""
    return {
        "Account": [
            rec("Account", "001A", Name="Acme", AccountNumber="A-1", Type="Customer",
                Customer_Status__c="Active", ARR__c=120000, Seats__c=10, Active_Users__c=8,
                Feature_Adoption__c=70, Contract_Start_Date__c="2025-01-01", Industry="Software",
                Region__c="West", CreatedDate="2024-12-20T00:00:00.000+0000"),
            rec("Account", "001B", Name="Bolt", AccountNumber="B-1", Type="Customer",
                Customer_Status__c="Active", ARR__c=40000, Seats__c=10, Active_Users__c=9,
                Feature_Adoption__c=60, Contract_Start_Date__c="2025-03-01", Industry="Retail",
                Region__c="East", CreatedDate="2025-02-20T00:00:00.000+0000"),
            rec("Account", "001C", Name="Cord", AccountNumber="C-1", Type="Customer",
                Customer_Status__c="Churned", Churn_Date__c="2026-09-01", Churn_Category__c="Pricing Concerns",
                Churn_Reason__c="Too expensive", ARR__c=9000, Seats__c=10, Active_Users__c=1,
                Feature_Adoption__c=10, Last_Login_Date__c="2026-08-20",
                Contract_Start_Date__c="2025-06-01", Industry="Media", Region__c="North",
                CreatedDate="2025-05-20T00:00:00.000+0000"),
        ],
        "Opportunity": [rec("Opportunity", "006A", AccountId="001A", Name="Acme Renewal",
                            StageName="Negotiation", IsClosed=False, IsWon=False, CloseDate="2026-12-31"),
                        rec("Opportunity", "006B", AccountId="001B", Name="Bolt Renewal",
                            StageName="Negotiation", IsClosed=False, IsWon=False, CloseDate="2026-12-31")],
        "OpportunityCompetitor": [rec("OpportunityCompetitor", "00kA", OpportunityId="006B",
                                      CompetitorName="Vantage")],
        "Case": [rec("Case", "500A", AccountId="001A", CaseNumber="1001", Subject="Login fails",
                     Priority="High", Type="Bug", CreatedDate="2026-10-01T09:00:00.000+0000"),
                 rec("Case", "500B", AccountId="001C", CaseNumber="1002", Subject="Invoice query",
                     Priority="Low", Type="Billing", CreatedDate="2026-08-01T09:00:00.000+0000")],
        "Task": [rec("Task", "00TA", AccountId="001A", Subject="Save play: executive review",
                     Description="Challenge: Pricing Concerns", ActivityDate="2026-10-02",
                     CreatedDate="2026-10-02T09:00:00.000+0000"),
                 rec("Task", "00TB", AccountId="001B", Subject="Call", ActivityDate="2026-10-03",
                     CreatedDate="2026-10-03T09:00:00.000+0000")],
    }


@pytest.fixture
def provider():
    return crypto.LocalKeyProvider({"v1": secrets.token_bytes(32)}, current="v1")


def connected(store, provider, sf: FakeSalesforce):
    from urllib.parse import parse_qs, urlsplit

    flow = ConnectFlow(store, provider, sf.oauth(), site_origin=SITE)
    started = flow.start()
    state = parse_qs(urlsplit(started.authorize_url).query)["state"][0]
    return flow.complete(CODE, state).connection


def engine_for(provider, sf: FakeSalesforce | None = None, clock: Clock | None = None):
    """A connected org, its stores, and an engine that runs inline."""
    sf = sf or FakeSalesforce()
    sf.records = sf.records or org_data()
    store = FakeStore()
    c = connected(store, provider, sf)
    runs = FakeSyncStore(store)
    engine = SyncEngine(TokenService(store, provider, sf.oauth()), store, runs,
                        clock=clock or Clock(NOON))
    engine.spawn = engine.run
    return engine, c, sf, store, runs


# ------------------------------------------------------------------- full run

def test_the_first_run_is_full_and_lands_every_object(provider):
    engine, c, sf, store, runs = engine_for(provider)
    t = engine.start(c.id)
    assert t.joined is False
    run = runs.get_run(t.run.id)

    assert run.status == "succeeded" and run.error is None and run.public()["mode"] == "full"
    assert run.contracts == {"passed": 22, "failed": 0, "skipped": 6, "failures": []}
    snap = runs.get_snapshot(run.snapshot_id)
    assert snap.contracts_passed and snap.run_id == run.id and snap.as_of == NOON.date()
    assert snap.row_counts == {"customers": 3, "support_tickets": 2, "customer_interactions": 2,
                               "engagement_snapshots": 3, "interventions": 1,
                               "success_stories": 0, "churn_analyses": 0}
    assert snap.report["customers_written"] == 3 and snap.report["accounts_read"] == 3
    assert all(isinstance(v, str) for row in snap.tables["customers"] for v in row.values())  # CSV shape
    assert [c["company_name"] for c in snap.tables["customers"]] == ["Acme", "Bolt", "Cord"]
    assert runs.latest_good_snapshot().id == snap.id
    pointer = runs.get_serving()                                   # promoted (ADR-0012 §6)
    assert pointer.snapshot_id == snap.id and pointer.run_id == run.id and pointer.previous_snapshot_id is None
    assert pointer.promoted_by == "system"
    assert [q[0] for q in sf.queries] == ["query"] * 5           # full reads: live records only
    assert [q[1] for q in sf.queries] == list(OBJECTS) and all(q[2] is None for q in sf.queries)
    assert {k: (v["mode"], v["fetched"], v["tombstones"]) for k, v in run.objects.items()} == {
        "Account": ("full", 3, 0), "Opportunity": ("full", 2, 0),
        "OpportunityCompetitor": ("full", 1, 0), "Case": ("full", 2, 0), "Task": ("full", 2, 0)}
    assert run.api_requests == 5 and run.api_usage == {"used": 5, "max": 15000}
    assert run.started_at == NOON and run.finished_at == NOON and run.as_of == NOON.date()
    assert all(runs.watermarks[(c.id, o)] == NOON for o in OBJECTS)
    assert runs.counts(None, c.id) == {o: {"live": n, "tombstones": 0}
                                       for o, n in zip(OBJECTS, (3, 2, 1, 2, 2))}
    assert [r["Name"] for r in runs.live_records(None, c.id, "Account")] == ["Acme", "Bolt", "Cord"]
    assert store.rows[c.id].last_used_at == NOON

    events = [a["event"] for a in store.audit]
    assert events[-4:] == ["sync_started", "refresh", "promotion", "sync_finished"]
    assert store.audit[-4]["actor"] == "operator" and store.audit[-4]["detail"]["trigger"] == "manual"
    promo = store.audit[-2]["detail"]
    assert promo["snapshot_id"] == snap.id and promo["previous_snapshot_id"] is None
    assert promo["row_counts"]["customers"] == 3
    done = store.audit[-1]["detail"]
    assert done["status"] == "succeeded" and done["objects"]["Account"]["fetched"] == 3
    assert done["snapshot_id"] == snap.id and done["contracts"]["passed"] == 22
    assert done["promoted"] is True and done["served"] is None            # no on_promote wired
    assert find_secret(json.dumps(run.public(), default=str)) is None


def test_every_field_the_laptop_pull_reads_is_in_the_sync_query():
    for sobject in OBJECTS:
        q = soql(sobject, SYNC_FIELDS)
        assert all(f in q for f in FIELDS[sobject]) and "SystemModstamp" in q and "IsDeleted" in q
    assert set(RAW_KEYS) == set(OBJECTS)


# ------------------------------------------------------------ incremental run

def test_the_second_run_reads_only_changes_and_tombstones_deletes(provider):
    clock = Clock(NOON)
    engine, c, sf, store, runs = engine_for(provider, clock=clock)
    engine.start(c.id)

    clock.at = NOON + timedelta(hours=1)
    sf.records["Account"][0]["ARR__c"] = 150000                       # Acme changed
    sf.records["Account"][0]["SystemModstamp"] = "2026-10-16T12:30:00Z"
    sf.records["Account"][1]["IsDeleted"] = True                      # Bolt deleted
    sf.records["Account"][1]["SystemModstamp"] = "2026-10-16T12:40:00Z"
    sf.queries.clear()

    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "succeeded" and run.public()["mode"] == "incremental"
    assert [q[0] for q in sf.queries] == ["queryAll"] * 5             # deleted records too
    assert all(q[2] == "2026-10-16T11:55:00Z" for q in sf.queries)    # watermark less 5 min
    assert run.objects["Account"] == {
        "mode": "incremental", "fetched": 2, "tombstones": 1, "requests": 1,
        "since": NOON.isoformat(), "watermark": clock.at.isoformat()}
    assert all(run.objects[o]["fetched"] == 0 for o in OBJECTS if o != "Account")
    assert runs.counts(None, c.id)["Account"] == {"live": 2, "tombstones": 1}
    live = {r["Id"]: r for r in runs.live_records(None, c.id, "Account")}
    assert set(live) == {"001A", "001C"} and live["001A"]["ARR__c"] == 150000
    snap = runs.get_snapshot(run.snapshot_id)                        # Bolt has left the candidate
    assert [c["company_name"] for c in snap.tables["customers"]] == ["Acme", "Cord"]
    assert snap.tables["customers"][0]["arr"] == "150000" and run.contracts["passed"] == 22
    pointer = runs.get_serving()                                      # flipped to the new one
    assert pointer.snapshot_id == snap.id and pointer.previous_snapshot_id == runs.runs(c.id)[1].snapshot_id
    assert runs.watermarks[(c.id, "Account")] == clock.at
    assert run.public()["records_fetched"] == 2 and run.public()["tombstones"] == 1


def test_pages_are_followed_to_the_end(provider):
    sf = FakeSalesforce()
    sf.page_size = 2
    engine, c, sf, store, runs = engine_for(provider, sf)
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "succeeded"
    assert run.objects["Account"]["fetched"] == 3 and run.objects["Account"]["requests"] == 2
    assert run.objects["Opportunity"]["requests"] == 1
    assert run.api_requests == 6


def test_an_expired_session_mid_run_is_refreshed_once(provider):
    sf = FakeSalesforce(api_401_once=True)
    engine, c, sf, store, runs = engine_for(provider, sf)
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "succeeded" and len(sf.refreshes) == 2 and run.api_requests == 5


# ---------------------------------------------------------------- failures

def test_a_failing_object_fails_the_run_and_keeps_the_earlier_watermarks(provider):
    sf = FakeSalesforce()
    sf.missing_fields["Case"] = {"Legacy_Created_Date__c"}
    engine, c, sf, store, runs = engine_for(provider, sf)
    run = runs.get_run(engine.start(c.id).run.id)

    assert run.status == "failed"
    assert run.error == "Case: INVALID_FIELD: No such column 'Legacy_Created_Date__c' on entity 'Case'"
    assert list(run.objects) == ["Account", "Opportunity", "OpportunityCompetitor"]
    assert run.api_requests == 4 and run.api_usage == {"used": 4, "max": 15000}
    assert set(o for (_, o, _) in runs.raw) == {"Account", "Opportunity", "OpportunityCompetitor"}
    assert {o for (_, o) in runs.watermarks} == {"Account", "Opportunity", "OpportunityCompetitor"}
    assert store.audit[-1]["detail"]["status"] == "failed" and "INVALID_FIELD" in store.audit[-1]["detail"]["error"]

    # Fixed in the org: the next run resumes, incremental where it got to and full where it did not.
    sf.missing_fields.clear()
    sf.queries.clear()
    again = runs.get_run(engine.start(c.id).run.id)
    assert again.status == "succeeded"
    assert {o: m for (_, o, since), m in zip(sf.queries, (q[0] for q in sf.queries))} == {
        "Account": "queryAll", "Opportunity": "queryAll", "OpportunityCompetitor": "queryAll",
        "Case": "query", "Task": "query"}


def test_a_failed_page_leaves_nothing_half_landed(provider):
    """Account is read in two pages and the second fails: no Account row may
    land, because the watermark did not move either."""
    sf = FakeSalesforce()
    sf.page_size = 2
    engine, c, sf, store, runs = engine_for(provider, sf)
    real = sf.handler

    def second_page_fails(request):
        if "/query/" in request.url.path:
            import httpx
            return httpx.Response(500, json=[{"errorCode": "UNKNOWN_EXCEPTION", "message": "boom"}])
        return real(request)

    engine.tokens.http = engine.tokens.oauth.http = __import__("httpx").Client(
        transport=__import__("httpx").MockTransport(second_page_fails))
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "failed" and run.error == "Account: UNKNOWN_EXCEPTION: boom"
    assert runs.raw == {} and runs.watermarks == {}
    assert run.api_requests == 2                      # both requests counted, nothing kept


def test_a_contract_failure_keeps_the_snapshot_and_degrades_the_run(provider):
    sf = FakeSalesforce()
    sf.records = org_data()
    sf.records["Account"][2]["Customer_Status__c"] = "Active"       # nobody churned: rate 0%
    sf.records["Account"][2]["Churn_Date__c"] = None
    engine, c, sf, store, runs = engine_for(provider, sf)
    run = runs.get_run(engine.start(c.id).run.id)

    assert run.status == "degraded"
    assert run.error == "contracts failed: churn rate is in a usable band (0.10-0.50)"
    assert run.contracts["failed"] == 1 and run.contracts["passed"] == 21
    assert run.contracts["failures"] == ["churn rate is in a usable band (0.10-0.50): churn rate is 0.0%"]
    snap = runs.get_snapshot(run.snapshot_id)
    assert snap is not None and snap.contracts_passed is False      # kept for diagnosis
    assert runs.latest_good_snapshot() is None and runs.get_serving() is None   # never served
    assert "promotion" not in [a["event"] for a in store.audit]
    assert all(runs.watermarks[(c.id, o)] == NOON for o in OBJECTS)  # the landing itself stood
    assert store.audit[-1]["detail"]["status"] == "degraded"


def test_a_degraded_run_leaves_the_previous_snapshot_serving(provider):
    clock = Clock(NOON)
    engine, c, sf, store, runs = engine_for(provider, clock=clock)
    first = runs.get_run(engine.start(c.id).run.id)
    assert runs.get_serving().snapshot_id == first.snapshot_id

    clock.at = NOON + timedelta(hours=1)
    sf.records["Account"][2]["Customer_Status__c"] = "Active"       # now nobody churned
    sf.records["Account"][2]["Churn_Date__c"] = None
    sf.records["Account"][2]["SystemModstamp"] = "2026-10-16T12:30:00Z"
    second = runs.get_run(engine.start(c.id).run.id)
    assert second.status == "degraded" and second.snapshot_id != first.snapshot_id
    assert runs.get_serving().snapshot_id == first.snapshot_id       # the last good one still serves
    assert runs.latest_good_snapshot().id == first.snapshot_id


def test_on_promote_is_called_with_the_snapshot_and_its_failure_is_recorded(provider):
    served: list = []
    engine, c, sf, store, runs = engine_for(provider)
    engine.on_promote = served.append
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "succeeded" and [s.id for s in served] == [run.snapshot_id]
    assert store.audit[-1]["detail"]["served"] is True

    def broken(snapshot):
        raise OSError("disk full")

    engine.on_promote = broken
    sf.records["Account"][0]["SystemModstamp"] = "2026-10-16T12:30:00Z"
    engine.now = Clock(NOON + timedelta(hours=1))
    again = runs.get_run(engine.start(c.id).run.id)
    assert again.status == "succeeded"                                # the pointer did flip
    assert again.error == "promoted, but serving it failed: OSError: disk full"
    assert runs.get_serving().snapshot_id == again.snapshot_id
    assert store.audit[-1]["detail"]["promoted"] is True and store.audit[-1]["detail"]["served"] is False


# --------------------------------------------------------------- schedule

def test_sync_due_runs_once_a_day_and_is_a_use(provider):
    clock = Clock(NOON)
    engine, c, sf, store, runs = engine_for(provider, clock=clock)

    (first,) = engine.sync_due(wait=True)                                # never synced: due
    assert first["action"] == "started" and first["trigger"] == "schedule"
    assert first["run"]["status"] == "succeeded" and first["run"]["trigger"] == "schedule"
    assert store.rows[c.id].last_used_at == NOON                          # resets the org's idle timer

    clock.at = NOON + timedelta(hours=23)
    (again,) = engine.sync_due(wait=True)
    assert again["action"] == "not_due" and again["due_at"] == NOON + SYNC_INTERVAL

    clock.at = NOON + timedelta(hours=25)
    (due,) = engine.sync_due(wait=True)
    assert due["action"] == "started" and due["run"]["mode"] == "incremental"
    assert len(runs.runs(c.id)) == 2
    assert sf.api_calls.count("/services/data/v59.0/limits") == 2         # one allowance check per due run


def test_sync_due_defers_while_the_org_allowance_is_low(provider):
    sf = FakeSalesforce()
    sf.api_remaining = 2000                                               # 13% of 15,000 left
    engine, c, sf, store, runs = engine_for(provider, sf)
    (line,) = engine.sync_due(wait=True)
    assert line["action"] == "deferred" and line["reason"] == "org API allowance low"
    assert line["fraction_remaining"] == 0.1333 and runs.runs(c.id) == []
    assert store.audit[-1]["event"] == "sync_deferred" and store.audit[-1]["detail"]["api_remaining"] == 2000

    sf.api_remaining = 3001                                               # just over the reserve
    (line,) = engine.sync_due(wait=True)
    assert line["action"] == "started"


def test_sync_due_backs_off_after_request_limit_exceeded(provider):
    clock = Clock(NOON)
    sf = FakeSalesforce()
    sf.fail_object["Account"] = (403, "REQUEST_LIMIT_EXCEEDED")
    engine, c, sf, store, runs = engine_for(provider, sf, clock=clock)
    (line,) = engine.sync_due(wait=True)
    assert line["run"]["status"] == "failed" and "REQUEST_LIMIT_EXCEEDED" in line["run"]["error"]

    clock.at = NOON + timedelta(minutes=30)
    (line,) = engine.sync_due(wait=True)
    assert line["action"] == "deferred" and line["reason"].startswith("backing off")
    assert NOON + BACKOFF_BASE <= line["retry_at"] <= NOON + BACKOFF_BASE + timedelta(minutes=10)

    clock.at = NOON + timedelta(hours=1, minutes=11)                      # past base plus any jitter
    (line,) = engine.sync_due(wait=True)
    assert line["action"] == "started" and line["run"]["status"] == "failed"   # second strike
    clock.at = clock.at + timedelta(hours=1, minutes=30)
    (line,) = engine.sync_due(wait=True)
    assert line["action"] == "deferred"                                    # now two hours
    assert len(runs.runs(c.id)) == 2

    sf.fail_object.clear()
    clock.at = clock.at + timedelta(hours=2)
    (line,) = engine.sync_due(wait=True)
    assert line["action"] == "started" and line["run"]["status"] == "succeeded"


def test_sync_due_skips_a_connection_that_needs_a_login_and_joins_a_running_one(provider):
    engine, c, sf, store, runs = engine_for(provider, FakeSalesforce(refresh_fail="invalid_grant"))
    (line,) = engine.sync_due(wait=True)                                  # the allowance check kills it
    assert line["action"] == "skipped" and "invalid_grant" in line["reason"]
    assert runs.runs(c.id) == []

    engine2, c2, sf2, store2, runs2 = engine_for(provider)
    engine2.spawn = lambda run_id: None
    pending = engine2.start(c2.id)
    (line,) = engine2.sync_due()
    assert line["action"] == "joined" and line["run"]["id"] == pending.run.id


def test_sync_due_without_wait_leaves_the_run_to_the_thread(provider):
    engine, c, sf, store, runs = engine_for(provider)
    spawned: list = []
    engine.spawn = spawned.append
    (line,) = engine.sync_due()
    assert line["action"] == "started" and line["run"]["status"] == "pending" and spawned == [line["run"]["id"]]


# ----------------------------------------------------------------- rollback

def test_rollback_serves_the_previous_snapshot_and_audits(provider):
    clock = Clock(NOON)
    served: list = []
    engine, c, sf, store, runs = engine_for(provider, clock=clock)
    engine.on_promote = served.append
    first = runs.get_run(engine.start(c.id).run.id)
    clock.at = NOON + timedelta(hours=1)
    sf.records["Account"][0]["SystemModstamp"] = "2026-10-16T12:30:00Z"
    second = runs.get_run(engine.start(c.id).run.id)
    assert runs.get_serving().snapshot_id == second.snapshot_id

    pointer, ok = engine.rollback(actor="operator")
    assert ok is True and pointer.snapshot_id == first.snapshot_id
    assert pointer.previous_snapshot_id == second.snapshot_id and pointer.promoted_by == "operator"
    assert served[-1].id == first.snapshot_id
    row = store.audit[-1]
    assert row["event"] == "rollback" and row["actor"] == "operator"
    assert row["detail"]["from_snapshot_id"] == second.snapshot_id
    assert row["detail"]["to_snapshot_id"] == first.snapshot_id and row["detail"]["row_counts"]["customers"] == 3

    # Rolling back again goes forward to the one just left: the pair is a toggle.
    pointer, _ = engine.rollback(actor="operator")
    assert pointer.snapshot_id == second.snapshot_id and pointer.previous_snapshot_id == first.snapshot_id
    # And by explicit id.
    pointer, _ = engine.rollback(actor="operator", to_snapshot_id=first.snapshot_id)
    assert pointer.snapshot_id == first.snapshot_id


def test_rollback_refusals_say_why(provider):
    engine, c, sf, store, runs = engine_for(provider)
    with pytest.raises(RollbackRefused, match="Nothing is being served"):
        engine.rollback()
    run = runs.get_run(engine.start(c.id).run.id)
    with pytest.raises(RollbackRefused, match="No earlier snapshot"):
        engine.rollback()
    with pytest.raises(RollbackRefused, match="already the one"):
        engine.rollback(to_snapshot_id=run.snapshot_id)
    with pytest.raises(RollbackRefused, match="no longer exists"):
        engine.rollback(to_snapshot_id="nope")

    sf.records["Account"][2]["Customer_Status__c"] = "Active"       # a degraded candidate
    sf.records["Account"][2]["Churn_Date__c"] = None
    sf.records["Account"][2]["SystemModstamp"] = "2026-10-16T12:30:00Z"
    engine.now = Clock(NOON + timedelta(hours=1))
    bad = runs.get_run(engine.start(c.id).run.id)
    assert bad.status == "degraded"
    with pytest.raises(RollbackRefused, match="failed its data contracts"):
        engine.rollback(to_snapshot_id=bad.snapshot_id)
    assert runs.get_serving().snapshot_id == run.snapshot_id
    assert "rollback" not in [a["event"] for a in store.audit]


# ---------------------------------------------------------------- retention

def test_retention_keeps_ten_snapshots_plus_the_serving_pair(provider):
    clock = Clock(NOON)
    engine, c, sf, store, runs = engine_for(provider, clock=clock)
    ids = []
    for i in range(SNAPSHOTS_KEPT + 3):
        clock.at = NOON + timedelta(hours=i)
        sf.records["Account"][0]["SystemModstamp"] = (NOON + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids.append(runs.get_run(engine.start(c.id).run.id).snapshot_id)
    assert len(runs.snaps) == SNAPSHOTS_KEPT
    assert set(runs.snaps) == set(ids[-SNAPSHOTS_KEPT:])
    kept = [a for a in store.audit if a["event"] == "retention"]
    assert kept and kept[-1]["detail"]["snapshots_deleted"] == 1 and kept[-1]["actor"] == "system"

    # Roll back to the oldest kept, then run ten more: the pair survives.
    oldest = ids[-SNAPSHOTS_KEPT]
    engine.rollback(to_snapshot_id=oldest)
    newest = ids[-1]
    for i in range(SNAPSHOTS_KEPT):
        clock.at = NOON + timedelta(days=1, hours=i)
        sf.records["Account"][0]["SystemModstamp"] = clock.at.strftime("%Y-%m-%dT%H:%M:%SZ")
        runs.get_run(engine.start(c.id).run.id)
    pointer = runs.get_serving()
    assert pointer.previous_snapshot_id in runs.snaps                 # protected, though old
    assert oldest not in runs.snaps or oldest == pointer.previous_snapshot_id
    assert newest not in runs.snaps or newest == pointer.previous_snapshot_id


def test_old_tombstones_are_pruned_live_rows_never(provider):
    clock = Clock(NOON)
    engine, c, sf, store, runs = engine_for(provider, clock=clock)
    engine.start(c.id)
    clock.at = NOON + timedelta(hours=1)
    sf.records["Account"][1]["IsDeleted"] = True
    sf.records["Account"][1]["SystemModstamp"] = "2026-10-16T12:30:00Z"
    engine.start(c.id)
    assert runs.counts(None, c.id)["Account"] == {"live": 2, "tombstones": 1}

    for row in runs.raw.values():                                     # a month passes
        row["last_seen_at"] -= timedelta(days=31)
    clock.at = NOON + timedelta(days=31)
    engine.start(c.id)
    counts = runs.counts(None, c.id)
    assert counts["Account"] == {"live": 2, "tombstones": 0}
    assert sum(v["live"] for v in counts.values()) == 9                 # 10 records, Bolt gone
    assert store.audit[-2]["event"] == "retention" and store.audit[-2]["detail"]["tombstones_deleted"] == 1


def test_request_limit_exceeded_is_named_not_retried(provider):
    sf = FakeSalesforce()
    sf.fail_object["Account"] = (403, "REQUEST_LIMIT_EXCEEDED")
    engine, c, sf, store, runs = engine_for(provider, sf)
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "failed" and run.error.startswith("Account: REQUEST_LIMIT_EXCEEDED")
    assert len(sf.queries) == 1


def test_a_dead_token_fails_the_run_and_marks_the_connection(provider):
    engine, c, sf, store, runs = engine_for(provider, FakeSalesforce(refresh_fail="invalid_grant"))
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "failed" and "invalid_grant" in run.error and run.objects == {}
    assert store.rows[c.id].status == "needs_reauth"
    with pytest.raises(NeedsReauth):
        engine.start(c.id)
    assert len(sf.refreshes) == 1


# ------------------------------------------------------------- concurrency

def test_a_second_trigger_joins_the_running_run(provider):
    engine, c, sf, store, runs = engine_for(provider)
    engine.spawn = lambda run_id: None                  # leave it pending, as a thread would
    first = engine.start(c.id)
    second = engine.start(c.id)
    assert second.joined is True and second.run.id == first.run.id
    assert [a["event"] for a in store.audit].count("sync_started") == 1

    engine.run(first.run.id)
    assert runs.get_run(first.run.id).status == "succeeded"
    third = engine.start(c.id)
    assert third.joined is False and third.run.id != first.run.id


def test_an_abandoned_run_is_swept_and_named(provider):
    engine, c, sf, store, runs = engine_for(provider)
    engine.spawn = lambda run_id: None
    stuck = engine.start(c.id).run
    runs._replace(stuck.id, status="running", started_at=datetime.now(timezone.utc) - timedelta(hours=1))

    fresh = engine.start(c.id)
    assert fresh.joined is False and fresh.run.id != stuck.id
    swept = runs.get_run(stuck.id)
    assert swept.status == "failed" and swept.error.startswith("abandoned")
    sweep_audit = [a for a in store.audit if a["event"] == "sync_finished" and a["actor"] == "system"]
    assert sweep_audit[-1]["detail"] == {"run_id": stuck.id, "status": "failed", "error": "abandoned"}


# ----------------------------------------------------------------------- HTTP

class State:
    def __init__(self, store, provider, tokens=None, runs=None, engine=None):
        self.connections = store
        self.key_provider = provider
        self.tokens = tokens
        self.sync_runs = runs
        self.sync = engine
        self.errors = {}
        self.not_applicable = {}


def make_app(state) -> TestClient:
    app = FastAPI()
    app.include_router(connections_routes.build_router(lambda: state))
    return TestClient(app)


KEY = {"X-Operator-Key": "k" * 32}


def test_sync_and_runs_routes(provider, monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)
    engine, c, sf, store, runs = engine_for(provider)
    client = make_app(State(store, provider, engine.tokens, runs, engine))

    assert client.post(f"/connections/{c.id}/sync").status_code == 403
    before = client.get("/connections").json()
    assert before["last_run"] is None and before["serving"] is None

    r = client.post(f"/connections/{c.id}/sync", headers=KEY)
    assert r.status_code == 202 and r.json()["joined"] is False
    assert r.json()["run"]["status"] == "pending"       # the row as created; the outcome is polled
    run_id = r.json()["run"]["id"]

    listed = client.get(f"/connections/{c.id}/runs").json()
    assert listed["enabled"] and [x["id"] for x in listed["runs"]] == [run_id]
    (run,) = listed["runs"]
    assert run["status"] == "succeeded" and run["records_fetched"] == 10 and run["api_requests"] == 5
    assert run["mode"] == "full" and run["duration_seconds"] == 0.0 and run["objects"]["Case"]["fetched"] == 2
    assert run["contracts"]["passed"] == 22 and run["snapshot_id"]
    text = json.dumps(listed)
    assert "Acme" not in text and REFRESH not in text and find_secret(text) is None   # no record data
    status = client.get("/connections").json()
    assert status["last_run"]["id"] == run_id
    assert status["serving"]["snapshot_id"] == run["snapshot_id"] and status["serving"]["row_counts"]["customers"] == 3

    assert client.post("/connections/nope/sync", headers=KEY).status_code == 404
    assert client.get("/connections/nope/runs").json()["runs"] == []


def test_sync_due_route_reports_an_outcome_a_scheduler_can_fail_on(provider, monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)
    engine, c, sf, store, runs = engine_for(provider)
    client = make_app(State(store, provider, engine.tokens, runs, engine))
    assert client.post("/connections/sync-due").status_code == 403

    r = client.post("/connections/sync-due?wait=true", headers=KEY)
    assert r.status_code == 200 and r.json()["outcome"] == "ok"
    (line,) = r.json()["connections"]
    assert line["action"] == "started" and line["run"]["status"] == "succeeded" and line["run"]["trigger"] == "schedule"

    sf.records["Account"][2]["Customer_Status__c"] = "Active"            # the next candidate degrades
    sf.records["Account"][2]["Churn_Date__c"] = None
    sf.records["Account"][2]["SystemModstamp"] = "2026-10-16T12:30:00Z"
    engine.now = Clock(NOON + timedelta(days=2))
    r = client.post("/connections/sync-due?wait=true", headers=KEY)
    assert r.status_code == 200 and r.json()["outcome"] == "attention"
    assert r.json()["connections"][0]["run"]["status"] == "degraded"


def test_rollback_route(provider, monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)
    clock = Clock(NOON)
    engine, c, sf, store, runs = engine_for(provider, clock=clock)
    client = make_app(State(store, provider, engine.tokens, runs, engine))

    assert client.post(f"/connections/{c.id}/rollback").status_code == 403
    r = client.post(f"/connections/{c.id}/rollback", headers=KEY)
    assert r.status_code == 409 and "Nothing is being served" in r.json()["detail"]

    client.post(f"/connections/{c.id}/sync", headers=KEY)
    first = runs.last_run(c.id).snapshot_id
    clock.at = NOON + timedelta(hours=1)
    sf.records["Account"][0]["SystemModstamp"] = "2026-10-16T12:30:00Z"
    client.post(f"/connections/{c.id}/sync", headers=KEY)
    r = client.post(f"/connections/{c.id}/rollback", headers=KEY)
    assert r.status_code == 200 and r.json()["serving"]["snapshot_id"] == first and r.json()["served"] is None
    assert client.get("/connections").json()["serving"]["snapshot_id"] == first
    assert client.post("/connections/nope/rollback", headers=KEY).status_code == 404
    assert client.post(f"/connections/{c.id}/rollback", headers=KEY,
                       json={"snapshot_id": "nope-nope"}).status_code == 409


def test_sync_route_says_why_it_cannot(provider, monkeypatch):
    monkeypatch.setenv("OPERATOR_KEY", "k" * 32)
    engine, c, sf, store, runs = engine_for(provider, FakeSalesforce(refresh_fail="invalid_grant"))
    client = make_app(State(store, provider, engine.tokens, runs, engine))
    client.post(f"/connections/{c.id}/sync", headers=KEY)        # fails, marks needs_reauth
    r = client.post(f"/connections/{c.id}/sync", headers=KEY)
    assert r.status_code == 409 and "invalid_grant" in r.json()["detail"]

    no_engine = make_app(State(store, provider, engine.tokens, runs, engine=None))
    assert no_engine.post(f"/connections/{c.id}/sync", headers=KEY).status_code == 503
    no_db = make_app(State(store, provider, tokens=None, runs=None))
    assert no_db.get(f"/connections/{c.id}/runs").json() == {"enabled": False, "runs": []}
