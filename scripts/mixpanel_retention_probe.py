"""
Prove Mixpanel Free keeps events backdated by years, and returns them on export.

Phase 1.6 seeds ~2.5 years of weekly product usage into Mixpanel, so the first
question was whether the free plan keeps events that old at all. PostHog Free does
not: one year of query retention. This is the check STATUS.md cites for Mixpanel,
and the only reproducible form of it -- the 2026-10-04 run was done by hand and
nothing of it was committed.

It sends one `cg_retention_probe` event per year back (1, 2 and 3 years before
--as-of by default) through /import, then reads them back through the raw export
API. Sending is idempotent: `$insert_id` is derived from the probe date, so Mixpanel
drops a repeat rather than storing it twice.

Reading back is the default and is read-only. Sending is opt-in, because it writes
to the project and because the read-back alone is what shows retention still holds.

Usage:
    uv run python scripts/mixpanel_retention_probe.py          # read back; exit 1 if a year is missing
    uv run python scripts/mixpanel_retention_probe.py --send   # (re)send the probes, then read back
    uv run python scripts/mixpanel_retention_probe.py --as-of 2026-10-01 --years 1,2,3

Needs MIXPANEL_PROJECT_ID, MIXPANEL_SA_USERNAME and MIXPANEL_SA_SECRET in .env: a
service account, not a login. US data residency; EU projects use data-eu / api-eu.

Measured 2026-10-14: the three probes sent 2026-10-04 were all returned.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

EVENT = "cg_retention_probe"
DISTINCT_ID = "cg-probe"
EXPORT_URL = "https://data.mixpanel.com/api/2.0/export"
IMPORT_URL = "https://api.mixpanel.com/import"

# The run STATUS.md cites. Steps of 365 days rather than calendar years, which is
# why the third probe lands on 2023-10-02: 2024 was a leap year.
DEFAULT_AS_OF = date(2026, 10, 1)
DEFAULT_YEARS = (1, 2, 3)


def probe_dates(as_of: date, years) -> list[date]:
    return [as_of - timedelta(days=365 * n) for n in years]


def probe_event(day: date) -> dict:
    """One /import payload. The insert id pins the date, so a resend is a no-op."""
    # Noon UTC, so the event falls on the same calendar day whatever timezone the
    # project reports in. A midnight stamp can shift a day either way.
    stamp = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    return {
        "event": EVENT,
        "properties": {
            "time": int(stamp.timestamp()),
            "distinct_id": DISTINCT_ID,
            "$insert_id": f"cg-probe-{day.isoformat()}",
            "probe_date": datetime(day.year, day.month, day.day).isoformat(),
        },
    }


def dates_in_export(lines) -> set[date]:
    """The probe dates present in raw export output, one JSON object per line."""
    found: set[date] = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("event") != EVENT:
            continue
        stamp = row["properties"]["time"]
        found.add(datetime.fromtimestamp(stamp, tz=timezone.utc).date())
    return found


def report(days: list[date], years, found: set[date], today: date) -> list[date]:
    """Print one line per probe and return the dates that did not come back."""
    missing = []
    for n, day in zip(years, days):
        ok = day in found
        if not ok:
            missing.append(day)
        print(f"  {n} year(s) back  {day}  {'returned' if ok else 'MISSING'}"
              f"  ({(today - day).days} days old)")
    if missing:
        print(f"{len(missing)} of {len(days)} probe(s) missing. "
              "Either not sent yet (--send), or retention has lapsed.")
    else:
        print(f"all {len(days)} probes returned; retention holds as of {today}")
    return missing


def credentials() -> tuple[str, str]:
    """Project id and a Basic auth header. Values are never printed."""
    try:
        project_id = os.environ["MIXPANEL_PROJECT_ID"]
        user = os.environ["MIXPANEL_SA_USERNAME"]
        secret = os.environ["MIXPANEL_SA_SECRET"]
    except KeyError as exc:
        sys.exit(f"{exc.args[0]} is not set. It belongs in .env; see .env.example.")
    token = base64.b64encode(f"{user}:{secret}".encode()).decode()
    return project_id, f"Basic {token}"


def _request(url: str, auth: str, body: bytes | None = None) -> str:
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
    req.add_header("Authorization", auth)
    req.add_header("Accept", "application/json")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as exc:
        sys.exit(f"{exc.code} from {url.split('?')[0]}: {exc.read().decode()[:300]}")


def send(days: list[date], project_id: str, auth: str) -> None:
    query = urllib.parse.urlencode({"project_id": project_id, "strict": 1})
    body = json.dumps([probe_event(d) for d in days]).encode()
    print(f"sent {len(days)} probe(s): {_request(f'{IMPORT_URL}?{query}', auth, body).strip()}")


def read_back(days: list[date], project_id: str, auth: str) -> set[date]:
    query = urllib.parse.urlencode({
        "project_id": project_id,
        "from_date": min(days).isoformat(),
        "to_date": max(days).isoformat(),
        "event": json.dumps([EVENT]),
    })
    return dates_in_export(_request(f"{EXPORT_URL}?{query}", auth).splitlines())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument("--as-of", type=date.fromisoformat, default=DEFAULT_AS_OF,
                    help=f"date the probes count back from (default {DEFAULT_AS_OF})")
    ap.add_argument("--years", default=",".join(map(str, DEFAULT_YEARS)),
                    help="comma-separated years back (default 1,2,3)")
    ap.add_argument("--send", action="store_true",
                    help="import the probes before reading back; idempotent")
    args = ap.parse_args()

    years = [int(y) for y in args.years.split(",") if y]
    days = probe_dates(args.as_of, years)
    project_id, auth = credentials()

    if args.send:
        send(days, project_id, auth)
    found = read_back(days, project_id, auth)
    missing = report(days, years, found, today=date.today())
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
