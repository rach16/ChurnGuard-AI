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
from integrations.sync import OBJECTS, SYNC_FIELDS, SyncEngine  # noqa: E402
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
    assert events[-3:] == ["sync_started", "refresh", "sync_finished"]
    assert store.audit[-3]["actor"] == "operator" and store.audit[-3]["detail"]["trigger"] == "manual"
    done = store.audit[-1]["detail"]
    assert done["status"] == "succeeded" and done["objects"]["Account"]["fetched"] == 3
    assert done["snapshot_id"] == snap.id and done["contracts"]["passed"] == 22
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
    assert runs.latest_good_snapshot() is None                       # never served
    assert all(runs.watermarks[(c.id, o)] == NOON for o in OBJECTS)  # the landing itself stood
    assert store.audit[-1]["detail"]["status"] == "degraded"


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
    assert client.get("/connections").json()["last_run"] is None

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
    assert client.get("/connections").json()["last_run"]["id"] == run_id

    assert client.post("/connections/nope/sync", headers=KEY).status_code == 404
    assert client.get("/connections/nope/runs").json()["runs"] == []


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
