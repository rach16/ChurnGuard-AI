"""
Seed the churnguard-dev Salesforce org with a second batch of customers, reproducibly.

The org is a seeded test org: its records were planted to stand in for a customer's
CRM, and the site says so. The first batch (2026-10-04) was seeded by hand and the
script that did it was never committed, so nobody could rebuild the org. This one is
committed, deterministic (a fixed random seed) and tagged, so the batch can be
inspected, rebuilt and removed.

What it adds, all on new accounts numbered ACC-30001 to ACC-30050:

  50 accounts     48 customers and 2 prospects
  100 deals       each customer's first deal, plus a renewal won, lost or still open
  50 cases        support tickets
  50 CSM notes    calls, meetings and emails, as Tasks
  50 save plays   Tasks whose subject starts "Save play:", recording what a CSM did
                  to keep an at-risk account. The renewal after each play is the
                  outcome: won means saved, lost means churned, open means too early

Outcomes are planted, not observed: each play has a fixed chance of preceding a won
renewal (SAVE_RATES below). Whatever the site reports as "what has worked" on this
batch is therefore what this file put there, and nothing more.

Mess is planted on purpose, so the connector has real work to do: duplicate
companies, missing ARR, a status left "Active" after a lost renewal, out-of-range
CSAT, cases without their original date, activities after a customer left, and save
plays whose subjects do not follow the convention.

Usage:
    uv run python scripts/seed_salesforce.py            # dry run
    uv run python scripts/seed_salesforce.py --apply    # write
    uv run python scripts/seed_salesforce.py --delete   # remove

Calls go through the Salesforce CLI login (`sf api request rest`); no token is handled here.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ORG_ALIAS = "churnguard-dev"
BATCH = "seed-2026-10-15"
MARKER = f"Seeded {BATCH} by scripts/seed_salesforce.py"
AS_OF = date(2026, 10, 15)
RNG_SEED = 20261015
FIRST_NUMBER = 30001
# Every account in this batch, and nothing else in the org, starts with this.
# Description holds the marker too, but long text cannot be filtered in SOQL.
BATCH_PREFIX = "ACC-300"

# Developer Edition counts most records as 2 KB against a 5 MB data allowance; the
# org had 4 MB used before this batch (sf org list limits, 2026-10-15).
KB_PER_RECORD = 2

# The plays a CSM records, and the planted chance that the renewal after each is won.
SAVE_RATES = {
    "Created internal champions program": 0.80,
    "Ran dedicated onboarding sessions": 0.70,
    "Provided API access for customization": 0.60,
    "Held executive business review": 0.50,
    "Offered custom pricing tier": 0.40,
}
CHALLENGES = ["Adoption Challenges", "Product Fit", "Competition", "Support Issues",
              "Pricing Concerns"]
# Planted deviations from the "Save play: <play>" convention. The first three are
# recognisable; the last two are not, and the connector should say it dropped them.
SLOPPY_SUBJECTS = [
    "SAVE PLAY - created internal champions program",
    "save-play: exec business review",
    "Saveplay:onboarding sessions",
    "Save play: tried the thing from last quarter again",
    "save play",
]

ADJECTIVES = ["Amberfield", "Copperleaf", "Silverpine", "Bluewater", "Ironbridge",
              "Clearpath", "Northstar", "Redwood", "Granite", "Summit", "Quarrystone",
              "Evergreen", "Harborpoint", "Keystone", "Sterling", "Westgate", "Oakridge",
              "Starlight", "Riverbend", "Highland", "Cedarview", "Foxglove", "Meridian",
              "Pinecrest", "Stonegate"]
NOUNS = ["Analytics", "Logistics", "Health", "Robotics", "Insurance", "Media", "Foods",
         "Energy", "Software", "Labs", "Retail", "Freight", "Biotech", "Capital",
         "Systems", "Networks", "Education", "Manufacturing", "Telecom", "Security"]
SUFFIXES = ["Inc", "LLC", "Corp", "Group", "Co", "Ltd"]
INDUSTRIES = ["Technology", "Healthcare", "Manufacturing", "Retail", "Finance", "Energy",
              "Education", "Logistics", "Media", "Telecommunications"]
REGIONS = ["NA", "EMEA", "APAC", "LATAM"]
CASE_SUBJECTS = ["Dashboard loading slowly", "Cannot log in after SSO change",
                 "API rate limit errors", "Export to CSV fails", "Report totals look wrong",
                 "Need help configuring alerts", "Webhook not firing",
                 "Billing invoice incorrect", "Mobile app crashes on sync",
                 "Permissions not applied to new users"]
NOTE_SUBJECTS = ["Quarterly check-in", "Expansion discussion", "Training session",
                 "Positive feedback on reporting", "Escalation follow-up",
                 "Renewal planning call", "Champion changed roles",
                 "Asked about cancellation terms", "Mentioned evaluating a competitor",
                 "Usage review"]


def build(rng: random.Random) -> dict[str, list[dict]]:
    """Every record in the batch, keyed by object, with local ids linking children."""
    accounts, opps, cases, notes, plays = [], [], [], [], []
    names = rng.sample([f"{a} {n}" for a in ADJECTIVES for n in NOUNS], 47)

    for i in range(50):
        number = f"ACC-{FIRST_NUMBER + i}"
        prospect = i >= 48
        if 45 <= i < 48:                 # planted duplicates of three earlier companies
            base = accounts[i - 45]["Name"].rsplit(" ", 1)[0]
            name = f"{base.upper()}, {rng.choice(['INC.', 'CORP.', 'LTD.'])}"
        else:
            name = f"{names[i if i < 45 else i - 3]} {rng.choice(SUFFIXES)}"
        segment = rng.choices(["SMB", "Commercial", "Enterprise"], [5, 3, 2])[0]
        arr = round({"SMB": rng.uniform(8e3, 40e3), "Commercial": rng.uniform(40e3, 200e3),
                     "Enterprise": rng.uniform(200e3, 900e3)}[segment])
        seats = max(5, int(arr / rng.uniform(700, 1400)))
        start = AS_OF - timedelta(days=rng.randint(380, 1100))
        end = start + timedelta(days=365 * rng.choice([1, 2, 3]))
        accounts.append({
            "_ref": number, "Name": name, "AccountNumber": number,
            "Type": "Prospect" if prospect else rng.choice(
                ["Customer - Direct", "Customer - Channel", "Other"]),
            "Industry": rng.choice(INDUSTRIES),
            "ARR__c": None if (not prospect and i in (5, 17, 29, 41)) else (None if prospect else arr),
            "Seats__c": None if prospect else seats,
            "Segment__c": None if prospect else segment,
            "Region__c": rng.choice(REGIONS),
            "Customer_Status__c": "Prospect" if prospect else "Active",
            "Contract_Start_Date__c": None if prospect else start.isoformat(),
            "Contract_End_Date__c": None if prospect else end.isoformat(),
            "Active_Users__c": None if prospect else rng.randint(1, seats),
            "Feature_Adoption__c": None if prospect else round(rng.uniform(5, 95), 1),
            "Last_Login_Date__c": None if prospect else (AS_OF - timedelta(days=rng.randint(0, 120))).isoformat(),
            "NPS_Score__c": None if prospect else rng.randint(-60, 70),
            "Description": MARKER,
        })
        acct = accounts[-1]

        if prospect:
            for stage in ("Prospecting", "Qualification"):
                opps.append({"_parent": number, "Name": f"{name} - New Business",
                             "StageName": stage, "Type": "New Customer",
                             "Amount": round(rng.uniform(10e3, 80e3)),
                             "CloseDate": (AS_OF + timedelta(days=rng.randint(20, 120))).isoformat()})
            continue

        opps.append({"_parent": number, "Name": f"{name} - New Business", "StageName": "Closed Won",
                     "Type": "New Customer", "Amount": arr, "CloseDate": start.isoformat()})

        # One save play, ahead of the renewal. Its planted rate decides the renewal.
        play = rng.choice(list(SAVE_RATES))
        challenge = rng.choice(CHALLENGES)
        renewal_date = AS_OF - timedelta(days=rng.randint(10, 300)) if rng.random() < 0.75 \
            else AS_OF + timedelta(days=rng.randint(15, 120))
        play_date = renewal_date - timedelta(days=rng.randint(30, 90))
        plays.append({"_parent": number, "Subject": f"Save play: {play}", "Status": "Completed",
                      "ActivityDate": play_date.isoformat(),
                      "Description": f"Challenge: {challenge}. Logged by CSM after a risk review."})

        if renewal_date > AS_OF:
            stage, loss = "Negotiation/Review", None
        elif rng.random() < SAVE_RATES[play]:
            stage, loss = "Closed Won", None
        else:
            stage, loss = "Closed Lost", rng.choice(["Competition", "Budget", "Champion Left",
                                                     "Poor Support", "Other"])
        opps.append({"_parent": number, "Name": f"{name} - Annual Renewal", "StageName": stage,
                     "Type": "Existing Customer - Replacement", "Amount": arr,
                     "CloseDate": renewal_date.isoformat(),
                     **({"Loss_Reason__c": loss, "Description": f"Lost at renewal: {loss.lower()}."}
                        if loss else {})})
        if stage == "Closed Lost":
            # Planted: three lost renewals where nobody updated the status.
            if sum(1 for a in accounts if a.get("_stale")) < 3:
                acct["_stale"] = True
            else:
                acct.update(Customer_Status__c="Churned", Churn_Date__c=renewal_date.isoformat(),
                            Churn_Category__c=loss)

    customers = [a["AccountNumber"] for a in accounts if a["Customer_Status__c"] != "Prospect"]

    # Two extra plays on existing customers make 50, plus the planted sloppy subjects.
    for number in rng.sample(customers, 2):
        first = next(p for p in plays if p["_parent"] == number)
        plays.append({**first, "Subject": f"Save play: {rng.choice(list(SAVE_RATES))}",
                      "Description": f"Challenge: {rng.choice(CHALLENGES)}. Second attempt."})
    for p, subject in zip(rng.sample(plays, len(SLOPPY_SUBJECTS)), SLOPPY_SUBJECTS):
        p["Subject"] = subject

    for i in range(50):
        parent = rng.choice(customers) if i >= 2 else accounts[48 + i]["AccountNumber"]
        created = AS_OF - timedelta(days=rng.randint(1, 200))
        csat = rng.choice([None, 1, 2, 3, 4, 5, 5, 4])
        cases.append({
            "_parent": parent, "Subject": rng.choice(CASE_SUBJECTS), "Origin": "Email",
            "Type": rng.choice(["Question", "Problem", "Feature Request", None]),
            "Priority": rng.choice(["Low", "Medium", "High"]),
            "Status": rng.choice(["New", "Working", "Escalated", "Closed"]),
            "Description": "Customer reported the issue through the support portal.",
            "Legacy_Created_Date__c": None if i % 9 == 0 else created.isoformat(),
            "CSAT__c": 7 if i in (11, 33) else csat,                  # planted out of range
            "Resolution_Hours__c": rng.randint(1, 96),
        })

    churned = [a for a in accounts if a.get("Churn_Date__c")]
    for i in range(50):
        if i < 3 and i < len(churned):                                # planted: after churn
            acct = churned[i]
            when = date.fromisoformat(acct["Churn_Date__c"]) + timedelta(days=10)
        else:
            pick = rng.choice(customers)
            acct = next(a for a in accounts if a["AccountNumber"] == pick)
            when = AS_OF - timedelta(days=rng.randint(1, 300))
        parent = acct["AccountNumber"]
        subject = rng.choice(NOTE_SUBJECTS)
        notes.append({"_parent": parent, "Subject": subject, "Status": "Completed",
                      "ActivityDate": when.isoformat(),
                      "Description": f"Logged by CSM: {subject.lower()}."})

    for a in accounts:
        a.pop("_stale", None)
    return {"Account": accounts, "Opportunity": opps, "Case": cases,
            "Task": notes + plays}


def summary(batch: dict[str, list[dict]]) -> dict:
    accounts = batch["Account"]
    tasks = batch["Task"]
    plays = [t for t in tasks if "save" in t["Subject"].lower() and "play" in t["Subject"].lower()]
    renewals = [o for o in batch["Opportunity"] if o["Name"].endswith("Annual Renewal")]
    total = sum(len(v) for v in batch.values())
    from src.connectors.salesforce import normalise_name
    names = Counter(normalise_name(a["Name"]) for a in accounts)
    return {
        "records": {k: len(v) for k, v in batch.items()}, "total": total,
        "storage_kb_estimate": total * KB_PER_RECORD,
        "customers": sum(a["Customer_Status__c"] != "Prospect" for a in accounts),
        "prospects": sum(a["Customer_Status__c"] == "Prospect" for a in accounts),
        "save_plays": len(plays), "notes": len(tasks) - len(plays),
        "renewals": dict(Counter(o["StageName"] for o in renewals)),
        "planted": {
            "duplicate_company_groups": sum(1 for n in names.values() if n > 1),
            "missing_arr": sum(1 for a in accounts if a["Customer_Status__c"] != "Prospect"
                               and a["ARR__c"] is None),
            "notes_after_churn": sum(
                1 for t in tasks for a in accounts
                if t["_parent"] == a["AccountNumber"] and a.get("Churn_Date__c")
                and t["ActivityDate"] > a["Churn_Date__c"]),
            "stale_active_after_lost_renewal": sum(
                1 for a in accounts if a["Customer_Status__c"] == "Active" and any(
                    o["_parent"] == a["AccountNumber"] and o["StageName"] == "Closed Lost"
                    for o in batch["Opportunity"])),
            "csat_out_of_range": sum(1 for c in batch["Case"] if c["CSAT__c"] == 7),
            "cases_without_original_date": sum(1 for c in batch["Case"]
                                               if not c["Legacy_Created_Date__c"]),
            "sloppy_play_subjects": len(SLOPPY_SUBJECTS),
        },
    }


def connect():
    """The org, through the Salesforce CLI login. No credential touches this code."""
    from src.connectors.salesforce import CliSession

    return CliSession(ORG_ALIAS)


def _insert(sf, sobject: str, rows: list[dict]) -> list[str]:
    """Insert in collections of 200; stop on the first record Salesforce rejects."""
    ids = []
    for start in range(0, len(rows), 200):
        chunk = [{"attributes": {"type": sobject}, **r} for r in rows[start:start + 200]]
        res = sf.restful("composite/sobjects", method="POST",
                         json={"allOrNone": True, "records": chunk})
        failed = [r for r in res if not r.get("success")]
        if failed:
            sys.exit(f"{sobject} insert rejected: {failed[0].get('errors')}")
        ids += [r["id"] for r in res]
    return ids


def apply(batch: dict[str, list[dict]]) -> None:
    sf = connect()
    existing = sf.query(f"SELECT Id FROM Account WHERE AccountNumber LIKE '{BATCH_PREFIX}%'")["totalSize"]
    if existing:
        sys.exit(f"{existing} accounts from {BATCH} already exist. Run --delete first.")

    clean = lambda r: {k: v for k, v in r.items() if not k.startswith("_") and v is not None}
    account_ids = _insert(sf, "Account", [clean(a) for a in batch["Account"]])
    by_ref = {a["_ref"]: i for a, i in zip(batch["Account"], account_ids)}
    print(f"Account: {len(account_ids)}")

    for sobject, key in (("Opportunity", "AccountId"), ("Case", "AccountId"), ("Task", "WhatId")):
        rows = [{**clean(r), key: by_ref[r["_parent"]]} for r in batch[sobject]]
        print(f"{sobject}: {len(_insert(sf, sobject, rows))}")


def delete() -> None:
    sf = connect()
    accounts = sf.query_all(f"SELECT Id FROM Account WHERE AccountNumber LIKE '{BATCH_PREFIX}%'")["records"]
    ids = [a["Id"] for a in accounts]
    if not ids:
        print(f"Nothing from {BATCH} in the org.")
        return
    quoted = ",".join(f"'{i}'" for i in ids)
    for sobject, key in (("Task", "WhatId"), ("Case", "AccountId"), ("Opportunity", "AccountId")):
        rows = sf.query_all(f"SELECT Id FROM {sobject} WHERE {key} IN ({quoted})")["records"]
        for start in range(0, len(rows), 200):
            chunk = ",".join(r["Id"] for r in rows[start:start + 200])
            sf.restful(f"composite/sobjects?ids={chunk}&allOrNone=true", method="DELETE")
        print(f"{sobject}: deleted {len(rows)}")
    for start in range(0, len(ids), 200):
        sf.restful(f"composite/sobjects?ids={','.join(ids[start:start + 200])}&allOrNone=true",
                   method="DELETE")
    print(f"Account: deleted {len(ids)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the batch to the org")
    mode.add_argument("--delete", action="store_true", help="remove the batch from the org")
    args = ap.parse_args()

    if args.delete:
        delete()
        return
    batch = build(random.Random(RNG_SEED))
    print(json.dumps(summary(batch), indent=2))
    if args.apply:
        apply(batch)
    else:
        print("\nDry run: nothing written. Pass --apply to write this batch.")


if __name__ == "__main__":
    main()
