"""
Serving a promoted snapshot (ADR-0012, section 6, 1.10.3).

What these pin: a snapshot restored to disk is the CSV set the laptop pull would
have written, with a marker naming it; restoring the same snapshot again is a
no-op and restoring another removes the first; the scorer loads from the
restored folder and scores it exactly as it scores the files; the datasource
override points the loaders at the folder and names what is served; and the
API's wake-up path restores whatever the pointer names, or leaves the data
folder serving when there is no pointer.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from connectors.salesforce import COLUMNS, as_csv_rows  # noqa: E402
from core import datasource  # noqa: E402
from integrations import serving  # noqa: E402
from integrations.sync_store import Snapshot  # noqa: E402

SALESFORCE_DIR = ROOT / "data" / "salesforce"


@pytest.fixture(autouse=True)
def no_serving_override():
    datasource.set_serving(None)
    yield
    datasource.set_serving(None)


def load_dir(folder: Path) -> dict[str, list[dict]]:
    out = {}
    for name in COLUMNS:
        with open(folder / f"{name}.csv", newline="", encoding="utf-8") as fh:
            out[name] = list(csv.DictReader(fh))
    return out


def snapshot_of_the_laptop_pull(snapshot_id: str = "a" * 32) -> Snapshot:
    """The committed Salesforce pull as the sync engine would store it: the
    canonical tables in CSV shape and the ingest report. Built from the
    committed files, not the raw dump, because the dump is gitignored and CI
    has none (the first version failed there, 2026-10-16)."""
    tables = as_csv_rows(load_dir(SALESFORCE_DIR))
    report = json.loads((SALESFORCE_DIR / "ingest_report.json").read_text())
    return Snapshot(id=snapshot_id, tenant_id="default", connection_id="c" * 32, run_id="r" * 32,
                    as_of=date.fromisoformat(report["as_of"]), tables=tables, report=report,
                    contracts={"passed": 22, "failed": 0, "skipped": 6, "failures": []},
                    contracts_passed=True, row_counts={n: len(r) for n, r in tables.items()},
                    created_at=datetime(2026, 10, 16, 20, 28, tzinfo=timezone.utc))


def test_a_restored_snapshot_is_the_csv_set_the_pull_wrote(tmp_path):
    snap = snapshot_of_the_laptop_pull()
    folder = serving.restore(snap, tmp_path)
    assert folder == tmp_path / snap.id
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [f"{n}.csv" for n in COLUMNS] + ["ingest_report.json", serving.MARKER])

    restored = load_dir(folder)
    committed = load_dir(SALESFORCE_DIR)
    for name in COLUMNS:
        assert restored[name] == committed[name], f"{name} differs from the committed pull"
    assert json.loads((folder / "ingest_report.json").read_text()) == json.loads(
        (SALESFORCE_DIR / "ingest_report.json").read_text())
    marker = serving.read_marker(folder)
    assert marker["snapshot_id"] == snap.id and marker["row_counts"]["customers"] == 187


def test_restore_is_idempotent_and_keeps_one_snapshot_on_disk(tmp_path):
    first = snapshot_of_the_laptop_pull("1" * 32)
    folder = serving.restore(first, tmp_path)
    stamp = serving.read_marker(folder)["restored_at"]
    assert serving.restore(first, tmp_path) == folder
    assert serving.read_marker(folder)["restored_at"] == stamp           # not rewritten

    second = snapshot_of_the_laptop_pull("2" * 32)
    other = serving.restore(second, tmp_path)
    assert other.exists() and not folder.exists()                        # one snapshot on disk
    assert [p.name for p in tmp_path.iterdir()] == [second.id]


def test_an_incomplete_snapshot_is_refused_not_served_empty(tmp_path):
    from dataclasses import replace
    snap = snapshot_of_the_laptop_pull()
    head_only = replace(snap, tables={})
    with pytest.raises(serving.IncompleteSnapshot, match="customers 0 of 187"):
        serving.restore(head_only, tmp_path)
    assert list(tmp_path.iterdir()) == []                               # nothing written


def test_the_scorer_reads_a_restored_snapshot_like_the_files(tmp_path):
    from core.health_scoring import CustomerHealthScorer

    folder = serving.restore(snapshot_of_the_laptop_pull(), tmp_path)
    from_snapshot = CustomerHealthScorer(data_folder=str(folder)).get_dashboard_stats()
    from_files = CustomerHealthScorer(data_folder=str(SALESFORCE_DIR)).get_dashboard_stats()
    assert from_snapshot == from_files
    assert from_snapshot["total_at_risk"] > 0


def test_the_datasource_override_points_the_loaders_at_the_folder(monkeypatch):
    monkeypatch.setenv("CHURNGUARD_DATA_DIR", "data/salesforce")
    assert datasource.serving_dir() == "data/salesforce" and datasource.serving_info() is None
    datasource.set_serving("data/serving/abc/", {"kind": "sync", "snapshot_id": "abc"})
    assert datasource.serving_dir() == "data/serving/abc"
    assert datasource.serving_info() == {"kind": "sync", "snapshot_id": "abc"}
    assert datasource.source_name() == "salesforce" and datasource.data_dir() == "data/salesforce"
    datasource.set_serving(None)
    assert datasource.serving_dir() == "data/salesforce"


def test_wake_restores_what_the_pointer_names(tmp_path, monkeypatch):
    import api
    from tests.salesforce_fakes import FakeStore, FakeSyncStore

    monkeypatch.setattr(serving, "SERVING_ROOT", tmp_path)
    monkeypatch.setattr(api.serving, "SERVING_ROOT", tmp_path)
    runs = FakeSyncStore(FakeStore())
    monkeypatch.setattr(api.state, "sync_runs", runs)

    api._restore_serving()                                            # no pointer: nothing
    assert datasource.serving_info() is None and list(tmp_path.iterdir()) == []

    snap = snapshot_of_the_laptop_pull()
    runs.snaps[snap.id] = snap
    with runs.db.connection() as conn:
        runs.set_serving(conn, tenant_id="default", snapshot_id=snap.id, run_id=snap.run_id, actor="system")
    api._restore_serving()
    assert datasource.serving_dir() == str(tmp_path / snap.id)
    assert datasource.serving_info()["snapshot_id"] == snap.id and (tmp_path / snap.id / "customers.csv").exists()

    # A pointer at a snapshot that is gone leaves the data folder serving, loudly.
    with runs.db.connection() as conn:
        runs.set_serving(conn, tenant_id="default", snapshot_id="gone", run_id=None, actor="system")
    datasource.set_serving(None)
    api._restore_serving()
    assert datasource.serving_info() is None


def test_source_endpoint_names_the_served_snapshot(monkeypatch):
    import asyncio

    import api

    monkeypatch.setenv("CHURNGUARD_DATA_DIR", "data/salesforce")
    datasource.set_serving("data/serving/x", {"kind": "sync", "snapshot_id": "x", "run_id": "r",
                                               "as_of": "2026-10-16", "row_counts": {"customers": 187},
                                               "created_at": "2026-10-16T20:28:00+00:00"})
    served = asyncio.run(api.get_source())
    assert served["source"] == "salesforce" and served["as_of"] == "2026-10-16"
    assert served["served_from"]["kind"] == "sync" and served["served_from"]["snapshot_id"] == "x"

    datasource.set_serving(None)
    from_file = asyncio.run(api.get_source())
    assert from_file["served_from"]["kind"] == "file" and from_file["as_of"] == "2026-10-15"
