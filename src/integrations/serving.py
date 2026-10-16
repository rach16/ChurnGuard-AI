"""
Putting a snapshot where the loaders can read it (ADR-0012, section 6, 1.10.3).

The scorer, the playbook and the evidence index read CSV files from a folder.
That is the right interface for them to keep: it is what the laptop pull
writes, what the contracts check and what dbt builds from. So serving a
snapshot means writing its tables back out as those files, into a folder of
its own under ``data/serving/<snapshot id>``, and pointing the loaders there.

Render's disk does not survive a restart, so this happens on every wake as
well as on every promotion: the database holds the pointer and the snapshot,
and the disk is a cache of it. A marker file in the folder names the snapshot
it holds, so a folder that already matches the pointer is reused.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from connectors.salesforce import COLUMNS
from integrations.sync_store import Snapshot

logger = logging.getLogger(__name__)

SERVING_ROOT = Path("data") / "serving"
MARKER = "SNAPSHOT.json"


def restore(snapshot: Snapshot, root: Optional[Path] = None) -> Path:
    """Write the snapshot's tables as CSV files into ``root/<id>`` and return it.

    Idempotent: a folder whose marker already names this snapshot is left as it
    is. Other folders under ``root`` are removed, so the disk holds one
    snapshot, the one being served. The write goes to a temporary folder first
    and is renamed into place, so a crash mid-write leaves no half folder with
    a marker.
    """
    root = root or SERVING_ROOT
    target = root / snapshot.id
    if read_marker(target) and read_marker(target)["snapshot_id"] == snapshot.id:
        return target

    tmp = root / f".{snapshot.id}.partial"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for name, columns in COLUMNS.items():
        with open(tmp / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            w.writeheader()
            w.writerows(snapshot.tables.get(name, []))
    (tmp / "ingest_report.json").write_text(json.dumps(snapshot.report, indent=2))
    (tmp / MARKER).write_text(json.dumps({
        "snapshot_id": snapshot.id, "run_id": snapshot.run_id, "as_of": snapshot.as_of.isoformat(),
        "row_counts": snapshot.row_counts, "contracts": snapshot.contracts,
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))

    if target.exists():
        shutil.rmtree(target)
    tmp.rename(target)
    for other in root.iterdir():
        if other.is_dir() and other != target:
            shutil.rmtree(other, ignore_errors=True)
    logger.info(f"✓ snapshot {snapshot.id[:8]} restored to {target} "
                f"({snapshot.row_counts.get('customers', 0)} customers)")
    return target


def read_marker(folder: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads((folder / MARKER).read_text())
    except (OSError, ValueError):
        return None
