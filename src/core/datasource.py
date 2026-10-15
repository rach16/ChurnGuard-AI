"""
Which customer's data this process serves, chosen by one setting.

``CHURNGUARD_DATA_DIR`` names the folder of canonical CSVs (``data`` for the synthetic
set, ``data/salesforce`` for the connector's output). The DuckDB warehouse built from
that folder follows from it, so the API, the contracts and dbt cannot end up reading
two different customers. ``warehouse/profiles.yml`` applies the same rule for dbt.

``DATA_FOLDER`` is the previous name and is still read, so a deployment that sets it
keeps working. ``DUCKDB_PATH`` still overrides the warehouse file outright.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_DIR = "data"


def data_dir() -> str:
    return (os.getenv("CHURNGUARD_DATA_DIR") or os.getenv("DATA_FOLDER")
            or DEFAULT_DATA_DIR).rstrip("/")


def _suffix() -> str:
    """'' for the synthetic set, '_<source>' otherwise (data/salesforce -> _salesforce)."""
    folder = data_dir()
    if folder == DEFAULT_DATA_DIR:
        return ""
    return "_" + folder.removeprefix(f"{DEFAULT_DATA_DIR}/").replace("/", "_")


def warehouse_path() -> Path:
    if os.getenv("DUCKDB_PATH"):
        return Path(os.environ["DUCKDB_PATH"])
    return Path(f"warehouse/churnguard{_suffix()}.duckdb")


def survival_model_path() -> Path:
    """A model is fitted to one customer's history, so it is per source too.

    Serving the synthetic model against Salesforce accounts would present another
    customer's model as this one's. Until a source has its own (7.6), the path does
    not exist and the API reports the model unavailable, with the reason.
    """
    if os.getenv("SURVIVAL_MODEL_PATH"):
        return Path(os.environ["SURVIVAL_MODEL_PATH"])
    return Path(f"models/survival{_suffix()}.joblib")


def source_name() -> str:
    """'synthetic' for the generated set, else the folder's source (data/salesforce -> salesforce)."""
    folder = data_dir()
    if folder == DEFAULT_DATA_DIR:
        return "synthetic"
    return folder.removeprefix(f"{DEFAULT_DATA_DIR}/").replace("/", "_")


def is_synthetic() -> bool:
    """True when the process serves the generated dataset rather than an integration.

    The site shows only integration data (decided 2026-10-14); the synthetic set is
    test data. Features built only for the synthetic set -- the narrative corpus
    behind the assistant, the evaluation question set -- switch themselves off
    when this is False rather than answer about companies that are not there.
    """
    return source_name() == "synthetic"
