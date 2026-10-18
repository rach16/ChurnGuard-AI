"""
Write-back: the site's risk reading on the Salesforce Account (ADR-0014, 9.2).

After every promotion and every rollback, the readings the site is now showing
go onto three custom fields of each active account's Account record:

    ChurnGuard_Risk_Score__c   Number(3,1)   the risk score, 0.0–100.0
    ChurnGuard_Risk_Band__c    Picklist      Critical / High / Medium / Low
    ChurnGuard_Top_Reason__c   Text(80)      the one reason the site names

Nothing is written until an admin **enables** it for the connection. Enabling
creates whichever of the three fields the org lacks through the Tooling API
(the owner's choice, ADR-0014 section 6: nothing for a customer's admin to
install), grants the connected user read and edit on them, confirms all three
are present and updateable, and only then flips ``connections.writeback_enabled``.

A write resolves each customer's Account Id from the live landings (section 2),
reads the three fields as the org holds them now, and writes **only the records
whose values differ** (section 3), in batches of 200 through the Composite
sObject Collections API with ``allOrNone`` off (section 4), under the same API
reserve as the sync. Every attempt is one ``writebacks`` row and one audit event
with counts, never values (section 7). A failed write never touches what the
site serves: the site was updated first and is right; the org is one sync
behind until the next promotion or a *Write back now*.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

from integrations import audit
from integrations.connections import ConnectionStore
from integrations.sync import API_RESERVE, SalesforceError, _usage
from integrations.sync_store import SyncStore, WriteBack as WriteBackRow
from integrations.tokens import TokenError, TokenService

logger = logging.getLogger("churnguard.writeback")

SOBJECT = "Account"
BATCH = 200                    # Composite sObject Collections' cap per request
KEEP = 10                      # attempts kept per connection, like snapshots
BANDS = ("Critical", "High", "Medium", "Low")
SCORE, BAND, REASON = "ChurnGuard_Risk_Score__c", "ChurnGuard_Risk_Band__c", "ChurnGuard_Top_Reason__c"
FIELD_NAMES = (SCORE, BAND, REASON)

# What Enable creates, as the Tooling API's CustomField.Metadata, and what the
# describe must show afterwards. Changing a definition here changes nothing in
# an org that already has the field: existing fields are never altered.
_DESCRIPTION = "Written by ChurnGuard after each sync (ADR-0014). Equals what the ChurnGuard site shows."
FIELDS: dict[str, dict[str, Any]] = {
    SCORE: {"label": "ChurnGuard risk score", "type": "Number", "precision": 4, "scale": 1,
            "description": _DESCRIPTION, "inlineHelpText": "0 to 100; higher is more likely to churn."},
    BAND: {"label": "ChurnGuard risk band", "type": "Picklist", "description": _DESCRIPTION,
           "valueSet": {"restricted": True, "valueSetDefinition": {
               "sorted": False,
               "value": [{"fullName": b, "label": b, "default": False} for b in BANDS]}}},
    REASON: {"label": "ChurnGuard top reason", "type": "Text", "length": 80,
             "description": _DESCRIPTION},
}
# The describe's name for each type, to confirm an existing field is usable.
_DESCRIBE_TYPES = {SCORE: ("double", "currency", "percent", "int"), BAND: ("picklist", "string"),
                   REASON: ("string", "textarea", "picklist")}


class WriteBackRefused(Exception):
    """Cannot proceed; the message names the step and the reason, and is safe to show."""


def reading(c: dict[str, Any]) -> dict[str, Any]:
    """The three values for one scored customer, exactly as the site shows them."""
    return {SCORE: round(float(c["risk_score"]), 1), BAND: c["risk_level"], REASON: c["risk_reason"]}


def _same(current: dict[str, Any], wanted: dict[str, Any]) -> bool:
    score = current.get(SCORE)
    return (score is not None and abs(float(score) - wanted[SCORE]) < 0.05
            and current.get(BAND) == wanted[BAND] and current.get(REASON) == wanted[REASON])


class WriteBackEngine:
    def __init__(self, tokens: TokenService, connections: ConnectionStore, runs: SyncStore, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.tokens = tokens
        self.connections = connections
        self.runs = runs
        self.now = clock

    # ----------------------------------------------------------------- status

    def status(self, connection_id: str) -> dict[str, Any]:
        c = self.connections.get_by_id(connection_id)
        last = self.runs.last_writeback(connection_id)
        return {"enabled": bool(c and c.writeback_enabled), "fields": list(FIELD_NAMES),
                "last": last.public() if last else None}

    # ----------------------------------------------------------------- enable

    def enable(self, connection_id: str, actor: str) -> dict[str, Any]:
        """Create the missing fields, grant the connected user edit on them,
        confirm, flip the flag. Stops at the first failure with the reason."""
        c = self._connection(connection_id)
        present = self._describe(connection_id)
        created: list[str] = []
        for name, meta in FIELDS.items():
            if name in present:
                continue
            r = self._call(connection_id, "POST", "tooling/sobjects/CustomField",
                           {"FullName": f"{SOBJECT}.{name}", "Metadata": meta})
            if r.status_code not in (200, 201):
                err = SalesforceError.from_response(r)
                hint = (" The connected user cannot create fields; ask a Salesforce admin to connect,"
                        " or to create the three ChurnGuard fields on Account."
                        if r.status_code == 403 or "INSUFFICIENT" in str(err) else "")
                raise WriteBackRefused(f"Creating {name} failed: {err}.{hint}")
            created.append(name)
        granted = self._grant(connection_id, c.user_id, [n for n in FIELD_NAMES if n not in present
                                                        or not present[n]["updateable"]])
        present = self._describe(connection_id)
        missing = [n for n in FIELD_NAMES if n not in present]
        if missing:
            raise WriteBackRefused(f"After creating them, the org does not show {', '.join(missing)} on Account.")
        wrong = [f"{n} is {present[n]['type']}" for n in FIELD_NAMES
                 if present[n]["type"] not in _DESCRIBE_TYPES[n]]
        if wrong:
            raise WriteBackRefused("A field exists with another type: " + "; ".join(wrong)
                                   + ". Rename or remove it in Setup, then enable again.")
        locked = [n for n in FIELD_NAMES if not present[n]["updateable"]]
        if locked:
            raise WriteBackRefused(f"The connected user cannot edit {', '.join(locked)}."
                                   " Grant edit access on these fields to that user's profile in Setup.")
        with self.connections.db.connection() as conn:
            self.connections.set_writeback(conn, c.id, True)
            audit.record(conn, "writeback_enabled", actor=actor, tenant_id=c.tenant_id,
                         connection_id=c.id, org_id=c.org_id,
                         detail={"fields_created": created, "fields_existing": [n for n in FIELD_NAMES if n not in created],
                                 "permissions_granted": granted})
        self.tokens.touch(c.id)
        logger.info(f"write-back enabled on {c.org_id} by {actor}: created {created or 'nothing'}")
        return {"enabled": True, "fields_created": created,
                "fields_existing": [n for n in FIELD_NAMES if n not in created],
                "permissions_granted": granted}

    def disable(self, connection_id: str, actor: str) -> dict[str, Any]:
        c = self._connection(connection_id)
        with self.connections.db.connection() as conn:
            self.connections.set_writeback(conn, c.id, False)
            audit.record(conn, "writeback_disabled", actor=actor, tenant_id=c.tenant_id,
                         connection_id=c.id, org_id=c.org_id)
        logger.info(f"write-back disabled on {c.org_id} by {actor}")
        return {"enabled": False}

    # -------------------------------------------------------------------- run

    def run(self, connection_id: str, *, scored: list[dict[str, Any]], snapshot_id: Optional[str],
            trigger: str, actor: str = "system", force: bool = False) -> Optional[WriteBackRow]:
        """Write the readings in ``scored`` (the site's own list) to the org.
        Returns the attempt's row, or None when write-back is off for the
        connection and ``force`` is not set (a manual run on a disabled
        connection is refused instead). Never raises for the org's sake: every
        outcome is a row and an audit event."""
        c = self.connections.get_by_id(connection_id)
        if c is None:
            return None
        if not c.writeback_enabled:
            if force:
                raise WriteBackRefused("Write-back is off for this connection. Enable it first.")
            return None
        started = self.now()
        with self.runs.db.connection() as conn:
            wb_id = self.runs.create_writeback(conn, connection_id=c.id, tenant_id=c.tenant_id,
                                               snapshot_id=snapshot_id, trigger=trigger, actor=actor,
                                               started_at=started)
        counts = {"candidates": len(scored), "resolved": 0, "unchanged": 0, "written": 0, "failed": 0}
        requests, usage, status, error = 0, None, "succeeded", None
        try:
            with self.runs.db.connection() as conn:
                ids = self.runs.account_ids(conn, c.id)
            wanted: dict[str, dict[str, Any]] = {}
            for cust in scored:
                sf_id = ids.get(str(cust.get("customer_id", "")))
                if sf_id:
                    wanted[sf_id] = reading(cust)
            counts["resolved"] = len(wanted)
            first_error: Optional[str] = None
            targets = list(wanted)
            for i in range(0, len(targets), BATCH):
                chunk = targets[i:i + BATCH]
                r = self._call(connection_id, "GET",
                               f"composite/sobjects/{SOBJECT}?ids={','.join(chunk)}&fields={','.join(FIELD_NAMES)}")
                requests += 1
                usage = _usage(r) or usage
                if r.status_code != 200:
                    raise SalesforceError.from_response(r)
                if usage and (usage["max"] - usage["used"]) / usage["max"] < API_RESERVE and not force:
                    status, error = "deferred", (f"org API allowance low: {usage['used']} of {usage['max']}"
                                                 " used today; the next promotion will try again")
                    break
                current = {rec["Id"]: rec for rec in r.json() if rec}
                changed = [sf_id for sf_id in chunk if not _same(current.get(sf_id, {}), wanted[sf_id])]
                counts["unchanged"] += len(chunk) - len(changed)
                if not changed:
                    continue
                body = {"allOrNone": False,
                        "records": [{"attributes": {"type": SOBJECT}, "Id": sf_id, **wanted[sf_id]}
                                    for sf_id in changed]}
                r = self._call(connection_id, "PATCH", "composite/sobjects", body)
                requests += 1
                usage = _usage(r) or usage
                if r.status_code != 200:
                    raise SalesforceError.from_response(r)
                for result in r.json():
                    if result.get("success"):
                        counts["written"] += 1
                    else:
                        counts["failed"] += 1
                        if first_error is None:
                            e = (result.get("errors") or [{}])[0]
                            first_error = f"{e.get('statusCode', 'ERROR')}: {e.get('message', '')}"[:300]
            if status == "succeeded" and counts["failed"]:
                status = "partial" if counts["written"] else "failed"
                error = first_error
        except SalesforceError as e:
            status, error = "failed", str(e)[:500]
        except TokenError as e:
            status, error = "failed", str(e)[:500]
        except Exception as e:  # noqa: BLE001 - the row must record it
            status, error = "failed", f"{type(e).__name__}: {e}"[:500]
            logger.error(f"write-back {wb_id[:8]} crashed: {error}", exc_info=True)

        finished = self.now()
        with self.runs.db.connection() as conn:
            self.runs.finish_writeback(conn, wb_id, status=status, counts=counts, error=error,
                                       api_requests=requests, api_usage=usage, finished_at=finished)
            self.runs.prune_writebacks(conn, c.id, KEEP)
            if requests:
                self.connections.touch(conn, c.id, finished)
            audit.record(conn, "writeback", actor=actor, tenant_id=c.tenant_id, connection_id=c.id,
                         org_id=c.org_id,
                         detail={"writeback_id": wb_id, "snapshot_id": snapshot_id, "trigger": trigger,
                                 "status": status, "error": error, **counts,
                                 "api_requests": requests, "api_usage": usage,
                                 "duration_seconds": round((finished - started).total_seconds(), 1)})
        level = logging.INFO if status in ("succeeded", "deferred") else logging.WARNING
        logger.log(level, f"write-back {wb_id[:8]} {status} ({trigger}): {counts['written']} written,"
                          f" {counts['unchanged']} unchanged, {counts['failed']} failed,"
                          f" {counts['candidates'] - counts['resolved']} unresolved, {requests} API call(s)"
                          + (f", {error}" if error else ""))
        return WriteBackRow(id=wb_id, tenant_id=c.tenant_id, connection_id=c.id, snapshot_id=snapshot_id,
                            trigger=trigger, status=status, counts=counts, error=error,
                            api_requests=requests, api_usage=usage, actor=actor,
                            started_at=started, finished_at=finished)

    # ---------------------------------------------------------------- helpers

    def _connection(self, connection_id: str):
        c = self.connections.get_by_id(connection_id)
        if c is None:
            raise WriteBackRefused("This connection no longer exists.")
        if c.status != "active":
            raise WriteBackRefused(c.status_reason or "The connection needs a fresh login.")
        return c

    def _call(self, connection_id: str, method: str, path: str, json: Any = None) -> httpx.Response:
        return self.tokens.request(connection_id, path, method=method, json=json)

    def _describe(self, connection_id: str) -> dict[str, dict[str, Any]]:
        """The three fields as the org describes them to the connected user."""
        r = self._call(connection_id, "GET", f"sobjects/{SOBJECT}/describe")
        if r.status_code != 200:
            raise WriteBackRefused(f"Describing {SOBJECT} failed: {SalesforceError.from_response(r)}")
        return {f["name"]: {"type": f.get("type"), "updateable": bool(f.get("updateable"))}
                for f in r.json().get("fields", []) if f.get("name") in FIELD_NAMES}

    def _grant(self, connection_id: str, user_id: str, names: list[str]) -> list[str]:
        """Field-level security for the connected user's own profile: a field
        made through the API is visible to nobody until granted. Best effort;
        the describe afterwards is the check that counts."""
        if not names:
            return []
        r = self._call(connection_id, "GET",
                       f"query?q=SELECT+ProfileId+FROM+User+WHERE+Id+%3D+%27{user_id}%27")
        if r.status_code != 200 or not r.json().get("records"):
            return []
        profile_id = r.json()["records"][0]["ProfileId"]
        r = self._call(connection_id, "GET",
                       "query?q=SELECT+Id+FROM+PermissionSet+WHERE+IsOwnedByProfile+%3D+true"
                       f"+AND+ProfileId+%3D+%27{profile_id}%27")
        if r.status_code != 200 or not r.json().get("records"):
            return []
        parent = r.json()["records"][0]["Id"]
        granted = []
        for name in names:
            r = self._call(connection_id, "POST", "sobjects/FieldPermissions",
                           {"ParentId": parent, "SobjectType": SOBJECT, "Field": f"{SOBJECT}.{name}",
                            "PermissionsRead": True, "PermissionsEdit": True})
            if r.status_code in (200, 201):
                granted.append(name)
            else:
                logger.warning(f"write-back: granting {name} to the connected user's profile answered"
                               f" {r.status_code}: {(r.text or '')[:200]}")
        return granted
