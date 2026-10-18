"""
Write-back to Salesforce (ADR-0014, 9.2.1), against the fake org.

Enable creates the fields the org lacks and leaves the ones it has; refuses
without the right to create fields or with a field of another type. A
promotion and a rollback write what the site shows, only where it changed,
200 a request, never to a churned account; a refused record fails alone; a
low API allowance defers; every attempt is a row and an audit event with
counts and never a value.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from integrations.sync import attention  # noqa: E402
from integrations.sync_store import _account_ids  # noqa: E402
from integrations.writeback import (  # noqa: E402
    BAND, FIELD_NAMES, REASON, SCORE, WriteBackEngine, WriteBackRefused, reading,
)
from tests.salesforce_fakes import FakeSalesforce  # noqa: E402
from tests.test_sync_engine import (  # noqa: E402
    KEY, NOON, Clock, State, engine_for, make_app, provider, site_origin,
)

__all__ = ["provider", "site_origin"]      # fixtures reused from the engine tests


def scores_for(snapshot) -> list[dict]:
    """What the site would show for the snapshot: the active customers, scored
    deterministically from their ARR so a test can predict every value."""
    out = []
    for c in snapshot.tables["customers"]:
        if str(c.get("is_churned")) in ("1", "True", "true"):
            continue
        arr = float(c["arr"])
        score = round(min(99.9, arr / 2000), 1)                 # 120000 → 60.0, 40000 → 20.0
        band = "Critical" if score >= 80 else "High" if score >= 60 else "Medium" if score >= 40 else "Low"
        out.append({"customer_id": c["customer_id"], "risk_score": score, "risk_level": band,
                    "risk_reason": "Low engagement"})
    return out


def with_writeback(provider, sf: FakeSalesforce | None = None, clock: Clock | None = None):
    """A connected org whose promotions and rollbacks write back, as api.py wires it."""
    engine, c, sf, store, runs = engine_for(provider, sf, clock)
    wb = WriteBackEngine(engine.tokens, store, runs, clock=engine.now)
    engine.on_promote = lambda snap: None                       # served (api.py restores and reloads)
    engine.on_served = lambda snap, trigger: wb.run(snap.connection_id, scored=scores_for(snap),
                                                    snapshot_id=snap.id, trigger=trigger)
    return engine, wb, c, sf, store, runs


def account(sf: FakeSalesforce, sf_id: str) -> dict:
    return next(r for r in sf.records["Account"] if r["Id"] == sf_id)


# ----------------------------------------------------------------- enabling

def test_enable_creates_the_missing_fields_grants_edit_and_switches_on(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    assert store.get_by_id(c.id).writeback_enabled is False

    result = wb.enable(c.id, actor="user:admin")

    assert result == {"enabled": True, "fields_created": list(FIELD_NAMES), "fields_existing": [],
                      "permissions_granted": list(FIELD_NAMES)}
    assert store.get_by_id(c.id).writeback_enabled is True
    created = {b["FullName"]: b["Metadata"] for b in sf.created_fields}
    assert created[f"Account.{SCORE}"]["type"] == "Number" and created[f"Account.{SCORE}"]["scale"] == 1
    assert [v["fullName"] for v in created[f"Account.{BAND}"]["valueSet"]["valueSetDefinition"]["value"]] == \
        ["Critical", "High", "Medium", "Low"]
    assert created[f"Account.{REASON}"]["length"] == 80
    assert all("ADR-0014" in m["description"] for m in created.values())
    assert all(sf.custom_fields[n]["updateable"] for n in FIELD_NAMES)
    assert [p["Field"] for p in sf.field_permissions] == [f"Account.{n}" for n in FIELD_NAMES]
    event = store.audit[-1]
    assert event["event"] == "writeback_enabled" and event["actor"] == "user:admin"
    assert event["detail"]["fields_created"] == list(FIELD_NAMES)


def test_enable_leaves_existing_fields_alone(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    for n in FIELD_NAMES:
        sf.custom_fields[n] = {"type": "double" if n == SCORE else "picklist" if n == BAND else "string",
                               "updateable": True}
    result = wb.enable(c.id, actor="user:admin")
    assert result["fields_created"] == [] and result["fields_existing"] == list(FIELD_NAMES)
    assert result["permissions_granted"] == []                 # already editable: nothing touched
    assert sf.created_fields == [] and sf.field_permissions == []
    assert store.get_by_id(c.id).writeback_enabled is True


def test_enable_refuses_when_the_user_cannot_create_fields(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    sf.can_customize = False
    with pytest.raises(WriteBackRefused, match="cannot create fields; ask a Salesforce admin"):
        wb.enable(c.id, actor="user:admin")
    assert store.get_by_id(c.id).writeback_enabled is False
    assert not any(a["event"].startswith("writeback") for a in store.audit)


def test_enable_refuses_a_field_of_another_type(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    sf.custom_fields[SCORE] = {"type": "string", "updateable": True}    # someone made it Text
    with pytest.raises(WriteBackRefused, match=f"{SCORE} is string"):
        wb.enable(c.id, actor="user:admin")
    assert store.get_by_id(c.id).writeback_enabled is False


def test_enable_refuses_a_connection_that_needs_a_login(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    with store.db.connection() as conn:
        store.set_status(conn, c.id, "needs_reauth", "The connection needs a fresh login.")
    with pytest.raises(WriteBackRefused, match="fresh login"):
        wb.enable(c.id, actor="user:admin")


def test_disable_switches_off_and_clears_nothing(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    wb.enable(c.id, actor="user:admin")
    engine.start(c.id)
    before = dict(account(sf, "001A"))
    assert wb.disable(c.id, actor="user:admin") == {"enabled": False}
    assert store.get_by_id(c.id).writeback_enabled is False
    assert account(sf, "001A") == before
    assert store.audit[-1]["event"] == "writeback_disabled"


# ------------------------------------------------------------------ writing

def test_off_by_default_a_promotion_writes_nothing(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "succeeded"
    assert sf.patches == [] and runs.writebacks(c.id) == []
    assert not any(a["event"] == "writeback" for a in store.audit)


def test_a_promotion_writes_the_sites_reading_to_each_active_account(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    wb.enable(c.id, actor="user:admin")
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "succeeded"

    assert account(sf, "001A")[SCORE] == 60.0 and account(sf, "001A")[BAND] == "High"
    assert account(sf, "001B")[SCORE] == 20.0 and account(sf, "001B")[BAND] == "Low"
    assert account(sf, "001A")[REASON] == "Low engagement"
    assert SCORE not in account(sf, "001C")                    # churned: not written
    assert len(sf.patches) == 1 and sf.patches[0]["allOrNone"] is False
    assert {r["Id"] for r in sf.patches[0]["records"]} == {"001A", "001B"}

    row = runs.last_writeback(c.id)
    assert row.status == "succeeded" and row.trigger == "promotion" and row.snapshot_id == run.snapshot_id
    assert row.counts == {"candidates": 2, "resolved": 2, "unchanged": 0, "written": 2, "failed": 0}
    assert row.api_requests == 2 and row.api_usage["max"] == 15000
    event = next(a for a in reversed(store.audit) if a["event"] == "writeback")
    assert event["detail"]["status"] == "succeeded" and event["detail"]["written"] == 2
    assert "60.0" not in str(event["detail"])                  # counts, never values


def test_an_unchanged_org_writes_nothing_the_second_time(provider):
    clock = Clock(NOON)
    engine, wb, c, sf, store, runs = with_writeback(provider, clock=clock)
    wb.enable(c.id, actor="user:admin")
    engine.start(c.id)
    clock.at = NOON + timedelta(hours=1)
    engine.start(c.id)
    assert len(sf.patches) == 1                                # the second promotion read, then skipped
    row = runs.last_writeback(c.id)
    assert row.status == "succeeded" and row.counts["unchanged"] == 2 and row.counts["written"] == 0
    assert row.api_requests == 1


def test_only_the_changed_account_is_written(provider):
    clock = Clock(NOON)
    engine, wb, c, sf, store, runs = with_writeback(provider, clock=clock)
    wb.enable(c.id, actor="user:admin")
    engine.start(c.id)
    clock.at = NOON + timedelta(hours=1)
    account(sf, "001A")["ARR__c"] = 200000                     # 100.0 → Critical
    account(sf, "001A")["SystemModstamp"] = "2026-10-16T12:30:00Z"
    engine.start(c.id)
    assert [r["Id"] for r in sf.patches[-1]["records"]] == ["001A"]
    assert account(sf, "001A")[SCORE] == 99.9 and account(sf, "001A")[BAND] == "Critical"
    assert runs.last_writeback(c.id).counts == {"candidates": 2, "resolved": 2, "unchanged": 1,
                                               "written": 1, "failed": 0}


def test_a_refused_record_fails_alone_and_the_page_is_told(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    wb.enable(c.id, actor="user:admin")
    sf.refuse_records["001B"] = ("FIELD_CUSTOM_VALIDATION_EXCEPTION", "Bolt is locked by a flow")
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "succeeded"                           # the site is served regardless
    assert account(sf, "001A")[SCORE] == 60.0 and SCORE not in account(sf, "001B")
    row = runs.last_writeback(c.id)
    assert row.status == "partial" and row.counts["written"] == 1 and row.counts["failed"] == 1
    assert row.error == "FIELD_CUSTOM_VALIDATION_EXCEPTION: Bolt is locked by a flow"
    needs = attention(store, runs)
    assert [n["kind"] for n in needs] == ["writeback_failed"]
    assert "wrote only some accounts" in needs[0]["reason"] and "Write back now" in needs[0]["reason"]


def test_a_low_api_allowance_defers_the_write(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    wb.enable(c.id, actor="user:admin")
    sf.api_used = 14000                                        # under 20% left after the read
    engine.start(c.id)
    assert sf.patches == []
    row = runs.last_writeback(c.id)
    assert row.status == "deferred" and "allowance low" in row.error
    assert attention(store, runs) == []                        # deferred is not a failure


def test_a_rollback_writes_the_readings_it_restores(provider):
    clock = Clock(NOON)
    engine, wb, c, sf, store, runs = with_writeback(provider, clock=clock)
    wb.enable(c.id, actor="user:admin")
    first = runs.get_run(engine.start(c.id).run.id)
    clock.at = NOON + timedelta(hours=1)
    account(sf, "001A")["ARR__c"] = 200000
    account(sf, "001A")["SystemModstamp"] = "2026-10-16T12:30:00Z"
    engine.start(c.id)
    assert account(sf, "001A")[SCORE] == 99.9

    clock.at = NOON + timedelta(hours=2)
    pointer, ok = engine.rollback(actor="user:op")
    assert ok and pointer.snapshot_id == first.snapshot_id
    assert account(sf, "001A")[SCORE] == 60.0                  # the org follows the site
    row = runs.last_writeback(c.id)
    assert row.trigger == "rollback" and row.snapshot_id == first.snapshot_id and row.counts["written"] == 1


def test_a_failed_write_never_touches_the_run_or_the_pointer(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    wb.enable(c.id, actor="user:admin")
    sf.custom_fields[SCORE]["updateable"] = False             # FLS removed after enabling
    run = runs.get_run(engine.start(c.id).run.id)
    assert run.status == "succeeded" and runs.get_serving().snapshot_id == run.snapshot_id
    row = runs.last_writeback(c.id)
    assert row.status == "failed" and "AssertionError" in row.error
    assert attention(store, runs)[0]["kind"] == "writeback_failed"


def test_writes_go_in_batches_of_two_hundred(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    wb.enable(c.id, actor="user:admin")
    with runs.db.connection() as conn:
        runs.land(conn, c.id, "Account", "run", [
            {"Id": f"001X{i:04d}", "AccountNumber": f"X-{i}", "IsDeleted": False} for i in range(450)])
    scored = [{"customer_id": f"X-{i}", "risk_score": 50.0, "risk_level": "Medium",
               "risk_reason": "Feature gaps"} for i in range(450)]
    row = wb.run(c.id, scored=scored, snapshot_id=None, trigger="manual", actor="user:op")
    assert row.status == "succeeded" and row.counts["written"] == 450
    assert [len(p["records"]) for p in sf.patches] == [200, 200, 50]
    assert row.api_requests == 6


def test_a_manual_run_on_a_disabled_connection_is_refused(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    with pytest.raises(WriteBackRefused, match="Enable it first"):
        wb.run(c.id, scored=[], snapshot_id=None, trigger="manual", actor="user:op", force=True)
    assert wb.run(c.id, scored=[], snapshot_id=None, trigger="promotion") is None


def test_only_the_last_ten_attempts_are_kept(provider):
    clock = Clock(NOON)
    engine, wb, c, sf, store, runs = with_writeback(provider, clock=clock)
    wb.enable(c.id, actor="user:admin")
    for i in range(12):
        clock.at = NOON + timedelta(minutes=i)
        wb.run(c.id, scored=[], snapshot_id=None, trigger="manual", actor="user:op", force=True)
    assert len(runs.writebacks(c.id, limit=100)) == 10


# ------------------------------------------------------------ resolving ids

def test_account_ids_follow_the_connectors_naming_and_never_guess():
    ids = _account_ids([("001A", "A-1"), ("001B", None), ("001C", "DUP"), ("001D", "DUP")])
    assert ids == {"A-1": "001A", "SF-001B": "001B"}


def test_an_unresolved_customer_is_counted_not_written(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    wb.enable(c.id, actor="user:admin")
    engine.start(c.id)
    scored = scores_for(runs.get_snapshot(runs.get_serving().snapshot_id))
    scored.append({"customer_id": "Z-9", "risk_score": 1.0, "risk_level": "Low", "risk_reason": "Feature gaps"})
    row = wb.run(c.id, scored=scored, snapshot_id=None, trigger="manual", actor="user:op")
    assert row.counts["candidates"] == 3 and row.counts["resolved"] == 2


def test_reading_is_exactly_what_the_site_shows():
    assert reading({"risk_score": 51.84, "risk_level": "Medium", "risk_reason": "Support issues"}) == \
        {SCORE: 51.8, BAND: "Medium", REASON: "Support issues"}


# ------------------------------------------------------------------- routes

class Scorer:
    def __init__(self, scored):
        self.scored = scored

    def score_active_customers(self):
        return self.scored


def test_writeback_routes_by_role(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    state = State(store, provider, engine.tokens, runs, engine)
    state.writeback = wb
    state.health_scorer = Scorer([])

    viewer = make_app(state, groups=("viewer",))
    r = viewer.get(f"/connections/{c.id}/writeback")
    assert r.status_code == 200 and r.json() == {"enabled": False, "fields": list(FIELD_NAMES), "last": None}
    assert viewer.post(f"/connections/{c.id}/writeback", headers=KEY).status_code == 403

    operator = make_app(state, groups=("operator",))
    assert operator.post(f"/connections/{c.id}/writeback", headers=KEY).status_code == 403
    r = operator.post(f"/connections/{c.id}/writeback/run", headers=KEY)
    assert r.status_code == 409 and "Enable it first" in r.json()["detail"]

    admin = make_app(state, groups=("admin",))          # each sign-in replaces the last client's session
    r = admin.post(f"/connections/{c.id}/writeback", headers=KEY)
    assert r.status_code == 200 and r.json()["fields_created"] == list(FIELD_NAMES)
    assert admin.get(f"/connections/{c.id}/writeback").json()["enabled"] is True

    engine.start(c.id)
    state.health_scorer = Scorer(scores_for(runs.get_snapshot(runs.get_serving().snapshot_id)))
    operator = make_app(state, groups=("operator",))
    r = operator.post(f"/connections/{c.id}/writeback/run", headers=KEY)
    assert r.status_code == 200
    body = r.json()["writeback"]
    assert body["trigger"] == "manual" and body["status"] == "succeeded" and body["actor"].startswith("user:")
    assert body["snapshot_id"] == runs.get_serving().snapshot_id

    admin = make_app(state, groups=("admin",))
    assert admin.get(f"/connections/{c.id}/writeback").json()["last"]["status"] == "succeeded"
    assert admin.delete(f"/connections/{c.id}/writeback", headers=KEY).json() == {"enabled": False}
    assert admin.get("/connections").json()["connection"]["writeback_enabled"] is False


def test_enable_refusal_reaches_the_page(provider):
    engine, wb, c, sf, store, runs = with_writeback(provider)
    sf.can_customize = False
    state = State(store, provider, engine.tokens, runs, engine)
    state.writeback = wb
    admin = make_app(state)
    r = admin.post(f"/connections/{c.id}/writeback", headers=KEY)
    assert r.status_code == 409 and "ask a Salesforce admin" in r.json()["detail"]
    assert admin.get(f"/connections/{c.id}/writeback").json()["enabled"] is False
