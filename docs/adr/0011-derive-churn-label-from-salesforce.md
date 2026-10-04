# 0011 — Derive the churn label from a Salesforce org, and report every guess

**Status:** Accepted · **Date:** 2026-10-04 · **Phase:** 1.5

## Context

Every number ChurnGuard publishes comes from data this project generated
(README §10). The largest gap between the project and something a customer could
use is an ingestion path from a system a customer actually runs.

A Salesforce Developer Edition org was seeded to stand in for that customer. It is
deliberately in the condition a real CRM is in:

- **No churn column.** Salesforce records that a renewal opportunity was lost, not
  that a customer left. Some accounts carry a `Customer_Status__c` a CSM set by
  hand; many do not, and some that do are stale.
- **Duplicate accounts.** The same company typed three ways (`Acme Industrial
  Supply`, `ACME Industrial Supply Inc.`, `Acme Industrial`), with revenue booked on
  more than one of them.
- **Dates that cannot be trusted.** Cases loaded by a migration all carry the load
  date as `CreatedDate`; the real date is in `Legacy_Created_Date__c` when it was
  migrated at all.
- **Activity after churn.** Tasks and cases logged against accounts that had
  already left.
- **No usage history.** Active users, adoption and last login exist as a current
  snapshot on the account. There is no weekly series.

The data contracts (`scripts/validate_dataset.py`, the dbt tests) assume a clean
churn label, unique company names, and no event after a churn date. Something has
to stand between the org and those contracts.

## Decision

`src/connectors/salesforce.py` splits into `fetch()` (network, credentials) and a
pure `transform()`. Every judgement about messy data lives in `transform()` and is
pinned by `tests/test_salesforce_mapping.py`.

**The churn label.** An account has churned if either:

1. `Customer_Status__c` is `Churned` (or an equivalent), or
2. its most recent closed opportunity, as of the run date, is Closed Lost and
   nothing was won after it.

The churn date is taken from `Churn_Date__c`, else the lost opportunity's close
date, else `Contract_End_Date__c`. A churned account with none of the three is
dropped and counted, because a label without a date cannot enter the survival
model without inventing one.

An open renewal whose close date has passed is **not** churn. It is a pipeline
hygiene problem, and treating it as churn would label every neglected opportunity a
lost customer.

**Everything else is flagged, not fixed:**

| Situation | Handling |
|---|---|
| Duplicate company names | Kept as separate customers. Names suffixed with the account number so `company_name` stays unique. Groups listed in the report. **Merging is the customer's decision**, not the connector's |
| Prospects | Excluded from `customers`; their cases are dropped and counted |
| ARR missing | Latest won opportunity amount; else zero, counted |
| Segment missing or non-standard | Inferred from ARR bands, counted |
| Case date | `Legacy_Created_Date__c`, falling back to `CreatedDate` and counting the fallback |
| Events after churn | Dropped and counted, so the "no event after churn" contract holds by construction rather than by luck |
| Competitor spelled four ways | Normalised through an alias table |
| Usage | One snapshot row per customer in `engagement_snapshots`; the report states one week of history is available |

Each run writes `ingest_report.json` next to the CSVs.

## Consequences

- The dbt models and data contracts run on Salesforce data unchanged. Point
  `CHURNGUARD_DATA_DIR=data/salesforce` at the output.
- **The survival model cannot be trained on this source.** It needs weekly
  history and this org holds one snapshot. Health scoring still runs; trend
  features are absent and say so. This is the D3 outcome in `docs/SITE_SURVEY.md`:
  the deliverable becomes ranking today, not prediction. Recovering history means a
  product-analytics feed, not more CRM fields.
- Rule 1 trusts a hand-set field over the opportunity record. Where the two
  disagree the status field wins and the disagreement is counted under
  `churn_label_source`. A customer whose CSMs do not maintain the field should flip
  that precedence, and the report gives them the numbers to decide.
- The demo org's custom field names are hard-coded in the SOQL. A real customer
  will have their own names; mapping them is a configuration step this ADR does not
  yet build.
