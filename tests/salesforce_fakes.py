"""
Fakes shared by the connect and token tests: a Salesforce that answers the token,
identity, revoke, query and limits endpoints over an httpx mock transport, and an
in-memory store with the methods the flow and the token service call.

The store's connection context manager rolls back on an exception, like the
pool's does. A refused-state audit row was lost to exactly that once, so the fake
has to be this honest or the test cannot see the bug.
"""

from __future__ import annotations

import re
import secrets
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import parse_qs

import httpx

from integrations.connections import Connection
from integrations.salesforce_oauth import SalesforceOAuth
from integrations.sync_store import Serving, Snapshot, SyncRun, _shape_metrics

ORG = "00Dg700000J62qbEAB"
OTHER_ORG = "00D000000000001AAA"
USER = "005g700000AbCdEfGH"
HOST = "https://orgfarm-e065461ef9-dev-ed.develop.my.salesforce.com"
SITE = "https://staging.example.test"
REFRESH = "5Aep861" + "r" * 60
ACCESS = f"{ORG}!AQEAQ" + "a" * 50
CODE = "aPrxGt" + "c" * 40


class FakeSalesforce:
    """Records every exchange, refresh and revoke so a test can assert that a
    refused token was revoked, that the exchange carried the verifier and the
    secret, and how many refreshes a sequence of calls really made."""

    def __init__(self, org=ORG, instance=HOST, refresh=REFRESH, fail=None, *,
                 rotate=True, refresh_fail=None, api_401_once=False):
        self.org, self.instance, self.refresh, self.fail = org, instance, refresh, fail
        self.rotate, self.refresh_fail, self.api_401_once = rotate, refresh_fail, api_401_once
        self.exchanges: list[dict] = []
        self.refreshes: list[dict] = []
        self.revoked: list[str] = []
        self.revoke_hosts: list[str] = []
        self.api_calls: list[str] = []
        self.issued_access: list[str] = []
        self.refresh_instance = instance
        # The org's data, for the sync engine: sobject -> records, each with Id,
        # SystemModstamp (ISO, Z) and IsDeleted. query() hides deleted ones,
        # queryAll() returns them, and both filter on SystemModstamp when the
        # SOQL has a WHERE clause.
        self.records: dict[str, list[dict]] = {}
        self.page_size = 2000
        self.missing_fields: dict[str, set[str]] = {}       # sobject -> fields that 400
        self.fail_object: dict[str, tuple[int, str]] = {}   # sobject -> (status, errorCode)
        self.queries: list[tuple[str, str, str | None]] = []   # (endpoint, sobject, since)
        self.api_used = 0
        self.api_max = 15000
        self.api_remaining = 14990                        # what /limits reports
        self._cursors: dict[str, list[dict]] = {}

    def _token_body(self, access: str, refresh: str | None) -> dict:
        body = {"access_token": access, "instance_url": self.instance,
                "id": f"https://login.salesforce.com/id/{self.org}/{USER}",
                "scope": "api refresh_token", "issued_at": "1760616000000", "token_type": "Bearer"}
        if refresh:
            body["refresh_token"] = refresh
        return body

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/services/oauth2/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form.get("grant_type") == "refresh_token":
                self.refreshes.append(form)
                if self.refresh_fail:
                    return httpx.Response(400, json={"error": self.refresh_fail, "error_description": "nope"})
                access = f"{self.org}!NEW{len(self.refreshes)}" + "b" * 50
                self.issued_access.append(access)
                new_refresh = ("5AepROT" + secrets.token_hex(20)) if self.rotate else None
                body = self._token_body(access, new_refresh)
                body["instance_url"] = self.refresh_instance
                return httpx.Response(200, json=body)
            self.exchanges.append(form)
            if self.fail:
                return httpx.Response(400, json={"error": self.fail, "error_description": "nope"})
            return httpx.Response(200, json=self._token_body(ACCESS, self.refresh))
        if path == f"/id/{self.org}/{USER}":
            assert request.headers["authorization"] == f"Bearer {ACCESS}"
            return httpx.Response(200, json={"username": "r.banik16@example.com", "display_name": "R"})
        if path == "/services/oauth2/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            self.revoke_hosts.append(request.url.host)
            return httpx.Response(200)
        if path.startswith("/services/data/"):
            self.api_calls.append(path + ("?" + request.url.query.decode() if request.url.query else ""))
            bearer = request.headers.get("authorization", "")
            if self.api_401_once:
                self.api_401_once = False
                return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID"}])
            if not any(bearer == f"Bearer {t}" for t in self.issued_access):
                return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID"}])
            self.api_used += 1
            headers = {"Sforce-Limit-Info": f"api-usage={self.api_used}/{self.api_max}"}
            if path.endswith("/limits"):
                return httpx.Response(200, headers=headers,
                                      json={"DailyApiRequests": {"Max": self.api_max,
                                                                 "Remaining": self.api_remaining}})
            if path.endswith("/query") or path.endswith("/queryAll"):
                q = parse_qs(request.url.query.decode())["q"][0]
                if "FROM Organization" in q:
                    return httpx.Response(200, headers=headers, json={"records": [
                        {"Name": "ChurnGuard Dev", "OrganizationType": "Developer Edition"}]})
                return self._soql(path.rsplit("/", 1)[1], q, headers)
            if "/query/" in path:                          # a nextRecordsUrl
                key, offset = path.rsplit("/", 1)[1].rsplit("-", 1)
                return self._page(self._cursors[key], int(offset), headers)
            return httpx.Response(404)
        return httpx.Response(404)

    def _soql(self, endpoint: str, q: str, headers: dict) -> httpx.Response:
        m = re.fullmatch(r"SELECT (.+?) FROM (\w+)(?: WHERE SystemModstamp >= (\S+))?", q.strip())
        assert m, f"fake cannot parse SOQL: {q}"
        fields = [f.strip() for f in m.group(1).split(",")]
        sobject, since = m.group(2), m.group(3)
        self.queries.append((endpoint, sobject, since))
        if sobject in self.fail_object:
            status, code = self.fail_object[sobject]
            return httpx.Response(status, headers=headers,
                                  json=[{"errorCode": code, "message": f"fake refused {sobject}"}])
        missing = [f for f in fields if f in self.missing_fields.get(sobject, ())]
        if missing:
            return httpx.Response(400, headers=headers, json=[{
                "errorCode": "INVALID_FIELD",
                "message": f"No such column '{missing[0]}' on entity '{sobject}'"}])
        rows = self.records.get(sobject, [])
        if endpoint == "query":
            rows = [r for r in rows if not r.get("IsDeleted")]
        if since:
            floor = datetime.fromisoformat(since.replace("Z", "+00:00"))
            rows = [r for r in rows
                    if datetime.fromisoformat(r["SystemModstamp"].replace("Z", "+00:00")) >= floor]
        projected = [{"attributes": {"type": sobject}, **{f: r.get(f) for f in fields}} for r in rows]
        return self._page(projected, 0, headers)

    def _page(self, rows: list[dict], offset: int, headers: dict) -> httpx.Response:
        chunk = rows[offset:offset + self.page_size]
        done = offset + self.page_size >= len(rows)
        body: dict = {"totalSize": len(rows), "done": done, "records": chunk}
        if not done:
            key = secrets.token_hex(4)
            self._cursors[key] = rows
            body["nextRecordsUrl"] = f"/services/data/v59.0/query/{key}-{offset + self.page_size}"
        return httpx.Response(200, headers=headers, json=body)

    def oauth(self) -> SalesforceOAuth:
        return SalesforceOAuth("client-id", "client-secret-value",
                              http=httpx.Client(transport=httpx.MockTransport(self.handler)))


class FakeConn:
    """Only audit inserts reach execute(); everything else goes through the store.

    ``pending`` holds writes a store queued in this transaction; FakeDb applies
    them on a clean exit and drops them on an exception, like Postgres does.
    """

    def __init__(self, store):
        self.store = store
        self.committed = len(store.audit)
        self.pending: list[Callable[[], None]] = []

    def execute(self, sql, params=None):
        if sql.lstrip().lower().startswith("insert into audit_events"):
            self.store.audit.append({"event": params[0], "actor": params[1], "tenant_id": params[2],
                                     "connection_id": params[3], "org_id": params[4],
                                     "detail": params[5].obj})
            return self
        raise AssertionError(f"unexpected SQL in fake: {sql[:60]}")

    def fetchone(self):
        return {"id": len(self.store.audit)}

    def commit(self):
        self.committed = len(self.store.audit)
        for fn in self.pending:
            fn()
        self.pending.clear()


class FakeDb:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def connection(self):
        conn = FakeConn(self.store)
        try:
            yield conn
        except BaseException:
            del self.store.audit[conn.committed:]
            conn.pending.clear()
            raise
        conn.commit()


class FakeStore:
    """In memory, same method names as ConnectionStore."""

    def __init__(self):
        self.db = FakeDb(self)
        self.rows: dict[str, Connection] = {}
        self.states: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.locks: list[str] = []

    def get(self, tenant_id="default", provider="salesforce", conn=None):
        return next((r for r in self.rows.values() if r.tenant_id == tenant_id), None)

    def get_by_id(self, connection_id, conn=None):
        return self.rows.get(connection_id)

    def all(self, conn):
        return list(self.rows.values())

    def new_id(self, conn):
        return secrets.token_hex(8)

    def _replace(self, row: Connection, **changes) -> Connection:
        from dataclasses import replace
        updated = replace(row, updated_at=datetime.now(timezone.utc), **changes)
        self.rows[row.id] = updated
        return updated

    def upsert(self, conn, *, id, tenant_id, org_id, instance_url, login_host, user_id, username,
               scopes, sealed, token_issued_at, auth_method="web_server_pkce"):
        existing = next((r for r in self.rows.values() if r.org_id == org_id), None)
        row_id = existing.id if existing else id
        now = datetime.now(timezone.utc)
        self.rows[row_id] = Connection(
            id=row_id, tenant_id=tenant_id, provider="salesforce", auth_method=auth_method,
            org_id=org_id, instance_url=instance_url, login_host=login_host, user_id=user_id,
            username=username, scopes=scopes, status="active", status_reason=None, sealed=sealed,
            token_issued_at=token_issued_at, last_refreshed_at=None, last_used_at=None,
            created_at=existing.created_at if existing else now, updated_at=now)
        return self.rows[row_id]

    def update_token(self, conn, connection_id, sealed, refreshed_at):
        self._replace(self.rows[connection_id], sealed=sealed, last_refreshed_at=refreshed_at,
                      status="active", status_reason=None)

    def mark_refreshed(self, conn, connection_id, refreshed_at):
        self._replace(self.rows[connection_id], last_refreshed_at=refreshed_at,
                      last_used_at=refreshed_at, status="active", status_reason=None)

    def rewrap_token(self, conn, connection_id, sealed):
        self._replace(self.rows[connection_id], sealed=sealed)

    def set_status(self, conn, connection_id, status, reason=None):
        self._replace(self.rows[connection_id], status=status, status_reason=reason)

    def touch(self, conn, connection_id, used_at):
        if connection_id in self.rows:
            self._replace(self.rows[connection_id], last_used_at=used_at)

    def delete(self, conn, connection_id):
        self.rows.pop(connection_id, None)

    def lock(self, conn, connection_id):
        self.locks.append(connection_id)

    def try_lock(self, conn, connection_id):
        return True

    def create_state(self, conn, *, state, code_verifier, login_host, redirect_uri,
                     tenant_id="default", expected_org_id=None, ttl_seconds=600):
        self.states[state] = {"state": state, "code_verifier": code_verifier, "login_host": login_host,
                              "redirect_uri": redirect_uri, "tenant_id": tenant_id,
                              "expected_org_id": expected_org_id, "consumed": False,
                              "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)}

    def consume_state(self, conn, state):
        row = self.states.get(state)
        if not row or row["consumed"] or row["expires_at"] <= datetime.now(timezone.utc):
            return None
        row["consumed"] = True
        return dict(row)

    def purge_states(self, conn, older_than_seconds=3600):
        return 0


class FakeSyncStore:
    """In memory, same method names as SyncStore. Shares the connection store's
    FakeDb so audit rows and sync writes commit or roll back together, which is
    what lets a test see that a failed object left no half-landed page."""

    def __init__(self, connections: FakeStore):
        self.db = connections.db
        self.runs_by_id: dict[str, SyncRun] = {}
        self.raw: dict[tuple[str, str, str], dict] = {}        # (conn, sobject, id) -> row
        self.watermarks: dict[tuple[str, str], datetime] = {}
        self.snaps: dict[str, Snapshot] = {}
        self.serving: dict[str, Serving] = {}

    def _put(self, run: SyncRun) -> SyncRun:
        self.runs_by_id[run.id] = run
        return run

    def _replace(self, run_id: str, **changes) -> None:
        from dataclasses import replace
        self.runs_by_id[run_id] = replace(self.runs_by_id[run_id], **changes)

    # runs
    def create_run(self, conn, *, connection_id, tenant_id, org_id, trigger, as_of: date) -> SyncRun:
        run = SyncRun(id=secrets.token_hex(8), tenant_id=tenant_id, connection_id=connection_id,
                      org_id=org_id, trigger=trigger, status="pending", as_of=as_of, objects={},
                      api_requests=0, api_usage=None, error=None,
                      created_at=datetime.now(timezone.utc), started_at=None, finished_at=None)
        conn.pending.append(lambda: self._put(run))
        return run

    def get_run(self, run_id, conn=None) -> Optional[SyncRun]:
        return self.runs_by_id.get(run_id)

    def unfinished_run(self, conn, connection_id) -> Optional[SyncRun]:
        return next((r for r in self.runs(connection_id) if r.unfinished), None)

    def runs(self, connection_id, limit=10, conn=None) -> list[SyncRun]:
        rows = [r for r in self.runs_by_id.values() if r.connection_id == connection_id]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)[:limit]

    def last_run(self, connection_id, conn=None) -> Optional[SyncRun]:
        found = self.runs(connection_id, limit=1)
        return found[0] if found else None

    def start_run(self, conn, run_id, started_at) -> None:
        conn.pending.append(lambda: self._replace(run_id, status="running", started_at=started_at))

    def record_object(self, conn, run_id, sobject, summary, api_requests, api_usage) -> None:
        def apply():
            objects = {**self.runs_by_id[run_id].objects, sobject: summary}
            self._replace(run_id, objects=objects, api_requests=api_requests, api_usage=api_usage)
        conn.pending.append(apply)

    def finish_run(self, conn, run_id, status, finished_at, error=None, api_requests=None,
                   api_usage=None) -> None:
        def apply():
            run = self.runs_by_id[run_id]
            self._replace(run_id, status=status, finished_at=finished_at, error=error,
                          api_requests=run.api_requests if api_requests is None else api_requests,
                          api_usage=run.api_usage if api_usage is None else api_usage)
        conn.pending.append(apply)

    def sweep_abandoned(self, conn, older_than_seconds) -> list[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        swept = [r.id for r in self.runs_by_id.values()
                 if r.unfinished and (r.started_at or r.created_at) < cutoff]
        # Applied at once, not at commit: the engine reads unfinished_run() in the
        # same transaction and Postgres would show it the update.
        for run_id in swept:
            self._replace(run_id, status="failed", finished_at=datetime.now(timezone.utc),
                          error="abandoned: the process stopped before the run finished")
        return swept

    # watermarks
    def get_watermark(self, conn, connection_id, sobject) -> Optional[datetime]:
        return self.watermarks.get((connection_id, sobject))

    def set_watermark(self, conn, connection_id, sobject, watermark, run_id) -> None:
        conn.pending.append(lambda: self.watermarks.__setitem__((connection_id, sobject), watermark))

    # raw records
    def land(self, conn, connection_id, sobject, run_id, records) -> tuple[int, int]:
        landed = tombstones = 0
        for rec in records:
            body = {k: v for k, v in rec.items() if k != "attributes"}
            deleted = bool(body.get("IsDeleted"))
            tombstones += deleted
            landed += 1
            key = (connection_id, sobject, body["Id"])
            conn.pending.append(lambda key=key, body=body, deleted=deleted: self.raw.__setitem__(
                key, {"record": body, "deleted": deleted, "last_run_id": run_id,
                      "last_seen_at": datetime.now(timezone.utc)}))
        return landed, tombstones

    def live_records(self, conn, connection_id, sobject) -> list[dict]:
        return [row["record"] for (c, s, _), row in sorted(self.raw.items())
                if c == connection_id and s == sobject and not row["deleted"]]

    def counts(self, conn, connection_id) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for (c, s, _), row in self.raw.items():
            if c != connection_id:
                continue
            bucket = out.setdefault(s, {"live": 0, "tombstones": 0})
            bucket["tombstones" if row["deleted"] else "live"] += 1
        return out

    # snapshots
    def create_snapshot(self, conn, *, run_id, connection_id, tenant_id, as_of, tables, report,
                        contracts) -> Snapshot:
        snap = Snapshot(id=secrets.token_hex(8), tenant_id=tenant_id, connection_id=connection_id,
                        run_id=run_id, as_of=as_of, tables=tables, report=report,
                        contracts=contracts, contracts_passed=not contracts.get("failures"),
                        row_counts={n: len(r) for n, r in tables.items()},
                        created_at=datetime.now(timezone.utc))
        conn.pending.append(lambda: self.snaps.__setitem__(snap.id, snap))
        # The real store returns the head only (no tables). The fake once
        # returned everything, which hid a bug that served empty files.
        from dataclasses import replace as _replace
        return _replace(snap, tables={})

    def attach_snapshot(self, conn, run_id, snapshot_id, contracts) -> None:
        conn.pending.append(lambda: self._replace(run_id, snapshot_id=snapshot_id, contracts=contracts))

    def get_snapshot(self, snapshot_id, conn=None, *, with_tables=True) -> Optional[Snapshot]:
        snap = self.snaps.get(snapshot_id)
        if snap is not None and not with_tables:
            from dataclasses import replace as _replace
            return _replace(snap, tables={})
        return snap

    def latest_good_snapshot(self, tenant_id="default", conn=None, *, with_tables=False) -> Optional[Snapshot]:
        good = [s for s in self.snaps.values() if s.contracts_passed and s.tenant_id == tenant_id]
        return max(good, key=lambda s: s.created_at, default=None)

    def snapshots(self, connection_id, limit=10, conn=None) -> list[Snapshot]:
        rows = [s for s in self.snaps.values() if s.connection_id == connection_id]
        return sorted(rows, key=lambda s: s.created_at, reverse=True)[:limit]

    # serving
    def get_serving(self, tenant_id="default", conn=None) -> Optional[Serving]:
        return self.serving.get(tenant_id)

    def set_serving(self, conn, *, tenant_id, snapshot_id, run_id, actor) -> Optional[str]:
        before = self.serving.get(tenant_id)
        previous = before.snapshot_id if before else None
        row = Serving(tenant_id=tenant_id, snapshot_id=snapshot_id, run_id=run_id,
                      previous_snapshot_id=previous, promoted_at=datetime.now(timezone.utc),
                      promoted_by=actor)
        conn.pending.append(lambda: self.serving.__setitem__(tenant_id, row))
        return previous

    # retention
    def prune_snapshots(self, conn, tenant_id, keep, protect) -> list[str]:
        rows = sorted((s for s in self.snaps.values() if s.tenant_id == tenant_id),
                      key=lambda s: s.created_at, reverse=True)
        doomed = [s.id for s in rows[keep:] if s.id not in protect]
        for sid in doomed:
            conn.pending.append(lambda sid=sid: self.snaps.pop(sid, None))
        return doomed

    def prune_tombstones(self, conn, older_than_days) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        doomed = [k for k, r in self.raw.items() if r["deleted"] and r["last_seen_at"] < cutoff]
        for k in doomed:
            conn.pending.append(lambda k=k: self.raw.pop(k, None))
        return len(doomed)

    def prune_orphans(self, conn, older_than_days) -> dict[str, int]:
        return {"connections": 0, "raw_records": 0, "watermarks": 0, "runs": 0}

    # metrics
    def metrics(self, connection_id, days=30, conn=None) -> dict:
        import statistics
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = [r for r in self.runs_by_id.values() if r.connection_id == connection_id and r.created_at >= since]
        durations = [(r.finished_at - r.started_at).total_seconds() for r in rows if r.finished_at and r.started_at]
        ok = [r.finished_at for r in rows if r.status == "succeeded" and r.finished_at]
        done = [r.finished_at for r in rows if r.finished_at]
        return _shape_metrics({
            "runs": len(rows),
            "succeeded": sum(r.status == "succeeded" for r in rows),
            "failed": sum(r.status == "failed" for r in rows),
            "degraded": sum(r.status == "degraded" for r in rows),
            "manual": sum(r.trigger == "manual" for r in rows),
            "records_fetched": sum(o.get("fetched", 0) for r in rows for o in r.objects.values()),
            "tombstones": sum(o.get("tombstones", 0) for r in rows for o in r.objects.values()),
            "api_requests": sum(r.api_requests for r in rows),
            "median_seconds": statistics.median(durations) if durations else None,
            "last_succeeded_at": max(ok) if ok else None,
            "last_finished_at": max(done) if done else None,
        }, days)
