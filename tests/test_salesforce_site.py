"""
The site on Salesforce data: real figures, or an honest "not available", never invented.

1.8 switched the site to integration data. What these pin is the rule that came
with it: where the source cannot support a figure, the API says so plainly instead
of inventing one, and nothing the old synthetic-only code generated (a per-feature
usage breakdown, a heuristic churn date, a score passed off as a probability)
reaches a page.
"""

import asyncio
import math
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "backend"))

from core.health_scoring import AT_RISK_SHARE, CustomerHealthScorer  # noqa: E402

SALESFORCE = "data/salesforce"
INVENTED = {"feature_usage", "days_until_churn", "churn_probability", "avg_days_to_churn"}


def _keys(obj) -> set[str]:
    """Every key anywhere in a nested response."""
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


@pytest.mark.parametrize("folder", ["data", SALESFORCE])
def test_nothing_invented_reaches_a_response(folder):
    scorer = CustomerHealthScorer(data_folder=folder)
    first = scorer.score_active_customers()[0]["id"]
    for response in (scorer.get_dashboard_stats(), scorer.get_customer_detail(first),
                     scorer.score_active_customers()):
        assert not (_keys(response) & INVENTED), _keys(response) & INVENTED


@pytest.mark.parametrize("folder", ["data", SALESFORCE])
def test_at_risk_is_the_top_share_of_the_book(folder):
    scorer = CustomerHealthScorer(data_folder=folder)
    stats = scorer.get_dashboard_stats()
    active = stats["total_active_customers"]
    assert stats["total_at_risk"] == math.ceil(active * AT_RISK_SHARE)
    assert "top 20%" in stats["at_risk_basis"]


def test_salesforce_flags_accounts_a_fixed_line_missed():
    # The reason for 1.8's option B: on the first pull no account reached 60.
    scorer = CustomerHealthScorer(data_folder=SALESFORCE)
    scored = scorer.score_active_customers()
    assert max(c["risk_score"] for c in scored) < 60
    assert scorer.get_dashboard_stats()["total_at_risk"] > 0


def test_history_depth_is_measured_not_assumed():
    assert CustomerHealthScorer(data_folder=SALESFORCE).history_weeks() == 1
    assert CustomerHealthScorer(data_folder="data").history_weeks() >= 104


@pytest.fixture
def api_on(monkeypatch, tmp_path):
    """A fresh service state serving the given source, with no model on disk."""
    import api

    def start(folder: str):
        monkeypatch.setenv("CHURNGUARD_DATA_DIR", folder)
        monkeypatch.setenv("SURVIVAL_MODEL_PATH", str(tmp_path / "absent.joblib"))
        # Never reach a real vector store or model provider from a test. Salesforce
        # is switched off before any connection; the synthetic path is disabled.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ENABLE_RAG", "true" if folder != "data" else "false")
        monkeypatch.setattr(api, "state", api.ServiceState())
        api._init_health_scorer()
        api._init_ai_stack()
        return api

    return start


def test_salesforce_model_endpoints_say_not_enough_history(api_on):
    api = api_on(SALESFORCE)
    customer = api.state.health_scorer.score_active_customers()[0]["id"]

    for response in (asyncio.run(api.get_customer_likelihood(customer)),
                     asyncio.run(api.get_book_exposure()),
                     asyncio.run(api.get_customer_exposure(customer))):
        assert response["available"] is False
        assert response["weeks_of_history"] == 1
        assert response["weeks_needed"] == api.MIN_HISTORY_WEEKS
        assert "Not enough history" in response["reason"]


def test_a_missing_model_on_deep_history_is_still_a_fault(api_on):
    # The history check must not hide a broken build on a source that has the data.
    api = api_on("data")
    assert "survival_model" not in api.state.not_applicable
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.get_customer_likelihood(1))
    assert e.value.status_code == 503


def test_salesforce_assistant_is_off_by_design_and_ready_says_so(api_on):
    api = api_on(SALESFORCE)
    assert set(api.AI_COMPONENTS) <= set(api.state.not_applicable)

    ready = asyncio.run(api.readiness_check(require_ai_stack=True))
    assert ready["ready"] is True
    assert ready["source"] == "salesforce"
    assert "synthetic test data" in ready["not_applicable"]["rag_retriever"]

    health = asyncio.run(api.health_check())
    assert health.status == "healthy" and health.degraded is False


def test_salesforce_evaluation_explains_why_there_is_none(api_on):
    api = api_on(SALESFORCE)
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.get_evaluation_results())
    assert e.value.status_code == 404
    assert "salesforce" in e.value.detail and "synthetic" in e.value.detail


def test_source_names_the_data_on_screen(api_on):
    api = api_on(SALESFORCE)
    source = asyncio.run(api.get_source())
    import json
    pulled = json.loads((ROOT / "data/salesforce/ingest_report.json").read_text())["as_of"]
    assert source == {"source": "salesforce", "label": "Salesforce", "synthetic": False,
                      "as_of": pulled, "seeded": True,
                      "served_from": {"kind": "file", "path": "data/salesforce/ingest_report.json"}}

    api = api_on("data")
    assert asyncio.run(api.get_source())["synthetic"] is True


def test_each_account_says_when_it_entered_the_crm():
    # The pull date is the same for every account (each sync re-reads everything);
    # the created date is what tells the two seeded batches apart.
    scored = CustomerHealthScorer(data_folder=SALESFORCE).score_active_customers()
    assert all(c["crm_created_date"] for c in scored)
    assert {c["crm_created_date"] for c in scored} == {"2026-10-04", "2026-10-15"}
    synthetic = CustomerHealthScorer(data_folder="data").score_active_customers()
    assert all(c["crm_created_date"] is None for c in synthetic)
