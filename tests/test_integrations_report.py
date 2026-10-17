"""
The Integrations page reports what the Salesforce connector did, from its own report.

Two things are pinned. The numbers served are the numbers in the report file, so
the page cannot drift into copy. And every decision the connector can record has a
plain-English label, so a new rule cannot reach the page as a raw key.
"""

import ast
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from connectors.salesforce import DECISION_LABELS, describe_report  # noqa: E402

CONNECTOR = ROOT / "src" / "connectors" / "salesforce.py"
REPORT = ROOT / "data" / "salesforce" / "ingest_report.json"


def keys_the_connector_can_record() -> set[str]:
    """Every key passed to report.note(), read from the connector's source.

    Covers literal keys, both arms of a conditional, and f-string keys, whose
    possible values are listed below. Plus the one key set directly.
    """
    tree = ast.parse(CONNECTOR.read_text())
    # The values each f-string key can take, by its literal prefix.
    variants = {
        "churn_label_source:": {"status", "lost_renewal", "status+lost_renewal", "active"},
        "save_play_outcome:": {"renewed", "churned", "pending"},
    }
    keys: set[str] = set()

    def literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            keys.add(node.value)
        elif isinstance(node, ast.IfExp):
            literal(node.body)
            literal(node.orelse)
        elif isinstance(node, ast.JoinedStr):
            prefix = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
            assert prefix in variants, f"f-string decision key {prefix!r} has no known values"
            keys.update(prefix + v for v in variants[prefix])

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "note" and node.args):
            literal(node.args[0])
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                and node.value.attr == "counts" and isinstance(node.slice, ast.Constant)):
            keys.add(node.slice.value)
    return keys


def test_every_decision_the_connector_can_record_has_a_label():
    keys = keys_the_connector_can_record()
    assert len(keys) > 20, "the source scan found too few keys; it is probably broken"
    missing = sorted(keys - DECISION_LABELS.keys())
    assert not missing, f"decisions with no label on the Integrations page: {missing}"


def test_no_label_is_left_over_from_a_removed_decision():
    stale = sorted(DECISION_LABELS.keys() - keys_the_connector_can_record())
    assert not stale, f"labels for decisions the connector no longer makes: {stale}"


def test_the_funnel_adds_up_on_the_committed_pull():
    funnel = describe_report(json.loads(REPORT.read_text()))["funnel"]
    assert funnel["accounts_read"] - funnel["prospects_excluded"] \
        - funnel["sample_records_excluded"] == funnel["customers_written"]


def test_the_endpoint_serves_the_report_file(tmp_path, monkeypatch):
    import api

    report = json.loads(REPORT.read_text())
    target = tmp_path / api.SALESFORCE_REPORT
    target.parent.mkdir(parents=True)
    shutil.copy(REPORT, target)
    monkeypatch.setattr(api, "ROOT", tmp_path)

    import asyncio
    served = asyncio.run(api.get_salesforce_integration())

    assert served["as_of"] == report["as_of"] and served["origin"]["kind"] == "file"
    assert served["funnel"]["customers_written"] == report["customers_written"]
    assert {r["key"]: r["count"] for r in served["decisions"]} == report["decisions"]
    assert not [r for r in served["decisions"] if r["group"] == "unlabelled"]


def test_no_pull_yet_is_a_404_that_says_what_to_run(tmp_path, monkeypatch):
    import api

    monkeypatch.setattr(api, "ROOT", tmp_path)
    import asyncio
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.get_salesforce_integration())
    assert e.value.status_code == 404
    assert "src.connectors.salesforce" in e.value.detail


def test_the_endpoint_prefers_the_last_good_snapshot(tmp_path, monkeypatch):
    """With a database, the report comes from the newest snapshot whose contracts
    passed, not from the laptop's file; with none that passed, the file serves."""
    import asyncio
    from datetime import date

    import api
    from tests.salesforce_fakes import FakeStore, FakeSyncStore

    monkeypatch.setattr(api, "ROOT", tmp_path)                  # no file at all
    runs = FakeSyncStore(FakeStore())
    monkeypatch.setattr(api.state, "sync_runs", runs)

    with pytest.raises(HTTPException):
        asyncio.run(api.get_salesforce_integration())             # no snapshot, no file: 404

    report = json.loads(REPORT.read_text())
    with runs.db.connection() as conn:
        bad = runs.create_snapshot(conn, run_id="r1", connection_id="c1", tenant_id="default",
                                   as_of=date(2026, 10, 16), tables={}, report={**report, "accounts_read": 1},
                                   contracts={"passed": 21, "failed": 1, "skipped": 6, "failures": ["x: y"]})
        good = runs.create_snapshot(conn, run_id="r2", connection_id="c1", tenant_id="default",
                                    as_of=date(2026, 10, 16), tables={}, report=report,
                                    contracts={"passed": 22, "failed": 0, "skipped": 6, "failures": []})
    served = asyncio.run(api.get_salesforce_integration())
    assert served["origin"]["kind"] == "sync" and served["origin"]["snapshot_id"] == good.id
    assert served["origin"]["run_id"] == "r2" and served["origin"]["contracts"]["passed"] == 22
    assert served["funnel"]["accounts_read"] == report["accounts_read"]     # not the failing one
    assert bad.id != good.id

    # Once something is promoted the report follows the pointer, even after a
    # rollback to an older snapshot than the newest passing one.
    with runs.db.connection() as conn:
        older = runs.create_snapshot(conn, run_id="r0", connection_id="c1", tenant_id="default",
                                     as_of=date(2026, 10, 15), tables={}, report={**report, "accounts_read": 2},
                                     contracts={"passed": 22, "failed": 0, "skipped": 6, "failures": []})
        runs.set_serving(conn, tenant_id="default", snapshot_id=older.id, run_id="r0", actor="operator")
    rolled = asyncio.run(api.get_salesforce_integration())
    assert rolled["origin"]["snapshot_id"] == older.id and rolled["funnel"]["accounts_read"] == 2
