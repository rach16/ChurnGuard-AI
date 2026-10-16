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

Each response's ``Sforce-Limit-Info`` is read and the last one kept on the run,
so the page can show how much of the org's daily allowance a sync costs. The
deferral below 20% and the back-off on ``REQUEST_LIMIT_EXCEEDED`` belong to the
schedule (1.11); a manual run today simply fails with that code named.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

import httpx

from connectors.salesforce import FIELDS, soql
from integrations import audit
from integrations.connections import ConnectionStore
from integrations.sync_store import SyncRun, SyncStore
from integrations.tokens import ConnectionGone, NeedsReauth, TokenError, TokenService

logger = logging.getLogger(__name__)

ACTOR = "operator"
OBJECTS: tuple[str, ...] = tuple(FIELDS)
SYNC_FIELDS = ("SystemModstamp", "IsDeleted")
OVERLAP = timedelta(minutes=5)
# A run still unfinished after this long belongs to a process that is gone.
ABANDONED_AFTER_SECONDS = 1800
LIMIT_INFO = re.compile(r"api-usage=(\d+)/(\d+)")
DATA_PREFIX = re.compile(r"^/services/data/v[\d.]+/")


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


def _soql_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _usage(r: httpx.Response) -> Optional[dict[str, int]]:
    m = LIMIT_INFO.search(r.headers.get("Sforce-Limit-Info", ""))
    return {"used": int(m.group(1)), "max": int(m.group(2))} if m else None


class SyncEngine:
    def __init__(self, tokens: TokenService, connections: ConnectionStore, runs: SyncStore,
                 *, overlap: timedelta = OVERLAP, objects: tuple[str, ...] = OBJECTS,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.tokens = tokens
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
        """At startup: runs the previous process left unfinished."""
        with self.connections.db.connection() as conn:
            return self._sweep(conn)

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
            audit.record(conn, "sync_finished", actor="system", tenant_id=run.tenant_id,
                         connection_id=run.connection_id, org_id=run.org_id,
                         detail={"run_id": run.id, "status": status, "error": error,
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
