"""
The Evaluation page must be able to show what the instructions on it produce.

Before this, three things were true at once: the page told the reader to run
benchmark_retrieval.py, that script wrote nothing to disk, and the endpoint
could only parse a RAGAS file whose columns the script does not measure. Each
piece looked reasonable alone. Together they meant following the instruction on
screen changed the screen not at all, for ever.

So the property under test is the join, not any one part: what the script writes
is what the endpoint reads, and what the endpoint returns is shaped the way the
table renders it.
"""

from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import api  # noqa: E402
import benchmark_retrieval as bench  # noqa: E402


SCORES = {
    "hybrid": {
        "single": {"n": 34, "hit_rate": 0.9706, "recall": 0.9412, "mrr": 0.8824},
        "all": {"n": 41, "hit_rate": 0.8537, "recall": 0.7317, "mrr": 0.7561},
    },
    "naive": {
        "single": {"n": 34, "hit_rate": 0.7353, "recall": 0.5441, "mrr": 0.6176},
        "all": {"n": 41, "hit_rate": 0.6341, "recall": 0.4390, "mrr": 0.5122},
    },
}

RAGAS_HEADER = ["Method", "faithfulness", "answer_relevancy", "context_recall",
                "context_precision", "answer_correctness", "semantic_similarity"]


@pytest.fixture
def written(tmp_path) -> Path:
    out = tmp_path / "retrieval_benchmark.csv"
    bench.write_results(SCORES, out, k=5)
    return out


def call_endpoint():
    return asyncio.run(api.get_evaluation_results())


@pytest.fixture
def baselines(tmp_path, monkeypatch):
    """Point the endpoint at a scratch directory so the committed baseline
    cannot make these tests pass, or fail, by accident.

    Patching ROOT is the whole redirection, which is the point: the endpoint
    resolves its paths per request, so there is one knob rather than two
    module-level Paths that a caller can miss.
    """
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    monkeypatch.setattr(api, "ROOT", tmp_path)
    return metrics / "ragas_evaluation_results.csv", metrics / "retrieval_benchmark.csv"


def write_ragas(path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(RAGAS_HEADER)
        w.writerow(["parent_document", 0.912, 0.887, 0.845, 0.901, 0.792, 0.938])


# --- the script actually writes something -------------------------------------

def test_write_results_creates_the_file(written):
    """The original defect in one assertion: running the benchmark left no trace."""
    assert written.exists()


def test_written_columns_match_the_declared_contract(written):
    with open(written, newline="", encoding="utf-8") as fh:
        assert next(csv.reader(fh)) == bench.FIELDNAMES


def test_one_row_per_method_with_both_cohorts_kept_apart(written):
    with open(written, newline="", encoding="utf-8") as fh:
        rows = {r["method"]: r for r in csv.DictReader(fh)}

    assert set(rows) == {"hybrid", "naive"}
    # Single-entity and overall must not be averaged into one figure -- that is
    # the distinction the whole benchmark exists to preserve.
    assert float(rows["hybrid"]["single_hit_rate"]) == pytest.approx(0.9706)
    assert float(rows["hybrid"]["all_hit_rate"]) == pytest.approx(0.8537)


def test_creates_the_metrics_directory_if_absent(tmp_path):
    out = tmp_path / "does" / "not" / "exist" / "retrieval_benchmark.csv"
    bench.write_results(SCORES, out, k=5)
    assert out.exists()


# --- what it writes is what the endpoint reads --------------------------------

def test_endpoint_serves_the_file_the_script_wrote(baselines, written):
    _, retrieval = baselines
    retrieval.write_bytes(written.read_bytes())

    payload = call_endpoint()

    assert payload["kind"] == "retrieval"
    assert {r["method"] for r in payload["results"]} == {"Hybrid", "Naive"}


def test_rows_carry_only_metrics(baselines, written):
    """The table renders every non-method key as a column, so a sample size or a
    timestamp sitting in a row would be displayed as though it were a score."""
    _, retrieval = baselines
    retrieval.write_bytes(written.read_bytes())

    row = call_endpoint()["results"][0]

    assert set(row) == {"method", "single_hit_rate", "single_recall", "single_mrr",
                        "all_hit_rate", "all_recall", "all_mrr"}


def test_rates_stay_on_zero_to_one(baselines, written):
    """0.971 is the figure quoted in the ADRs and STATUS. Rescaling it to 97.1
    in one surface only is how two numbers for one measurement start circulating."""
    _, retrieval = baselines
    retrieval.write_bytes(written.read_bytes())

    hybrid = next(r for r in call_endpoint()["results"] if r["method"] == "Hybrid")
    assert hybrid["single_hit_rate"] == pytest.approx(0.971, abs=1e-3)


def test_note_carries_the_measurement_date(baselines, written):
    """A table of scores with no date cannot tell a reader it is stale."""
    _, retrieval = baselines
    retrieval.write_bytes(written.read_bytes())

    with open(written, newline="", encoding="utf-8") as fh:
        stamp = next(csv.DictReader(fh))["generated_at"]

    assert stamp in call_endpoint()["note"]


def test_note_carries_k_and_sample_sizes(baselines, written):
    _, retrieval = baselines
    retrieval.write_bytes(written.read_bytes())

    note = call_endpoint()["note"]
    assert "k=5" in note and "34" in note and "41" in note


# --- choosing between the two, and having neither -----------------------------

def test_ragas_wins_when_both_exist(baselines, written):
    ragas, retrieval = baselines
    write_ragas(ragas)
    retrieval.write_bytes(written.read_bytes())

    assert call_endpoint()["kind"] == "ragas"


def test_ragas_still_serves_percentages(baselines):
    ragas, _ = baselines
    write_ragas(ragas)

    row = call_endpoint()["results"][0]
    assert row["method"] == "Parent Document"
    assert row["faithfulness"] == pytest.approx(91.2)


def test_404_when_no_baseline_exists(baselines):
    """Absence is the honest normal state, not an error to be dressed up."""
    with pytest.raises(HTTPException) as e:
        call_endpoint()

    assert e.value.status_code == 404
    # The 404 has to name the free path. Naming only the paid RAGAS run is what
    # left the page unactionable.
    assert "benchmark_retrieval.py" in e.value.detail


def test_the_image_carries_the_baseline_directory():
    """A committed baseline the container cannot see is the 2.9 failure again.

    There, the model and the warehouse were absent from the image and the
    service came up reporting healthy while serving nothing. The Dockerfile
    creates cache/ and notebooks/ at runtime; metrics/ must be copied, because
    what it holds is committed input rather than runtime scratch.
    """
    dockerfile = (ROOT / "src" / "backend" / "Dockerfile").read_text()

    assert "COPY metrics/" in dockerfile
    # The same line must not appear in the runtime mkdir, which would create an
    # empty directory over the copy and hide the omission.
    mkdir_lines = [l for l in dockerfile.splitlines() if "mkdir -p" in l]
    assert not any("metrics" in l for l in mkdir_lines)


def test_unreadable_baseline_is_a_500_not_a_silent_404(baselines):
    """A corrupt file must not be reported as 'nothing measured yet'."""
    _, retrieval = baselines
    retrieval.write_text("method,single_hit_rate\nhybrid,not-a-number\n")

    with pytest.raises(HTTPException) as e:
        call_endpoint()

    assert e.value.status_code == 500
