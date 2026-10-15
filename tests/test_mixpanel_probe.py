"""
The retention probe's pure parts: which dates it chooses, what it sends, and how
it reads the export back. The network calls are not tested here; running the
script is the test of those, and STATUS.md records when it was last run.
"""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mixpanel_retention_probe as probe  # noqa: E402


def test_default_dates_are_the_ones_status_md_records():
    # 365-day steps from 2026-10-01. The 2024 leap day is why the third lands
    # on the 2nd, and why the test pins the dates rather than the arithmetic.
    assert probe.probe_dates(probe.DEFAULT_AS_OF, probe.DEFAULT_YEARS) == [
        date(2025, 10, 1),
        date(2024, 10, 1),
        date(2023, 10, 2),
    ]


def test_payload_matches_what_was_sent_on_2026_10_04():
    # Shape read back from the export API on 2026-10-14. If this drifts, a
    # resend stops deduplicating against the probes already in the project.
    event = probe.probe_event(date(2025, 10, 1))
    assert event["event"] == "cg_retention_probe"
    props = event["properties"]
    assert props["time"] == 1759320000  # 2025-10-01T12:00:00Z
    assert props["distinct_id"] == "cg-probe"
    assert props["$insert_id"] == "cg-probe-2025-10-01"
    assert props["probe_date"] == "2025-10-01T00:00:00"


def test_export_lines_reduce_to_the_probe_dates_present():
    lines = [
        json.dumps({"event": "cg_retention_probe", "properties": {"time": 1759320000}}),
        json.dumps({"event": "$session_start", "properties": {"time": 1759320000}}),
        "",  # the export ends with a blank line
    ]
    assert probe.dates_in_export(lines) == {date(2025, 10, 1)}


def test_report_returns_the_missing_dates_and_says_so(capsys):
    days = [date(2025, 10, 1), date(2024, 10, 1)]
    missing = probe.report(days, [1, 2], found={date(2025, 10, 1)}, today=date(2026, 10, 14))
    assert missing == [date(2024, 10, 1)]
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert "1 of 2" in out


def test_report_is_clean_when_every_probe_came_back(capsys):
    days = [date(2025, 10, 1)]
    assert probe.report(days, [1], found=set(days), today=date(2026, 10, 14)) == []
    assert "retention holds" in capsys.readouterr().out
