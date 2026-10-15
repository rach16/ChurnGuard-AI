"""
Save plays: what a CSM did to keep an account, and whether the account renewed after.

Pins the three places this can go wrong: reading the play out of a messy task
subject, matching it to the right renewal, and ranking "what has worked" so a play
that usually fails cannot top the list. Plus the seeding script that planted the
batch, so its counts and its planted mess stay what the docs say. No network.
"""

import random
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from src.connectors.salesforce import canonical_play, transform  # noqa: E402
from tests.test_salesforce_mapping import acct, opp  # noqa: E402

AS_OF = date(2026, 10, 15)


def play(id_, account_id, subject, when, challenge="Adoption Challenges"):
    return {"Id": id_, "AccountId": account_id, "WhatId": account_id, "Subject": subject,
            "Description": f"Challenge: {challenge}. Logged by CSM.", "ActivityDate": when,
            "TaskSubtype": "Task"}


def renewal(id_, account_id, stage, close):
    return {**opp(id_, account_id, stage, close), "Name": f"Account {account_id} - Annual Renewal"}


def run(accounts, opportunities, tasks):
    tables, report = transform({"accounts": accounts, "opportunities": opportunities,
                                "competitors": [], "cases": [], "tasks": tasks}, AS_OF)
    return tables["interventions"], report.to_dict()["decisions"]


def test_messy_subjects_resolve_to_the_play_or_are_dropped():
    assert canonical_play("SAVE PLAY - created internal champions program".split("-", 1)[1]) \
        == "Created internal champions program"
    assert canonical_play("exec business review") == "Held executive business review"
    assert canonical_play("tried the thing from last quarter again") is None
    assert canonical_play("") is None


def test_outcome_is_the_first_renewal_after_the_play():
    accounts = [acct("1", "Won Co"), acct("2", "Lost Co"), acct("3", "Open Co")]
    opps = [
        renewal("r0", "1", "Closed Lost", "2025-01-01"),   # before the play: ignored
        renewal("r1", "1", "Closed Won", "2026-06-01"),
        renewal("r2", "2", "Closed Lost", "2026-06-01"),
        renewal("r3", "3", "Negotiation/Review", "2026-12-01"),
    ]
    tasks = [play("t1", "1", "Save play: Created internal champions program", "2026-04-01"),
             play("t2", "2", "save-play: exec business review", "2026-04-01"),
             play("t3", "3", "Saveplay:onboarding sessions", "2026-09-01")]
    rows, decisions = run(accounts, opps, tasks)
    outcome = {r["company_name"]: r["outcome"] for r in rows}
    assert outcome == {"Won Co": "renewed", "Lost Co": "churned", "Open Co": "pending"}
    assert decisions["save_play_recorded"] == 3


def test_unnamed_plays_and_non_customers_are_dropped_and_counted():
    accounts = [acct("1", "Customer Co"),
                acct("9", "Prospect Co", Type="Prospect", Customer_Status__c="Prospect")]
    tasks = [play("t1", "1", "save play", "2026-04-01", challenge="Pricing Concerns"),
             play("t2", "9", "Save play: Offered custom pricing tier", "2026-04-01")]
    rows, decisions = run(accounts, [], tasks)
    assert rows == []
    # "Pricing Concerns" in the description must not be read as the pricing play.
    assert decisions["save_play_unrecognised_dropped"] == 1
    assert decisions["save_play_not_on_a_customer_dropped"] == 1


def test_usage_after_the_churn_date_is_dropped():
    a = acct("1", "Gone Co", Customer_Status__c="Churned", Churn_Date__c="2026-03-01",
             Last_Login_Date__c="2026-08-01")
    tables, report = transform({"accounts": [a], "opportunities": [], "competitors": [],
                                "cases": [], "tasks": []}, AS_OF)
    assert tables["engagement_snapshots"] == []
    assert report.to_dict()["decisions"]["engagement_after_churn_date_dropped"] == 1


def _engine(tmp_path, rows):
    from core.plays import PlaybookEngine

    pd.read_csv(ROOT / "data" / "success_stories.csv").head(0).to_csv(
        tmp_path / "success_stories.csv", index=False)
    pd.DataFrame(rows).to_csv(tmp_path / "interventions.csv", index=False)
    return PlaybookEngine(str(tmp_path))


def _record(play_name, outcomes, challenge="Adoption Challenges"):
    return [{"customer_id": f"C{i}", "company_name": f"Co {i}", "segment": "SMB",
             "play": play_name, "challenge_category": challenge, "play_date": "2026-01-01",
             "outcome": o, "renewal_close_date": "2026-03-01"} for i, o in enumerate(outcomes)]


def test_a_play_that_usually_fails_cannot_top_what_has_worked(tmp_path):
    rows = (_record("Offered custom pricing tier", ["renewed"] + ["churned"] * 5)
            + _record("Created internal champions program", ["renewed"] * 5 + ["churned"]))
    plays = _engine(tmp_path, rows).plays_for("Low engagement")
    assert [p.action for p in plays] == ["Created internal champions program",
                                         "Offered custom pricing tier"]
    assert (plays[1].renewed, plays[1].decided) == (1, 6)   # failures are counted


def test_pending_renewals_do_not_count_and_thin_records_are_withheld(tmp_path):
    rows = (_record("Ran dedicated onboarding sessions", ["renewed", "renewed", "pending",
                                                          "pending"])
            + _record("Held executive business review", ["renewed", "churned", "renewed"]))
    plays = _engine(tmp_path, rows).plays_for("Low engagement")
    assert [p.action for p in plays] == ["Held executive business review"]
    assert plays[0].decided == 3


def test_a_play_without_enough_on_this_challenge_says_it_is_across_all(tmp_path):
    rows = (_record("Provided API access for customization", ["renewed"], "Adoption Challenges")
            + _record("Provided API access for customization", ["renewed", "churned", "renewed"],
                      "Competition"))
    plays = _engine(tmp_path, rows).plays_for("Low engagement")
    assert plays[0].scope == "any challenge" and plays[0].decided == 4


def test_synthetic_data_keeps_the_write_up_playbook():
    from core.plays import PlaybookEngine

    assert PlaybookEngine(str(ROOT / "data")).mode == "stories"


def test_the_seed_batch_is_what_its_docstring_says():
    import seed_salesforce as seed

    batch = seed.build(random.Random(seed.RNG_SEED))
    summary = seed.summary(batch)
    assert summary["records"] == {"Account": 50, "Opportunity": 100, "Case": 50, "Task": 100}
    assert summary["save_plays"] == 50 and summary["notes"] == 50
    assert summary["storage_kb_estimate"] == 600
    planted = summary["planted"]
    assert planted["duplicate_company_groups"] == 3
    assert planted["missing_arr"] == 4
    assert planted["stale_active_after_lost_renewal"] == 3
    assert planted["sloppy_play_subjects"] == 5
    assert all(a["AccountNumber"].startswith(seed.BATCH_PREFIX) for a in batch["Account"])
    # Deterministic: a second build is identical.
    assert seed.build(random.Random(seed.RNG_SEED)) == batch
