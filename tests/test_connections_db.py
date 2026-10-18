"""
The connection store against a real Postgres (ADR-0012, section 3).

Runs only when DATABASE_URL is set (locally, from .env), and then in a throwaway
schema on that database: the real migrations are applied there and the schema is
dropped at the end, so the tests prove the SQL against the engine that runs it and
leave the staging tables untouched. CI has no database and skips this file; that
is the standing rule that CI needs no container, not a gap in coverage.
"""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from integrations import audit, crypto  # noqa: E402
from integrations.connections import ConnectionStore  # noqa: E402
from integrations.db import Database, redacted_url  # noqa: E402
from integrations.sessions import SessionStore, new_cookie_value  # noqa: E402
from integrations.sync_store import SyncStore  # noqa: E402

URL = os.getenv("DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="DATABASE_URL not set; needs a Postgres")

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
ORG = "00Dg700000J62qbEAB"
HOST = "https://orgfarm-e065461ef9-dev-ed.develop.my.salesforce.com"


@pytest.fixture(scope="module")
def db():
    schema = f"cg_test_{secrets.token_hex(4)}"
    import psycopg

    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"create schema {schema}")
    database = Database(URL, schema=schema).open()
    try:
        assert database.migrate() == ["0001_connections", "0002_sync", "0003_snapshots", "0004_serving", "0005_login", "0006_writeback"]
        assert database.migrate() == []                     # idempotent
        assert database.applied_migrations() == ["0001_connections", "0002_sync", "0003_snapshots", "0004_serving", "0005_login", "0006_writeback"]
        yield database
    finally:
        database.close()
        with psycopg.connect(URL, autocommit=True) as admin:
            admin.execute(f"drop schema {schema} cascade")


@pytest.fixture
def store(db):
    return ConnectionStore(db)


@pytest.fixture(scope="module")
def provider():
    """One key for the module: the rows these tests share were sealed by it."""
    return crypto.LocalKeyProvider({"v1": secrets.token_bytes(32)}, current="v1")


def _connect(store, conn, provider, org=ORG, token="5Aep" + "t" * 40):
    cid = store.new_id(conn)
    return store.upsert(conn, id=cid, tenant_id="default", org_id=org, instance_url=HOST,
                        login_host="login.salesforce.com", user_id="005xx000001",
                        username="r.banik16@example.com", scopes="api refresh_token",
                        sealed=crypto.seal(provider, cid, token), token_issued_at=NOW)


def test_url_redaction_hides_the_password():
    assert "***" in redacted_url("postgresql://u:p@h/db") and ":p@" not in redacted_url("postgresql://u:p@h/db")


def test_pool_closes_idle_connections_before_neon_does_and_checks_on_checkout():
    """Neon suspends after five idle minutes and kills connections; the pool must
    not hand one of those out. No database needed to assert the configuration."""
    from psycopg_pool import ConnectionPool

    db = Database("postgresql://u:p@localhost/x")
    assert db.pool.max_idle < 300
    assert db.pool._check is ConnectionPool.check_connection


@needs_db
def test_a_token_survives_the_round_trip_through_postgres(db, store, provider):
    with db.connection() as conn:
        created = _connect(store, conn, provider, token="5Aep" + "r" * 40)
    fetched = store.get()
    assert fetched is not None and fetched.id == created.id and fetched.status == "active"
    assert crypto.unseal(provider, fetched.id, fetched.sealed) == "5Aep" + "r" * 40
    assert "sealed" not in fetched.public() and "token_ciphertext" not in fetched.public()


@needs_db
def test_reconnecting_the_same_org_replaces_the_token_in_place(db, store, provider):
    with db.connection() as conn:
        first = store.get(conn=conn)
        again = store.upsert(conn, id=first.id, tenant_id="default", org_id=ORG, instance_url=HOST,
                             login_host="login.salesforce.com", user_id="005xx000002",
                             username="other@example.com", scopes="api refresh_token",
                             sealed=crypto.seal(provider, first.id, "5Aep" + "n" * 40),
                             token_issued_at=NOW)
    assert again.id == first.id and again.user_id == "005xx000002"
    assert crypto.unseal(provider, again.id, again.sealed) == "5Aep" + "n" * 40
    with db.connection() as conn:
        assert conn.execute("select count(*) as n from connections").fetchone()["n"] == 1


@needs_db
def test_status_lock_and_touch(db, store):
    with db.connection() as conn:
        c = store.get(conn=conn)
        assert store.try_lock(conn, c.id) is True
        store.set_status(conn, c.id, "needs_reauth", "invalid_grant on refresh")
        store.touch(conn, c.id, NOW)
    c = store.get()
    assert (c.status, c.status_reason, c.last_used_at) == ("needs_reauth", "invalid_grant on refresh", NOW)


@needs_db
def test_rewrap_changes_only_the_wrapped_key(db, store, provider):
    rotated = crypto.LocalKeyProvider({"v1": provider.keys["v1"], "v2": secrets.token_bytes(32)},
                                      current="v2")
    before = store.get()
    with db.connection() as conn:
        store.rewrap_token(conn, before.id, crypto.rewrap(rotated, before.id, before.sealed))
    after = store.get()
    assert after.sealed.master_key_version == "v2"
    assert after.sealed.ciphertext == before.sealed.ciphertext
    assert crypto.unseal(rotated, after.id, after.sealed) == crypto.unseal(provider, before.id, before.sealed)


@needs_db
def test_oauth_state_is_single_use_and_expires(db, store):
    with db.connection() as conn:
        store.create_state(conn, state="s1", code_verifier="v" * 43, login_host="login.salesforce.com",
                           redirect_uri="https://staging.example/api/auth/salesforce",
                           expected_org_id=ORG)
        store.create_state(conn, state="s2", code_verifier="v" * 43, login_host="login.salesforce.com",
                           redirect_uri="https://staging.example/api/auth/salesforce", ttl_seconds=-1)
    with db.connection() as conn:
        first = store.consume_state(conn, "s1")
        assert first and first["code_verifier"] == "v" * 43 and first["expected_org_id"] == ORG
        assert store.consume_state(conn, "s1") is None       # second presentation refused
        assert store.consume_state(conn, "s2") is None       # already expired
        assert store.consume_state(conn, "never-issued") is None
        assert store.purge_states(conn, older_than_seconds=0) >= 1


@needs_db
def test_audit_rows_are_written_and_read_back_newest_first(db, store):
    c = store.get()
    with db.connection() as conn:
        audit.record(conn, "connect", actor="operator", connection_id=c.id, org_id=ORG,
                     detail={"host": HOST, "scopes": "api refresh_token"})
        audit.record(conn, "disconnect", actor="operator", connection_id=c.id, org_id=ORG)
        rows = audit.recent(conn)
    assert [r["event"] for r in rows[:2]] == ["disconnect", "connect"]
    assert rows[1]["detail"]["host"] == HOST
    with db.connection() as conn, pytest.raises(ValueError):
        audit.record(conn, "connect", actor="operator", detail={"t": "5Aep" + "x" * 40})


@needs_db
def test_sync_rows_land_upsert_tombstone_and_watermark(db, store):
    """The 0002 tables against the real engine: a run row, a page landed twice
    without duplicating, a delete seen as a tombstone, the watermark, and the
    one-unfinished-run index that makes the row the lock."""
    import psycopg

    runs = SyncStore(db)
    c = store.get()
    with db.connection() as conn:
        run = runs.create_run(conn, connection_id=c.id, tenant_id="default", org_id=ORG,
                              trigger="manual", as_of=NOW.date())
        assert run.status == "pending" and runs.unfinished_run(conn, c.id).id == run.id
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                runs.create_run(conn, connection_id=c.id, tenant_id="default", org_id=ORG,
                                trigger="manual", as_of=NOW.date())
        runs.start_run(conn, run.id, NOW)

    page = [{"attributes": {"type": "Account"}, "Id": "001A", "Name": "Acme",
             "SystemModstamp": "2026-10-16T10:00:00.000+0000", "IsDeleted": False},
            {"attributes": {"type": "Account"}, "Id": "001B", "Name": "Bolt",
             "SystemModstamp": "2026-10-16T10:00:00.000+0000", "IsDeleted": False}]
    with db.connection() as conn:
        assert runs.land(conn, c.id, "Account", run.id, page) == (2, 0)
        assert runs.land(conn, c.id, "Account", run.id, page) == (2, 0)      # same Ids: upsert
        runs.set_watermark(conn, c.id, "Account", NOW, run.id)
        runs.record_object(conn, run.id, "Account", {"mode": "full", "fetched": 2, "tombstones": 0},
                           1, {"used": 1, "max": 15000})
    with db.connection() as conn:
        assert runs.counts(conn, c.id) == {"Account": {"live": 2, "tombstones": 0}}
        assert runs.get_watermark(conn, c.id, "Account") == NOW
        assert runs.get_watermark(conn, c.id, "Case") is None
        assert [r["Name"] for r in runs.live_records(conn, c.id, "Account")] == ["Acme", "Bolt"]
        assert "attributes" not in runs.live_records(conn, c.id, "Account")[0]

    gone = [{**page[1], "IsDeleted": True, "SystemModstamp": "2026-10-16T11:00:00.000+0000"}]
    with db.connection() as conn:
        assert runs.land(conn, c.id, "Account", run.id, gone) == (1, 1)
        runs.finish_run(conn, run.id, "succeeded", NOW, api_requests=2, api_usage={"used": 2, "max": 15000})
    with db.connection() as conn:
        assert runs.counts(conn, c.id) == {"Account": {"live": 1, "tombstones": 1}}
        assert [r["Name"] for r in runs.live_records(conn, c.id, "Account")] == ["Acme"]
        assert runs.unfinished_run(conn, c.id) is None
    done = runs.get_run(run.id)
    assert done.status == "succeeded" and done.api_requests == 2 and done.objects["Account"]["fetched"] == 2
    assert runs.last_run(c.id).id == run.id and done.public()["mode"] == "full"


@needs_db
def test_snapshots_round_trip_and_the_last_good_one_is_found(db, store):
    runs = SyncStore(db)
    c = store.get()
    run = runs.last_run(c.id)
    tables = {"customers": [{"customer_id": "A-1", "company_name": "Acme", "arr": "120000"}],
              "support_tickets": []}
    with db.connection() as conn:
        bad = runs.create_snapshot(conn, run_id=run.id, connection_id=c.id, tenant_id="default",
                                   as_of=NOW.date(), tables=tables, report={"customers_written": 1},
                                   contracts={"passed": 21, "failed": 1, "skipped": 6,
                                              "failures": ["churn rate is in a usable band: 0.0%"]})
    with db.connection() as conn:                       # a later run: its own transaction, later created_at
        good = runs.create_snapshot(conn, run_id=run.id, connection_id=c.id, tenant_id="default",
                                    as_of=NOW.date(), tables=tables, report={"customers_written": 1},
                                    contracts={"passed": 22, "failed": 0, "skipped": 6, "failures": []})
        runs.attach_snapshot(conn, run.id, good.id, good.contracts)
    assert bad.contracts_passed is False and good.contracts_passed is True
    assert good.row_counts == {"customers": 1, "support_tickets": 0} and good.tables == {}   # head only
    assert runs.latest_good_snapshot().id == good.id
    full = runs.get_snapshot(good.id)
    assert full.tables == tables and full.report == {"customers_written": 1}
    assert runs.get_snapshot(good.id, with_tables=False).tables == {}
    assert [s.id for s in runs.snapshots(c.id)] == [good.id, bad.id]
    attached = runs.get_run(run.id)
    assert attached.snapshot_id == good.id and attached.contracts["passed"] == 22
    assert "tables" not in good.public()


@needs_db
def test_the_serving_pointer_flips_atomically_and_remembers_the_previous(db, store):
    runs = SyncStore(db)
    c = store.get()
    good, bad = runs.snapshots(c.id)[0], runs.snapshots(c.id)[1]
    assert runs.get_serving() is None
    with db.connection() as conn:
        assert runs.set_serving(conn, tenant_id="default", snapshot_id=bad.id, run_id=bad.run_id,
                                actor="system") is None
    first = runs.get_serving()
    assert first.snapshot_id == bad.id and first.previous_snapshot_id is None and first.promoted_by == "system"
    with db.connection() as conn:
        assert runs.set_serving(conn, tenant_id="default", snapshot_id=good.id, run_id=good.run_id,
                                actor="operator") == bad.id
    second = runs.get_serving()
    assert second.snapshot_id == good.id and second.previous_snapshot_id == bad.id
    assert second.promoted_at >= first.promoted_at and second.promoted_by == "operator"
    with db.connection() as conn:
        assert conn.execute("select count(*) as n from serving").fetchone()["n"] == 1
    assert "tables" not in second.public() and second.public()["snapshot_id"] == good.id


@needs_db
def test_retention_prunes_old_snapshots_tombstones_and_orphans(db, store):
    runs = SyncStore(db)
    c = store.get()
    run = runs.last_run(c.id)
    with db.connection() as conn:
        for i in range(12):                                  # 12 more snapshots, oldest first
            conn.execute("insert into snapshots (connection_id, run_id, as_of, tables, report, contracts,"
                         " contracts_passed, row_counts, created_at) values (%s, %s, %s, '{}', '{}',"
                         " '{\"failures\": []}', true, '{}', now() - make_interval(days => %s))",
                         (c.id, run.id, NOW.date(), 30 - i))
        pointer = runs.get_serving()
        protect = [pointer.snapshot_id, pointer.previous_snapshot_id]
        before = conn.execute("select count(*) as n from snapshots").fetchone()["n"]
        doomed = runs.prune_snapshots(conn, "default", keep=10, protect=protect)
        after = conn.execute("select count(*) as n from snapshots").fetchone()["n"]
        assert before == 14 and after == 10                   # the protected pair is among the newest 10
        assert pointer.snapshot_id not in doomed and pointer.previous_snapshot_id not in doomed
        assert conn.execute("select count(*) as n from snapshots where id::text = any(%s)",
                            (protect,)).fetchone()["n"] == 2

        conn.execute("update raw_records set last_seen_at = now() - interval '31 days' where deleted")
        assert runs.prune_tombstones(conn, older_than_days=30) == 1
        assert runs.counts(conn, c.id) == {"Account": {"live": 1, "tombstones": 0}}

        # Rows of a connection that no longer exists, older than the window.
        ghost = "00000000-0000-0000-0000-000000000001"
        runs.land(conn, ghost, "Account", run.id, [{"Id": "001Z", "Name": "Zed", "IsDeleted": False}])
        runs.set_watermark(conn, ghost, "Account", NOW, run.id)
        assert runs.prune_orphans(conn, older_than_days=30) == {"connections": 0, "raw_records": 0,
                                                                "watermarks": 0, "runs": 0}   # too recent
        conn.execute("update raw_records set last_seen_at = now() - interval '31 days' where connection_id = %s",
                     (ghost,))
        assert runs.prune_orphans(conn, older_than_days=30) == {"connections": 1, "raw_records": 1,
                                                                "watermarks": 1, "runs": 0}
        assert runs.counts(conn, ghost) == {}


@needs_db
def test_metrics_sql_aggregates_the_run_rows(db, store):
    runs = SyncStore(db)
    c = store.get()
    with db.connection() as conn:
        conn.execute("insert into sync_runs (connection_id, org_id, trigger, status, as_of, objects, api_requests,"
                     " started_at, finished_at) values"
                     " (%s, %s, 'schedule', 'succeeded', %s, '{\"Account\": {\"fetched\": 7, \"tombstones\": 1}}', 5,"
                     "  now() - interval '2 hours', now() - interval '2 hours' + interval '3 seconds'),"
                     " (%s, %s, 'manual', 'failed', %s, '{}', 1,"
                     "  now() - interval '1 hour', now() - interval '1 hour' + interval '1 second')",
                     (c.id, ORG, NOW.date(), c.id, ORG, NOW.date()))
    m = runs.metrics(c.id, days=30)
    assert m["runs"] >= 3 and m["failed"] >= 1 and m["succeeded"] >= 2         # plus the earlier test's run
    assert m["records_fetched"] >= 9 and m["tombstones"] >= 1 and m["api_requests"] >= 8
    assert m["median_seconds"] is not None and m["last_succeeded_at"] is not None
    assert m["api_requests_per_day"] == round(m["api_requests"] / 30, 2)
    assert runs.metrics("00000000-0000-0000-0000-000000000009")["runs"] == 0


@needs_db
def test_sessions_round_trip_hashed_and_expiring(db, provider):
    from datetime import timedelta

    sessions = SessionStore(db)
    cookie = new_cookie_value()
    with db.connection() as conn:
        sid = sessions.new_id(conn)
        sealed = crypto.seal(provider, sid, "rt-" + "z" * 40, purpose=crypto.SESSION_PURPOSE)
        created = sessions.create(conn, id=sid, cookie_value=cookie, tenant_id="default",
                                  user_sub="sub-1", email="a@example.test", groups=("admin",),
                                  sealed_refresh=sealed, now=NOW)
        stored = conn.execute("select id_hash from sessions where id = %s", (sid,)).fetchone()["id_hash"]
    assert bytes(stored) != cookie.encode() and len(bytes(stored)) == 32        # only the hash
    assert created.role == "admin" and created.expires_at == NOW + timedelta(hours=8)

    got = sessions.get(cookie, now=NOW + timedelta(minutes=5))
    assert got.id == sid and got.groups == ("admin",)
    assert crypto.unseal(provider, sid, got.sealed_refresh, purpose=crypto.SESSION_PURPOSE) == "rt-" + "z" * 40
    assert sessions.get(cookie, now=NOW + timedelta(minutes=5)).last_seen_at == NOW + timedelta(minutes=5)
    assert sessions.get("wrong", now=NOW) is None

    with db.connection() as conn:
        sessions.recheck(conn, sid, ("viewer",), NOW + timedelta(minutes=6))
    assert sessions.get_by_id(sid).groups == ("viewer",)

    assert sessions.get(cookie, now=NOW + timedelta(hours=2)) is None             # idle: deleted
    assert sessions.get_by_id(sid) is None

    with db.connection() as conn:
        for _ in range(2):
            i = sessions.new_id(conn)
            sessions.create(conn, id=i, cookie_value=new_cookie_value(), tenant_id="default",
                            user_sub="sub-2", email="b@example.test", groups=("operator",),
                            sealed_refresh=crypto.seal(provider, i, "rt", purpose=crypto.SESSION_PURPOSE),
                            now=NOW)
        assert sessions.delete_for_user(conn, "sub-2") == 2


@needs_db
def test_a_sign_in_state_and_a_connect_state_cannot_stand_in_for_each_other(db, store):
    sessions = SessionStore(db)
    with db.connection() as conn:
        sessions.create_state(conn, state="login-1", verifier="v" * 43, nonce="n" * 43,
                              redirect_uri="https://site/api/auth/callback", return_to="/integrations",
                              login_host="https://x.auth.us-west-2.amazoncognito.com")
        store.create_state(conn, state="connect-1", code_verifier="v" * 43, login_host="login.salesforce.com",
                           redirect_uri="https://site/api/integrations/salesforce/callback")
    with db.connection() as conn:
        assert store.consume_state(conn, "login-1") is None                      # not a connect state
        assert sessions.consume_state(conn, "connect-1") is None                 # not a sign-in state
        got = sessions.consume_state(conn, "login-1")
        assert got["nonce"] == "n" * 43 and got["return_to"] == "/integrations"
        assert sessions.consume_state(conn, "login-1") is None                   # single use


@needs_db
def test_abandoned_runs_are_swept(db, store):
    runs = SyncStore(db)
    c = store.get()
    with db.connection() as conn:
        stuck = runs.create_run(conn, connection_id=c.id, tenant_id="default", org_id=ORG,
                                trigger="manual", as_of=NOW.date())
        conn.execute("update sync_runs set started_at = now() - interval '1 hour', status = 'running'"
                     " where id = %s", (stuck.id,))
        assert runs.sweep_abandoned(conn, older_than_seconds=1800) == [stuck.id]
        assert runs.sweep_abandoned(conn, older_than_seconds=1800) == []
    assert runs.get_run(stuck.id).status == "failed" and "abandoned" in runs.get_run(stuck.id).error


@needs_db
def test_writeback_rows_flag_and_account_ids(db, store):
    """The 0006 pieces against the real database: the per-connection switch,
    an attempt row written then finished, newest first, pruned to a count, and
    customer ids resolved from the landings (a shared AccountNumber resolves to
    nothing, a missing one to SF-<Id>)."""
    runs = SyncStore(db)
    c = store.get()
    assert c.writeback_enabled is False
    with db.connection() as conn:
        store.set_writeback(conn, c.id, True)
    assert store.get_by_id(c.id).writeback_enabled is True

    with db.connection() as conn:
        runs.land(conn, c.id, "Account", "00000000-0000-0000-0000-000000000001", [
            {"Id": "001W1", "AccountNumber": "W-1", "SystemModstamp": "2026-10-16T10:00:00.000+0000"},
            {"Id": "001W2", "AccountNumber": None, "SystemModstamp": "2026-10-16T10:00:00.000+0000"},
            {"Id": "001W3", "AccountNumber": "DUP", "SystemModstamp": "2026-10-16T10:00:00.000+0000"},
            {"Id": "001W4", "AccountNumber": "DUP", "SystemModstamp": "2026-10-16T10:00:00.000+0000"},
            {"Id": "001W5", "AccountNumber": "GONE", "SystemModstamp": "2026-10-16T10:00:00.000+0000",
             "IsDeleted": True},
        ])
        ids = runs.account_ids(conn, c.id)
    assert ids["W-1"] == "001W1" and ids["SF-001W2"] == "001W2"
    assert "DUP" not in ids and "GONE" not in ids

    with db.connection() as conn:
        first = runs.create_writeback(conn, connection_id=c.id, tenant_id="default", snapshot_id=None,
                                      trigger="manual", actor="user:op", started_at=NOW)
        runs.finish_writeback(conn, first, status="partial",
                              counts={"candidates": 3, "resolved": 2, "unchanged": 0, "written": 1, "failed": 1},
                              error="FIELD_CUSTOM_VALIDATION_EXCEPTION: locked", api_requests=2,
                              api_usage={"used": 5, "max": 15000}, finished_at=NOW + timedelta(seconds=3))
        second = runs.create_writeback(conn, connection_id=c.id, tenant_id="default", snapshot_id=None,
                                       trigger="promotion", actor="system", started_at=NOW + timedelta(minutes=1))
        runs.finish_writeback(conn, second, status="succeeded", counts={"written": 0}, error=None,
                              api_requests=1, api_usage=None, finished_at=NOW + timedelta(minutes=1, seconds=1))
    rows = runs.writebacks(c.id)
    assert [r.id for r in rows] == [second, first]
    assert rows[1].status == "partial" and rows[1].counts["failed"] == 1 and rows[1].api_usage["max"] == 15000
    assert runs.last_writeback(c.id).trigger == "promotion"
    with db.connection() as conn:
        assert runs.prune_writebacks(conn, c.id, keep=1) == 1
    assert [r.id for r in runs.writebacks(c.id)] == [second]


@needs_db
def test_delete_removes_the_connection(db, store):
    c = store.get()
    with db.connection() as conn:
        store.delete(conn, c.id)
    assert store.get() is None
