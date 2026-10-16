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
