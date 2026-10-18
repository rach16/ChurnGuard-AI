"""
Rows behind a sync: the run, the raw landings, the watermarks (ADR-0012, section 5).

A store like ``connections.py``: reads and writes, no decisions. The engine in
``sync.py`` decides what to read from Salesforce and when a run has failed; this
records what it decided.

Two properties the SQL is shaped around:

- **A watermark and the records it covers are one transaction.** ``land`` and
  ``set_watermark`` are called on the same connection, which commits once per
  object. A crash between them cannot leave a watermark that claims records the
  table does not hold.
- **Landing is an upsert by Salesforce Id.** The overlap window re-reads records
  near the watermark on every run, and a resumed run re-reads an object from its
  last committed watermark. Neither can duplicate a row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Optional

from integrations.db import Database

# Everything an endpoint may show about a run. No column here can hold a token.
_RUN_COLUMNS = ("id, tenant_id, connection_id, org_id, trigger, status, as_of, objects,"
                " api_requests, api_usage, error, snapshot_id, contracts, created_at, started_at,"
                " finished_at")


@dataclass(frozen=True)
class SyncRun:
    id: str
    tenant_id: str
    connection_id: str
    org_id: str
    trigger: str
    status: str
    as_of: date
    objects: dict[str, Any]
    api_requests: int
    api_usage: Optional[dict[str, Any]]
    error: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    snapshot_id: Optional[str] = None
    contracts: Optional[dict[str, Any]] = None

    @property
    def unfinished(self) -> bool:
        return self.status in ("pending", "running")

    def public(self) -> dict[str, Any]:
        duration = None
        if self.started_at and self.finished_at:
            duration = round((self.finished_at - self.started_at).total_seconds(), 1)
        modes = {o.get("mode") for o in self.objects.values()}
        return {
            "id": self.id, "connection_id": self.connection_id, "org_id": self.org_id,
            "trigger": self.trigger, "status": self.status, "as_of": self.as_of,
            "mode": ("full" if modes == {"full"} else "incremental" if modes else None),
            "objects": self.objects,
            "records_fetched": sum(o.get("fetched", 0) for o in self.objects.values()),
            "tombstones": sum(o.get("tombstones", 0) for o in self.objects.values()),
            "api_requests": self.api_requests, "api_usage": self.api_usage,
            "error": self.error, "snapshot_id": self.snapshot_id, "contracts": self.contracts,
            "created_at": self.created_at,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "duration_seconds": duration,
        }


def _row_to_run(r: dict[str, Any]) -> SyncRun:
    return SyncRun(
        id=str(r["id"]), tenant_id=r["tenant_id"], connection_id=str(r["connection_id"]),
        org_id=r["org_id"], trigger=r["trigger"], status=r["status"], as_of=r["as_of"],
        objects=dict(r["objects"] or {}), api_requests=r["api_requests"],
        api_usage=r["api_usage"], error=r["error"], created_at=r["created_at"],
        started_at=r["started_at"], finished_at=r["finished_at"],
        snapshot_id=str(r["snapshot_id"]) if r.get("snapshot_id") else None,
        contracts=r.get("contracts"),
    )


@dataclass(frozen=True)
class Snapshot:
    id: str
    tenant_id: str
    connection_id: str
    run_id: str
    as_of: date
    tables: dict[str, list[dict[str, Any]]]
    report: dict[str, Any]
    contracts: dict[str, Any]
    contracts_passed: bool
    row_counts: dict[str, int]
    created_at: datetime

    def public(self) -> dict[str, Any]:
        """Everything but the tables."""
        return {"id": self.id, "connection_id": self.connection_id, "run_id": self.run_id,
                "as_of": self.as_of, "contracts": self.contracts,
                "contracts_passed": self.contracts_passed, "row_counts": self.row_counts,
                "created_at": self.created_at}


@dataclass(frozen=True)
class Serving:
    tenant_id: str
    snapshot_id: str
    run_id: Optional[str]
    previous_snapshot_id: Optional[str]
    promoted_at: datetime
    promoted_by: str

    def public(self) -> dict[str, Any]:
        return {"snapshot_id": self.snapshot_id, "run_id": self.run_id,
                "previous_snapshot_id": self.previous_snapshot_id,
                "promoted_at": self.promoted_at, "promoted_by": self.promoted_by}


@dataclass(frozen=True)
class WriteBack:
    """One attempt to put the site's readings on the org (ADR-0014 section 7)."""
    id: str
    tenant_id: str
    connection_id: str
    snapshot_id: Optional[str]
    trigger: str
    status: str
    counts: dict[str, int]
    error: Optional[str]
    api_requests: int
    api_usage: Optional[dict[str, int]]
    actor: str
    started_at: datetime
    finished_at: Optional[datetime]

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "snapshot_id": self.snapshot_id, "trigger": self.trigger,
                "status": self.status, "counts": self.counts, "error": self.error,
                "api_requests": self.api_requests, "api_usage": self.api_usage,
                "actor": self.actor, "started_at": self.started_at, "finished_at": self.finished_at}


def _account_ids(rows: Iterable[tuple[str, Optional[str]]]) -> dict[str, str]:
    """Shared with the fake store so both resolve the same way."""
    by_number: dict[str, list[str]] = {}
    out: dict[str, str] = {}
    for sf_id, number in rows:
        if number:
            by_number.setdefault(number, []).append(sf_id)
        else:
            out[f"SF-{sf_id}"] = sf_id
    for number, ids in by_number.items():
        if len(ids) == 1:
            out[number] = ids[0]
    return out


_WRITEBACK_COLUMNS = ("id, tenant_id, connection_id, snapshot_id, trigger, status, counts, error,"
                      " api_requests, api_usage, actor, started_at, finished_at")


def _row_to_writeback(r: dict[str, Any]) -> WriteBack:
    return WriteBack(
        id=str(r["id"]), tenant_id=r["tenant_id"], connection_id=str(r["connection_id"]),
        snapshot_id=str(r["snapshot_id"]) if r["snapshot_id"] else None, trigger=r["trigger"],
        status=r["status"], counts=r["counts"] or {}, error=r["error"],
        api_requests=r["api_requests"], api_usage=r["api_usage"], actor=r["actor"],
        started_at=r["started_at"], finished_at=r["finished_at"],
    )


_SNAPSHOT_COLUMNS = ("id, tenant_id, connection_id, run_id, as_of, tables, report, contracts,"
                     " contracts_passed, row_counts, created_at")
_SNAPSHOT_HEAD = ("id, tenant_id, connection_id, run_id, as_of, null::jsonb as tables, report,"
                  " contracts, contracts_passed, row_counts, created_at")


def _row_to_snapshot(r: dict[str, Any]) -> Snapshot:
    return Snapshot(
        id=str(r["id"]), tenant_id=r["tenant_id"], connection_id=str(r["connection_id"]),
        run_id=str(r["run_id"]), as_of=r["as_of"], tables=r["tables"] or {}, report=r["report"],
        contracts=r["contracts"], contracts_passed=r["contracts_passed"],
        row_counts=r["row_counts"], created_at=r["created_at"],
    )


class SyncStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------- runs

    def create_run(self, conn, *, connection_id: str, tenant_id: str, org_id: str,
                   trigger: str, as_of: date) -> SyncRun:
        row = conn.execute(
            f"insert into sync_runs (tenant_id, connection_id, org_id, trigger, as_of)"
            f" values (%s, %s, %s, %s, %s) returning {_RUN_COLUMNS}",
            (tenant_id, connection_id, org_id, trigger, as_of),
        ).fetchone()
        return _row_to_run(row)

    def get_run(self, run_id: str, conn=None) -> Optional[SyncRun]:
        sql = f"select {_RUN_COLUMNS} from sync_runs where id = %s"
        if conn is not None:
            row = conn.execute(sql, (run_id,)).fetchone()
        else:
            with self.db.connection() as c:
                row = c.execute(sql, (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def unfinished_run(self, conn, connection_id: str) -> Optional[SyncRun]:
        row = conn.execute(
            f"select {_RUN_COLUMNS} from sync_runs where connection_id = %s"
            " and status in ('pending', 'running') order by created_at desc limit 1",
            (connection_id,),
        ).fetchone()
        return _row_to_run(row) if row else None

    def runs(self, connection_id: str, limit: int = 10, conn=None) -> list[SyncRun]:
        """Newest first."""
        sql = (f"select {_RUN_COLUMNS} from sync_runs where connection_id = %s"
               " order by created_at desc limit %s")
        if conn is not None:
            rows = conn.execute(sql, (connection_id, limit)).fetchall()
        else:
            with self.db.connection() as c:
                rows = c.execute(sql, (connection_id, limit)).fetchall()
        return [_row_to_run(r) for r in rows]

    def last_run(self, connection_id: str, conn=None) -> Optional[SyncRun]:
        found = self.runs(connection_id, limit=1, conn=conn)
        return found[0] if found else None

    def start_run(self, conn, run_id: str, started_at: datetime) -> None:
        conn.execute("update sync_runs set status = 'running', started_at = %s where id = %s",
                     (started_at, run_id))

    def record_object(self, conn, run_id: str, sobject: str, summary: dict[str, Any],
                      api_requests: int, api_usage: Optional[dict[str, Any]]) -> None:
        """One object's outcome, merged into the run's ``objects`` as it completes,
        so a run that fails on the third object still shows the first two."""
        from psycopg.types.json import Jsonb

        conn.execute(
            "update sync_runs set objects = objects || %s, api_requests = %s, api_usage = %s"
            " where id = %s",
            (Jsonb({sobject: summary}), api_requests, Jsonb(api_usage) if api_usage else None,
             run_id),
        )

    def finish_run(self, conn, run_id: str, status: str, finished_at: datetime,
                   error: Optional[str] = None, api_requests: Optional[int] = None,
                   api_usage: Optional[dict[str, Any]] = None) -> None:
        from psycopg.types.json import Jsonb

        conn.execute(
            "update sync_runs set status = %s, finished_at = %s, error = %s,"
            " api_requests = coalesce(%s, api_requests), api_usage = coalesce(%s, api_usage)"
            " where id = %s",
            (status, finished_at, error, api_requests,
             Jsonb(api_usage) if api_usage else None, run_id),
        )

    def sweep_abandoned(self, conn, older_than_seconds: int) -> list[str]:
        """Runs still pending or running after too long belong to a process that
        died. Marked failed so the next trigger can start, and so the page says
        'abandoned' rather than 'running' forever. Returns the ids swept."""
        rows = conn.execute(
            "update sync_runs set status = 'failed', finished_at = now(),"
            " error = 'abandoned: the process stopped before the run finished'"
            " where status in ('pending', 'running')"
            " and coalesce(started_at, created_at) < now() - make_interval(secs => %s)"
            " returning id",
            (older_than_seconds,),
        ).fetchall()
        return [str(r["id"]) for r in rows]

    # ------------------------------------------------------------- watermarks

    def get_watermark(self, conn, connection_id: str, sobject: str) -> Optional[datetime]:
        row = conn.execute(
            "select watermark from sync_watermarks where connection_id = %s and sobject = %s",
            (connection_id, sobject),
        ).fetchone()
        return row["watermark"] if row else None

    def set_watermark(self, conn, connection_id: str, sobject: str, watermark: datetime,
                      run_id: str) -> None:
        conn.execute(
            "insert into sync_watermarks (connection_id, sobject, watermark, run_id)"
            " values (%s, %s, %s, %s)"
            " on conflict (connection_id, sobject) do update set"
            " watermark = excluded.watermark, run_id = excluded.run_id, updated_at = now()",
            (connection_id, sobject, watermark, run_id),
        )

    # ------------------------------------------------------------ raw records

    def land(self, conn, connection_id: str, sobject: str, run_id: str,
             records: Iterable[dict[str, Any]]) -> tuple[int, int]:
        """Upsert a page of records by Id. Returns (landed, tombstones).

        ``IsDeleted`` on a record (from ``queryAll``) makes it a tombstone; a
        record seen alive again is revived, which is what Salesforce's own
        undelete looks like from here.
        """
        from psycopg.types.json import Jsonb

        rows = []
        tombstones = 0
        for rec in records:
            body = {k: v for k, v in rec.items() if k != "attributes"}
            deleted = bool(body.get("IsDeleted"))
            tombstones += deleted
            rows.append((connection_id, sobject, body["Id"], Jsonb(body),
                         body.get("SystemModstamp"), deleted, run_id, run_id))
        if rows:
            conn.cursor().executemany(
                "insert into raw_records (connection_id, sobject, sf_id, record, system_modstamp,"
                " deleted, first_run_id, last_run_id) values (%s, %s, %s, %s, %s, %s, %s, %s)"
                " on conflict (connection_id, sobject, sf_id) do update set"
                " record = excluded.record, system_modstamp = excluded.system_modstamp,"
                " deleted = excluded.deleted, last_run_id = excluded.last_run_id,"
                " last_seen_at = now()",
                rows,
            )
        return len(rows), tombstones

    def live_records(self, conn, connection_id: str, sobject: str) -> list[dict[str, Any]]:
        """Every record not tombstoned, as Salesforce returned it. What the
        transform (1.10.2) reads."""
        rows = conn.execute(
            "select record from raw_records where connection_id = %s and sobject = %s"
            " and not deleted order by sf_id",
            (connection_id, sobject),
        ).fetchall()
        return [r["record"] for r in rows]

    def counts(self, conn, connection_id: str) -> dict[str, dict[str, int]]:
        rows = conn.execute(
            "select sobject, count(*) filter (where not deleted) as live,"
            " count(*) filter (where deleted) as tombstones"
            " from raw_records where connection_id = %s group by sobject order by sobject",
            (connection_id,),
        ).fetchall()
        return {r["sobject"]: {"live": r["live"], "tombstones": r["tombstones"]} for r in rows}

    # -------------------------------------------------------------- snapshots

    def create_snapshot(self, conn, *, run_id: str, connection_id: str, tenant_id: str,
                        as_of: date, tables: dict[str, list[dict]], report: dict[str, Any],
                        contracts: dict[str, Any]) -> Snapshot:
        from psycopg.types.json import Jsonb

        passed = not contracts.get("failures")
        counts = {name: len(rows) for name, rows in tables.items()}
        row = conn.execute(
            f"insert into snapshots (tenant_id, connection_id, run_id, as_of, tables, report,"
            f" contracts, contracts_passed, row_counts) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            f" returning {_SNAPSHOT_HEAD}",
            (tenant_id, connection_id, run_id, as_of, Jsonb(tables), Jsonb(report),
             Jsonb(contracts), passed, Jsonb(counts)),
        ).fetchone()
        return _row_to_snapshot(row)

    def attach_snapshot(self, conn, run_id: str, snapshot_id: str, contracts: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        conn.execute("update sync_runs set snapshot_id = %s, contracts = %s where id = %s",
                     (snapshot_id, Jsonb(contracts), run_id))

    def get_snapshot(self, snapshot_id: str, conn=None, *, with_tables: bool = True) -> Optional[Snapshot]:
        cols = _SNAPSHOT_COLUMNS if with_tables else _SNAPSHOT_HEAD
        sql = f"select {cols} from snapshots where id = %s"
        if conn is not None:
            row = conn.execute(sql, (snapshot_id,)).fetchone()
        else:
            with self.db.connection() as c:
                row = c.execute(sql, (snapshot_id,)).fetchone()
        return _row_to_snapshot(row) if row else None

    def latest_good_snapshot(self, tenant_id: str = "default", conn=None, *,
                             with_tables: bool = False) -> Optional[Snapshot]:
        """The newest snapshot whose contracts all passed: the last good sync."""
        cols = _SNAPSHOT_COLUMNS if with_tables else _SNAPSHOT_HEAD
        sql = (f"select {cols} from snapshots where tenant_id = %s and contracts_passed"
               " order by created_at desc limit 1")
        if conn is not None:
            row = conn.execute(sql, (tenant_id,)).fetchone()
        else:
            with self.db.connection() as c:
                row = c.execute(sql, (tenant_id,)).fetchone()
        return _row_to_snapshot(row) if row else None

    def snapshots(self, connection_id: str, limit: int = 10, conn=None) -> list[Snapshot]:
        """Newest first, without their tables."""
        sql = (f"select {_SNAPSHOT_HEAD} from snapshots where connection_id = %s"
               " order by created_at desc limit %s")
        if conn is not None:
            rows = conn.execute(sql, (connection_id, limit)).fetchall()
        else:
            with self.db.connection() as c:
                rows = c.execute(sql, (connection_id, limit)).fetchall()
        return [_row_to_snapshot(r) for r in rows]

    # ---------------------------------------------------------------- serving

    def get_serving(self, tenant_id: str = "default", conn=None) -> Optional[Serving]:
        sql = ("select tenant_id, snapshot_id::text, run_id::text, previous_snapshot_id::text,"
               " promoted_at, promoted_by from serving where tenant_id = %s")
        if conn is not None:
            row = conn.execute(sql, (tenant_id,)).fetchone()
        else:
            with self.db.connection() as c:
                row = c.execute(sql, (tenant_id,)).fetchone()
        return Serving(**row) if row else None

    def set_serving(self, conn, *, tenant_id: str, snapshot_id: str, run_id: Optional[str],
                    actor: str) -> Optional[str]:
        """Point the tenant at a snapshot. Returns the snapshot id it pointed at
        before, or None. One statement, so the flip is atomic with whatever
        else the transaction records."""
        before = conn.execute("select snapshot_id::text as id from serving where tenant_id = %s",
                              (tenant_id,)).fetchone()
        previous = before["id"] if before else None
        conn.execute(
            "insert into serving (tenant_id, snapshot_id, run_id, previous_snapshot_id, promoted_by)"
            " values (%s, %s, %s, %s, %s)"
            " on conflict (tenant_id) do update set snapshot_id = excluded.snapshot_id,"
            " run_id = excluded.run_id, previous_snapshot_id = excluded.previous_snapshot_id,"
            " promoted_at = now(), promoted_by = excluded.promoted_by",
            (tenant_id, snapshot_id, run_id, previous, actor),
        )
        return previous

    # -------------------------------------------------------------- write-back

    def account_ids(self, conn, connection_id: str) -> dict[str, str]:
        """``customer_id`` → Salesforce Account Id, from the live landings
        (ADR-0014 section 2): the connector names a customer by AccountNumber,
        or ``SF-<Id>`` without one. An AccountNumber two live records share
        resolves to neither; the write skips it rather than guess."""
        rows = conn.execute(
            "select sf_id, record->>'AccountNumber' as number from raw_records"
            " where connection_id = %s and sobject = 'Account' and not deleted",
            (connection_id,),
        ).fetchall()
        return _account_ids([(r["sf_id"], r["number"]) for r in rows])

    def create_writeback(self, conn, *, connection_id: str, tenant_id: str,
                         snapshot_id: Optional[str], trigger: str, actor: str,
                         started_at: datetime) -> str:
        row = conn.execute(
            "insert into writebacks (tenant_id, connection_id, snapshot_id, trigger, status, actor,"
            " started_at) values (%s, %s, %s, %s, 'failed', %s, %s) returning id::text",
            (tenant_id, connection_id, snapshot_id, trigger, actor, started_at),
        ).fetchone()
        return row["id"]

    def finish_writeback(self, conn, writeback_id: str, *, status: str, counts: dict[str, int],
                         error: Optional[str], api_requests: int,
                         api_usage: Optional[dict[str, int]], finished_at: datetime) -> None:
        from psycopg.types.json import Jsonb
        conn.execute(
            "update writebacks set status = %s, counts = %s, error = %s, api_requests = %s,"
            " api_usage = %s, finished_at = %s where id = %s",
            (status, Jsonb(counts), error, api_requests,
             Jsonb(api_usage) if api_usage is not None else None, finished_at, writeback_id),
        )

    def writebacks(self, connection_id: str, limit: int = 10, conn=None) -> list[WriteBack]:
        """Newest first."""
        sql = (f"select {_WRITEBACK_COLUMNS} from writebacks where connection_id = %s"
               " order by started_at desc limit %s")
        if conn is not None:
            rows = conn.execute(sql, (connection_id, limit)).fetchall()
        else:
            with self.db.connection() as c:
                rows = c.execute(sql, (connection_id, limit)).fetchall()
        return [_row_to_writeback(r) for r in rows]

    def last_writeback(self, connection_id: str, conn=None) -> Optional[WriteBack]:
        found = self.writebacks(connection_id, limit=1, conn=conn)
        return found[0] if found else None

    def prune_writebacks(self, conn, connection_id: str, keep: int) -> int:
        return conn.execute(
            "delete from writebacks where connection_id = %s and id not in"
            " (select id from writebacks where connection_id = %s order by started_at desc limit %s)",
            (connection_id, connection_id, keep),
        ).rowcount

    # -------------------------------------------------------------- retention

    def prune_snapshots(self, conn, tenant_id: str, keep: int, protect: list[str]) -> list[str]:
        """Delete every snapshot older than the newest ``keep`` for the tenant,
        except those in ``protect`` (the serving one and its predecessor, so a
        rollback target cannot be retired from under the pointer)."""
        rows = conn.execute(
            "delete from snapshots where tenant_id = %s"
            " and id not in (select id from snapshots where tenant_id = %s"
            "                order by created_at desc, id desc limit %s)"
            " and not (id::text = any(%s)) returning id::text",
            (tenant_id, tenant_id, keep, protect),
        ).fetchall()
        return [r["id"] for r in rows]

    def prune_tombstones(self, conn, older_than_days: int) -> int:
        """A tombstone has done its work once the record has been absent from a
        promoted candidate; after the retention window the row itself goes."""
        cur = conn.execute(
            "delete from raw_records where deleted and last_seen_at < now() - make_interval(days => %s)",
            (older_than_days,),
        )
        return cur.rowcount

    def prune_orphans(self, conn, older_than_days: int) -> dict[str, int]:
        """Rows belonging to a connection that no longer exists (disconnected),
        once the recovery window has passed (ADR-0012 section 7)."""
        gone = ("select distinct r.connection_id from raw_records r"
                " where not exists (select 1 from connections c where c.id = r.connection_id)"
                " and not exists (select 1 from raw_records n where n.connection_id = r.connection_id"
                "                 and n.last_seen_at >= now() - make_interval(days => %s))")
        ids = [r["connection_id"] for r in conn.execute(gone, (older_than_days,)).fetchall()]
        out = {"connections": len(ids), "raw_records": 0, "watermarks": 0, "runs": 0}
        if not ids:
            return out
        out["raw_records"] = conn.execute(
            "delete from raw_records where connection_id = any(%s)", (ids,)).rowcount
        out["watermarks"] = conn.execute(
            "delete from sync_watermarks where connection_id = any(%s)", (ids,)).rowcount
        out["runs"] = conn.execute(
            "delete from sync_runs where connection_id = any(%s) and status not in ('pending', 'running')"
            " and created_at < now() - make_interval(days => %s)", (ids, older_than_days)).rowcount
        return out

    # ---------------------------------------------------------------- metrics

    def metrics(self, connection_id: str, days: int = 30, conn=None) -> dict[str, Any]:
        """The last ``days`` of runs for a connection, as the Integrations page
        shows them: counts by outcome, records changed, API calls, durations."""
        sql = """
            select count(*) as runs,
                   count(*) filter (where status = 'succeeded') as succeeded,
                   count(*) filter (where status = 'failed') as failed,
                   count(*) filter (where status = 'degraded') as degraded,
                   count(*) filter (where trigger = 'manual') as manual,
                   coalesce(sum((select coalesce(sum((o.value->>'fetched')::int), 0)
                                 from jsonb_each(objects) o)), 0) as records_fetched,
                   coalesce(sum((select coalesce(sum((o.value->>'tombstones')::int), 0)
                                 from jsonb_each(objects) o)), 0) as tombstones,
                   coalesce(sum(api_requests), 0) as api_requests,
                   percentile_cont(0.5) within group
                       (order by extract(epoch from finished_at - started_at))
                       filter (where finished_at is not null and started_at is not null)
                       as median_seconds,
                   max(finished_at) filter (where status = 'succeeded') as last_succeeded_at,
                   max(finished_at) as last_finished_at
            from sync_runs
            where connection_id = %s and created_at >= now() - make_interval(days => %s)
        """
        if conn is not None:
            row = conn.execute(sql, (connection_id, days)).fetchone()
        else:
            with self.db.connection() as c:
                row = c.execute(sql, (connection_id, days)).fetchone()
        return _shape_metrics(dict(row), days)


def _shape_metrics(row: dict[str, Any], days: int) -> dict[str, Any]:
    runs = int(row.get("runs") or 0)
    succeeded = int(row.get("succeeded") or 0)
    median = row.get("median_seconds")
    return {
        "days": days, "runs": runs, "succeeded": succeeded,
        "failed": int(row.get("failed") or 0), "degraded": int(row.get("degraded") or 0),
        "manual": int(row.get("manual") or 0),
        "success_rate": round(succeeded / runs, 3) if runs else None,
        "records_fetched": int(row.get("records_fetched") or 0),
        "tombstones": int(row.get("tombstones") or 0),
        "api_requests": int(row.get("api_requests") or 0),
        "api_requests_per_day": round(int(row.get("api_requests") or 0) / days, 2),
        "median_seconds": round(float(median), 1) if median is not None else None,
        "last_succeeded_at": row.get("last_succeeded_at"),
        "last_finished_at": row.get("last_finished_at"),
    }
