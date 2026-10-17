"""
The sync run (ADR-0012, section 5): read what changed in the org through the
stored connection and land it, one object at a time, behind a watermark.

What a run does, per object (Account, Opportunity, OpportunityCompetitor, Case,
Task), in one database transaction each:

1. Read the object's watermark. None means this object has never been read: the
   run is **full** for it (``query``, which returns only live records). Otherwise
   it is **incremental**: ``queryAll`` for everything with ``SystemModstamp`` at
   or after the watermark less an overlap window, which also returns records in
   the recycle bin with ``IsDeleted`` true.
2. Page through the result and upsert every record by Salesforce Id; a deleted
   one becomes a tombstone. The overlap re-reads a few minutes of records every
   run, and the upsert makes that harmless.
3. Write the new watermark, the moment this object's read began, in the same
   transaction. A crash before commit loses the records *and* the watermark, so
   the next run re-reads from the old one: resumable per object, never a gap.

The run row is the lock. The partial unique index in migration 0002 allows one
unfinished run per connection; a second trigger finds that run and joins it. A
run whose process died is swept to ``failed`` after half an hour.

After the last object, the run builds its **candidate** (1.10.2): every live
record goes through the connector's ``transform`` into the canonical tables and
the ingest report, the data contracts run on the result, and the whole thing is
stored as a snapshot. Contracts pass: the run ``succeeded`` and the snapshot is
**promoted** (1.10.3): the tenant's serving pointer flips to it in the same
transaction, and ``on_promote`` puts it on disk and reloads the loaders. Any
fail: the run is ``degraded``, the snapshot is kept for diagnosis with the
failing contracts named, and the pointer stays where it was. A transform that
raises is a ``failed`` run like any other error.

Each response's ``Sforce-Limit-Info`` is read and the last one kept on the run,
so the page can show how much of the org's daily allowance a sync costs. The
deferral below 20% and the back-off on ``REQUEST_LIMIT_EXCEEDED`` belong to the
schedule (1.11); a manual run today simply fails with that code named.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

import httpx

from connectors.salesforce import FIELDS, RAW_KEYS, as_csv_rows, soql, transform
from core.contracts import run_contracts
from integrations import audit
from integrations.connections import ConnectionStore
from integrations.sync_store import Serving, Snapshot, SyncRun, SyncStore
from integrations.tokens import ConnectionGone, NeedsReauth, RefreshFailed, TokenError, TokenService

logger = logging.getLogger(__name__)

ACTOR = "operator"
OBJECTS: tuple[str, ...] = tuple(FIELDS)
SYNC_FIELDS = ("SystemModstamp", "IsDeleted")
OVERLAP = timedelta(minutes=5)
# A run still unfinished after this long belongs to a process that is gone.
ABANDONED_AFTER_SECONDS = 1800
# ADR-0012 section 7: snapshots, the last 10; raw landings, 30 days.
SNAPSHOTS_KEPT = 10
RAW_RETENTION_DAYS = 30
# The schedule (ADR-0012 section 8). A connection is due when its last
# successful run is older than this; a scheduled run is deferred while the
# org has less than the reserve of its daily API allowance left, and backed
# off after REQUEST_LIMIT_EXCEEDED: an hour, doubling, capped, with jitter.
SYNC_INTERVAL = timedelta(hours=float(os.getenv("SYNC_INTERVAL_HOURS", "24")))
API_RESERVE = float(os.getenv("SYNC_API_RESERVE", "0.20"))
BACKOFF_BASE = timedelta(hours=1)
BACKOFF_CAP = timedelta(hours=12)
BACKOFF_JITTER = timedelta(minutes=10)
LIMIT_INFO = re.compile(r"api-usage=(\d+)/(\d+)")
DATA_PREFIX = re.compile(r"^/services/data/v[\d.]+/")


class RollbackRefused(Exception):
    """The pointer cannot move there; the message says why and is safe to show."""


class SalesforceError(Exception):
    """A non-200 from the org, with Salesforce's own code and message."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}"[:300])
        self.status, self.code, self.message = status, code, message

    @classmethod
    def from_response(cls, r: httpx.Response) -> "SalesforceError":
        code, message = f"HTTP_{r.status_code}", (r.text or "")[:200]
        try:
            body = r.json()
            if isinstance(body, list) and body and isinstance(body[0], dict):
                code = body[0].get("errorCode", code)
                message = body[0].get("message", message)
        except ValueError:
            pass
        return cls(r.status_code, code, message)


@dataclass(frozen=True)
class Triggered:
    run: SyncRun
    joined: bool       # True when an unfinished run already existed


@dataclass
class _Progress:
    """Counters that must survive an object's transaction rolling back."""
    api_requests: int = 0
    api_usage: Optional[dict[str, int]] = None
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    snapshot_id: Optional[str] = None
    contracts: Optional[dict[str, Any]] = None
    promoted: bool = False
    served: Optional[bool] = None


def _soql_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _usage(r: httpx.Response) -> Optional[dict[str, int]]:
    m = LIMIT_INFO.search(r.headers.get("Sforce-Limit-Info", ""))
    return {"used": int(m.group(1)), "max": int(m.group(2))} if m else None


class SyncEngine:
    def __init__(self, tokens: TokenService, connections: ConnectionStore, runs: SyncStore,
                 *, overlap: timedelta = OVERLAP, objects: tuple[str, ...] = OBJECTS,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 on_promote: Optional[Callable[[Snapshot], None]] = None):
        self.tokens = tokens
        # Called after the serving pointer has flipped to a snapshot, with it:
        # the API restores the snapshot to disk and reloads its scorer from it.
        self.on_promote = on_promote
        self.connections = connections
        self.runs = runs
        self.overlap = overlap
        self.objects = objects
        self.now = clock
        # How a triggered run is executed. A thread here; tests call run() inline.
        self.spawn: Callable[[str], None] = self._spawn_thread

    # ---------------------------------------------------------------- trigger

    def start(self, connection_id: str, *, actor: str = ACTOR, trigger: str = "manual") -> Triggered:
        """Create the run and set it going, or join the one already going."""
        triggered = self.trigger(connection_id, actor=actor, trigger=trigger)
        if not triggered.joined:
            self.spawn(triggered.run.id)
        return triggered

    def trigger(self, connection_id: str, *, actor: str = ACTOR, trigger: str = "manual") -> Triggered:
        """The run row, created or found. Under the connection's lock so two
        triggers cannot both create; the unique index would refuse the second
        anyway, but a join is the right answer, not an error."""
        with self.connections.db.connection() as conn:
            self.connections.lock(conn, connection_id)
            c = self.connections.get_by_id(connection_id, conn)
            if c is None:
                raise ConnectionGone("This connection no longer exists.")
            if c.status != "active":
                raise NeedsReauth(c.status_reason or "The connection needs a fresh login.")
            self._sweep(conn)
            existing = self.runs.unfinished_run(conn, connection_id)
            if existing is not None:
                return Triggered(existing, True)
            run = self.runs.create_run(conn, connection_id=c.id, tenant_id=c.tenant_id,
                                       org_id=c.org_id, trigger=trigger, as_of=self.now().date())
            audit.record(conn, "sync_started", actor=actor, tenant_id=c.tenant_id,
                         connection_id=c.id, org_id=c.org_id,
                         detail={"run_id": run.id, "trigger": trigger})
        return Triggered(run, False)

    def sweep(self) -> list[str]:
        """At startup: runs the previous process left unfinished, and retention."""
        with self.connections.db.connection() as conn:
            swept = self._sweep(conn)
            self._retain(conn, "default")
        return swept

    # --------------------------------------------------------------- schedule

    def sync_due(self, *, actor: str = "system", trigger: str = "schedule",
                 wait: bool = False, interval: timedelta = SYNC_INTERVAL) -> list[dict[str, Any]]:
        """Start a run for every active connection whose last success is older
        than ``interval``. One line per connection saying what happened:
        ``started`` (or ``joined``), ``not_due``, ``deferred`` (API allowance or
        back-off, with when it will be tried), or ``skipped`` (needs a login).
        With ``wait`` the runs execute inline and their outcome is in the line,
        which is what a scheduler that wants to fail loudly needs."""
        now = self.now()
        with self.connections.db.connection() as conn:
            connections = self.connections.all(conn)
        out: list[dict[str, Any]] = []
        for c in connections:
            line: dict[str, Any] = {"connection_id": c.id, "org_id": c.org_id, "trigger": trigger}
            if c.status != "active":
                out.append({**line, "action": "skipped", "reason": c.status_reason or c.status})
                continue
            recent = self.runs.runs(c.id, limit=10)
            last_ok = next((r for r in recent if r.status == "succeeded"), None)
            if last_ok and last_ok.finished_at and now - last_ok.finished_at < interval:
                out.append({**line, "action": "not_due", "last_succeeded_at": last_ok.finished_at,
                            "due_at": last_ok.finished_at + interval})
                continue
            retry_at = self._backoff_until(recent)
            if retry_at and now < retry_at:
                out.append({**line, "action": "deferred", "reason": "backing off after REQUEST_LIMIT_EXCEEDED",
                            "retry_at": retry_at})
                continue
            reserve = self._allowance(c.id)
            if reserve is not None and reserve["fraction_remaining"] < API_RESERVE:
                self._audit_deferred(c, {"reason": "api_allowance_low", **reserve})
                out.append({**line, "action": "deferred", "reason": "org API allowance low", **reserve})
                continue
            try:
                triggered = self.trigger(c.id, actor=actor, trigger=trigger)
            except NeedsReauth as e:
                out.append({**line, "action": "skipped", "reason": str(e)})
                continue
            if triggered.joined:
                out.append({**line, "action": "joined", "run": triggered.run.public()})
                continue
            if wait:
                run = self.run(triggered.run.id)
            else:
                self.spawn(triggered.run.id)
                run = triggered.run
            out.append({**line, "action": "started", "run": run.public()})
        return out

    def _allowance(self, connection_id: str) -> Optional[dict[str, Any]]:
        """The org's daily API allowance now, one call. None when it cannot be
        read; the run itself will find out."""
        try:
            limits = self.tokens.get(connection_id, "limits")
        except (TokenError, RefreshFailed) as e:
            logger.warning(f"allowance check for {connection_id[:8]} failed: {type(e).__name__}: {e}")
            return None
        daily = limits.get("DailyApiRequests") or {}
        maximum, remaining = daily.get("Max"), daily.get("Remaining")
        if not maximum:
            return None
        return {"api_max": maximum, "api_remaining": remaining,
                "fraction_remaining": round(remaining / maximum, 4)}

    def _backoff_until(self, recent: list[SyncRun]) -> Optional[datetime]:
        """After REQUEST_LIMIT_EXCEEDED: one hour, doubling per consecutive
        such failure, capped, plus a jitter derived from the run id so the
        decision is the same whoever asks."""
        streak = 0
        for r in recent:
            if r.status == "failed" and r.error and "REQUEST_LIMIT_EXCEEDED" in r.error:
                streak += 1
            else:
                break
        if not streak or not recent[0].finished_at:
            return None
        wait = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (streak - 1)))
        jitter = BACKOFF_JITTER * (int(recent[0].id.replace("-", "")[:8], 16) % 1000 / 1000)
        return recent[0].finished_at + wait + jitter

    def _audit_deferred(self, c, detail: dict[str, Any]) -> None:
        with self.connections.db.connection() as conn:
            audit.record(conn, "sync_deferred", actor="system", tenant_id=c.tenant_id,
                         connection_id=c.id, org_id=c.org_id, detail=detail)

    # --------------------------------------------------------------- rollback

    def rollback(self, *, actor: str = ACTOR, to_snapshot_id: Optional[str] = None,
                 tenant_id: str = "default") -> tuple[Serving, Optional[bool]]:
        """Move the serving pointer back: to ``to_snapshot_id``, or to the
        snapshot served before this one. Only a snapshot that passed its
        contracts can be served. Audited as ``rollback``; then the snapshot is
        restored and the loaders reloaded like a promotion. Returns the new
        pointer and whether serving it succeeded (None without ``on_promote``)."""
        with self.runs.db.connection() as conn:
            current = self.runs.get_serving(tenant_id, conn)
            if current is None:
                raise RollbackRefused("Nothing is being served yet; there is nothing to roll back from.")
            target_id = to_snapshot_id or current.previous_snapshot_id
            if not target_id:
                raise RollbackRefused("No earlier snapshot is recorded to roll back to.")
            if target_id == current.snapshot_id:
                raise RollbackRefused("That snapshot is already the one being served.")
            target = self.runs.get_snapshot(target_id, conn)
            if target is None:
                raise RollbackRefused("That snapshot no longer exists; retention keeps the last "
                                      f"{SNAPSHOTS_KEPT}.")
            if not target.contracts_passed:
                raise RollbackRefused("That snapshot failed its data contracts and cannot be served.")
            previous = self.runs.set_serving(conn, tenant_id=tenant_id, snapshot_id=target.id,
                                             run_id=target.run_id, actor=actor)
            audit.record(conn, "rollback", actor=actor, tenant_id=tenant_id,
                         connection_id=target.connection_id,
                         detail={"from_snapshot_id": previous, "to_snapshot_id": target.id,
                                 "to_run_id": target.run_id, "as_of": target.as_of.isoformat(),
                                 "row_counts": target.row_counts})
        logger.warning(f"rollback by {actor}: serving {target.id[:8]} (run {target.run_id[:8]}), "
                       f"was {(previous or '')[:8]}")
        served: Optional[bool] = None
        if self.on_promote is not None:
            try:
                self.on_promote(target)
                served = True
            except Exception as e:  # noqa: BLE001
                served = False
                logger.error(f"rollback to {target.id[:8]}: pointer moved but serving it failed: "
                             f"{type(e).__name__}: {e}", exc_info=True)
        return self.runs.get_serving(tenant_id), served

    # -------------------------------------------------------------- retention

    def _retain(self, conn, tenant_id: str) -> dict[str, Any]:
        """ADR-0012 section 7. Never the serving snapshot or its predecessor."""
        pointer = self.runs.get_serving(tenant_id, conn)
        protect = [i for i in ((pointer.snapshot_id, pointer.previous_snapshot_id) if pointer else ()) if i]
        snapshots = self.runs.prune_snapshots(conn, tenant_id, SNAPSHOTS_KEPT, protect)
        tombstones = self.runs.prune_tombstones(conn, RAW_RETENTION_DAYS)
        orphans = self.runs.prune_orphans(conn, RAW_RETENTION_DAYS)
        summary = {"snapshots_deleted": len(snapshots), "tombstones_deleted": tombstones,
                   "orphaned_connections_purged": orphans["connections"],
                   "orphaned_rows_deleted": orphans["raw_records"] + orphans["watermarks"] + orphans["runs"]}
        if any(summary.values()):
            audit.record(conn, "retention", actor="system", tenant_id=tenant_id,
                         detail={**summary, "snapshot_ids": snapshots})
            logger.info(f"retention: {summary}")
        return summary

    def _sweep(self, conn) -> list[str]:
        swept = self.runs.sweep_abandoned(conn, ABANDONED_AFTER_SECONDS)
        for run_id in swept:
            logger.warning(f"sync run {run_id} abandoned by a previous process; marked failed")
            audit.record(conn, "sync_finished", actor="system",
                         detail={"run_id": run_id, "status": "failed", "error": "abandoned"})
        return swept

    def _spawn_thread(self, run_id: str) -> None:
        threading.Thread(target=self.run, args=(run_id,), daemon=True,
                         name=f"sync-{run_id[:8]}").start()

    # -------------------------------------------------------------------- run

    def run(self, run_id: str) -> SyncRun:
        run = self.runs.get_run(run_id)
        if run is None:
            raise ValueError(f"no sync run {run_id}")
        started = self.now()
        with self.runs.db.connection() as conn:
            self.runs.start_run(conn, run.id, started)
        logger.info(f"sync {run.id[:8]} started for org {run.org_id} ({run.trigger})")

        progress = _Progress()
        status, error = "succeeded", None
        try:
            for sobject in self.objects:
                self._sync_object(run, sobject, progress)
            status, error = self._build_candidate(run, progress)
        except SalesforceError as e:
            status, error = "failed", f"{sobject}: {e}"
        except TokenError as e:
            status, error = "failed", str(e)
        except Exception as e:  # noqa: BLE001 - the run row must record it
            status, error = "failed", f"{type(e).__name__}: {e}"[:500]
            logger.error(f"sync {run.id[:8]} crashed: {error}", exc_info=True)

        finished = self.now()
        with self.runs.db.connection() as conn:
            self.runs.finish_run(conn, run.id, status, finished, error,
                                 api_requests=progress.api_requests, api_usage=progress.api_usage)
            if progress.api_requests:
                self.connections.touch(conn, run.connection_id, finished)
            if status == "succeeded":
                self._retain(conn, run.tenant_id)
            audit.record(conn, "sync_finished", actor="system", tenant_id=run.tenant_id,
                         connection_id=run.connection_id, org_id=run.org_id,
                         detail={"run_id": run.id, "status": status, "error": error,
                                 "snapshot_id": progress.snapshot_id,
                                 "contracts": progress.contracts,
                                 "promoted": progress.promoted, "served": progress.served,
                                 "objects": {k: {"mode": v["mode"], "fetched": v["fetched"],
                                                 "tombstones": v["tombstones"]}
                                             for k, v in progress.objects.items()},
                                 "api_requests": progress.api_requests,
                                 "api_usage": progress.api_usage,
                                 "duration_seconds": round((finished - started).total_seconds(), 1)})
        level = logging.INFO if status == "succeeded" else logging.WARNING
        logger.log(level, f"sync {run.id[:8]} {status}: "
                          f"{sum(o['fetched'] for o in progress.objects.values())} records, "
                          f"{progress.api_requests} API calls"
                          + (f", {error}" if error else ""))
        return self.runs.get_run(run.id)

    def _sync_object(self, run: SyncRun, sobject: str, progress: _Progress) -> None:
        """One object, one transaction: land every page, then the watermark."""
        with self.runs.db.connection() as conn:
            since = self.runs.get_watermark(conn, run.connection_id, sobject)
            began = self.now()
            query = soql(sobject, SYNC_FIELDS)
            endpoint, mode = "query", "full"
            if since is not None:
                endpoint, mode = "queryAll", "incremental"
                query += f" WHERE SystemModstamp >= {_soql_datetime(since - self.overlap)}"
            path: Optional[str] = f"{endpoint}?q={quote(query)}"

            fetched = tombstones = requests = 0
            while path:
                r = self.tokens.request(run.connection_id, path)
                requests += 1
                progress.api_requests += 1
                progress.api_usage = _usage(r) or progress.api_usage
                if r.status_code != 200:
                    raise SalesforceError.from_response(r)
                page = r.json()
                n, t = self.runs.land(conn, run.connection_id, sobject, run.id, page.get("records", []))
                fetched += n
                tombstones += t
                next_url = page.get("nextRecordsUrl") if not page.get("done", True) else None
                path = DATA_PREFIX.sub("", next_url) if next_url else None

            self.runs.set_watermark(conn, run.connection_id, sobject, began, run.id)
            summary = {"mode": mode, "fetched": fetched, "tombstones": tombstones,
                       "requests": requests, "since": since.isoformat() if since else None,
                       "watermark": began.isoformat()}
            progress.objects[sobject] = summary
            self.runs.record_object(conn, run.id, sobject, summary,
                                    progress.api_requests, progress.api_usage)
        logger.info(f"sync {run.id[:8]} {sobject}: {mode}, {fetched} records, "
                    f"{tombstones} deleted, {requests} request(s)")

    def _build_candidate(self, run: SyncRun, progress: _Progress) -> tuple[str, Optional[str]]:
        """Transform the landed records, run the contracts, store the snapshot.
        Returns the run's status and error."""
        with self.runs.db.connection() as conn:
            raw = {key: self.runs.live_records(conn, run.connection_id, sobject)
                   for sobject, key in RAW_KEYS.items()}
        tables, report = transform(raw, run.as_of)
        csv_tables = as_csv_rows(tables)
        checker = run_contracts(csv_tables, log=lambda line: None)
        contracts = checker.summary()
        with self.runs.db.connection() as conn:
            snapshot = self.runs.create_snapshot(
                conn, run_id=run.id, connection_id=run.connection_id, tenant_id=run.tenant_id,
                as_of=run.as_of, tables=csv_tables, report=report.to_dict(), contracts=contracts)
            self.runs.attach_snapshot(conn, run.id, snapshot.id, contracts)
            if not checker.failures:
                # Promotion, in the same transaction as the result it records
                # (section 6): the pointer and the snapshot land together or not at all.
                previous = self.runs.set_serving(conn, tenant_id=run.tenant_id,
                                                 snapshot_id=snapshot.id, run_id=run.id, actor="system")
                audit.record(conn, "promotion", actor="system", tenant_id=run.tenant_id,
                             connection_id=run.connection_id, org_id=run.org_id,
                             detail={"run_id": run.id, "snapshot_id": snapshot.id,
                                     "previous_snapshot_id": previous,
                                     "row_counts": snapshot.row_counts})
        progress.snapshot_id, progress.contracts = snapshot.id, contracts
        if checker.failures:
            names = "; ".join(f.split(":")[0] for f in checker.failures)
            logger.warning(f"sync {run.id[:8]} candidate failed {len(checker.failures)} contract(s): {names}")
            return "degraded", f"contracts failed: {names}"[:500]

        progress.promoted = True
        logger.info(f"sync {run.id[:8]} promoted snapshot {snapshot.id[:8]}: "
                    f"{report.customers_written} customers, {checker.passed} contracts passed, "
                    f"{len(checker.skipped)} skipped"
                    + (f", replacing {previous[:8]}" if previous else ""))
        # Serving is a consequence of the pointer, not a condition of it: if the
        # restore fails here the pointer still names the right snapshot and the
        # next start restores it. The run says so rather than failing.
        if self.on_promote is not None:
            try:
                self.on_promote(snapshot)
                progress.served = True
            except Exception as e:  # noqa: BLE001
                progress.served = False
                logger.error(f"sync {run.id[:8]}: promoted but not served: {type(e).__name__}: {e}",
                             exc_info=True)
                return "succeeded", f"promoted, but serving it failed: {type(e).__name__}: {e}"[:500]
        return "succeeded", None
