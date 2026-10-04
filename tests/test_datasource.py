"""One setting chooses the customer; the warehouse and model files follow it."""

from pathlib import Path

import pytest

from src.core.datasource import data_dir, survival_model_path, warehouse_path

VARS = ("CHURNGUARD_DATA_DIR", "DATA_FOLDER", "DUCKDB_PATH", "SURVIVAL_MODEL_PATH")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)


def test_default_is_the_synthetic_set_and_its_existing_files():
    assert data_dir() == "data"
    assert warehouse_path() == Path("warehouse/churnguard.duckdb")
    assert survival_model_path() == Path("models/survival.joblib")


def test_a_connector_source_gets_its_own_warehouse_and_model(monkeypatch):
    monkeypatch.setenv("CHURNGUARD_DATA_DIR", "data/salesforce/")
    assert data_dir() == "data/salesforce"
    assert warehouse_path() == Path("warehouse/churnguard_salesforce.duckdb")
    # No model has been fitted to this source, so the path must not be the synthetic one.
    assert survival_model_path() == Path("models/survival_salesforce.joblib")


def test_previous_name_still_works_and_new_name_wins(monkeypatch):
    monkeypatch.setenv("DATA_FOLDER", "data/salesforce")
    assert data_dir() == "data/salesforce"
    monkeypatch.setenv("CHURNGUARD_DATA_DIR", "data")
    assert data_dir() == "data"


def test_explicit_paths_override(monkeypatch):
    monkeypatch.setenv("CHURNGUARD_DATA_DIR", "data/salesforce")
    monkeypatch.setenv("DUCKDB_PATH", "/tmp/x.duckdb")
    monkeypatch.setenv("SURVIVAL_MODEL_PATH", "/tmp/m.joblib")
    assert warehouse_path() == Path("/tmp/x.duckdb")
    assert survival_model_path() == Path("/tmp/m.joblib")
