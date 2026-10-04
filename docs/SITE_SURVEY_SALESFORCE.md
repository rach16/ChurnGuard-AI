# Site survey: Salesforce org `churnguard-dev`

**Surveyed:** 2026-10-04 · **Instrument:** [`SITE_SURVEY.md`](SITE_SURVEY.md) v1 · **Phase:** 5.5

> **The org is real; the data in it is not.** `churnguard-dev` is a genuine Salesforce
> Developer Edition org, read through the same API a customer's would be. Its
> records were seeded by this project on 2026-10-04, partly with issues planted on
> purpose. Where a finding below is one of those planted issues, it says so. Those
> findings test the connector; they are not discoveries. Treating this survey as
> evidence about a real customer would be the error ADR-0007 records.

Every figure below comes from `scripts/survey_salesforce.py`, run 2026-10-04 against
the pull committed in `61ea46c` (`data/salesforce/`, `--as-of 2026-10-04`).

## Prospect

A stand-in B2B SaaS company. Salesforce is the only system: 244 accounts, 519
opportunities, 370 cases, 210 tasks. **150 accounts are customers** (94 are
prospects), carrying $25.6M ARR, of which $20.9M is active.

There is no warehouse, no product-analytics feed and no customer-success tool.
Usage exists only as fields on the Account record.

## Disposition: 🔴 Red

**Do not scope a prediction.** Two blocking items are absent, and one of them (D2) is
the red the instrument calls "the one worth declining on".

| Section | Result | Note |
|---|---|---|
| D1 customer dimension | 🟡 | 93% complete on the seeded accounts. 11 of the 150 customers are Salesforce's own sample records |
| D2 weekly telemetry | 🔴 | **One snapshot per account.** No series exists anywhere in the org |
| D3 churn dates | 🟢 | Every churned account is dated, and both sources agree. Too neatly to be real (see below) |
| D4 support | 🟡 | CSAT on 72% of tickets. 26 tickets carry a load date, not a real one |
| D5 interactions | 🟢 | 93% of customers have one. 75% of tasks carry free text |
| D6 interventions | 🔴 | None recorded |
| V1 history | 🔴 | 1 week of usage. Events span 2024-03 to 2026-10, but that cannot substitute |
| V2 events | 🔴 | **33 churn events.** The model needs ~300 |
| I identity | 🔴 | 40 duplicate-name groups. In 7, the same company is both churned and live |
| S security | 🟢 | No contact PII pulled. Free text in cases and tasks still needs review before indexing |
| E environment | 🟡 | No warehouse. Our DuckDB and Athena layer stands in |
| P people | ⚪ | Not surveyable: there are no stakeholders |

## The four findings that change the scope

**D2 and V1: there is no usage history, and the CRM cannot supply one.**
`Active_Users__c`, `Feature_Adoption__c` and `Last_Login_Date__c` are present on
139 of 150 customers (93%), but each is a single current value. The connector writes
one engagement row per customer, and the most any customer has is one week.

That removes the survival model entirely. Eight of its seventeen features are
rolling windows over this table (`SITE_SURVEY.md` D2). It also degrades the health
score in two ways that are easy to miss:

- **The trend factor, 15% of the weight, is constant.** With one row the slope is 0,
  so every account reads "stable" and the factor adds nothing. Nothing in the
  output says so.
- **Adoption is counted twice.** The connector builds `engagement_score` as half
  seat utilisation, half adoption, and the scorer then weights adoption separately.
  Adoption's effective weight is 0.375, not the 0.20 in `RISK_WEIGHTS`.

*Scope change:* this is a ranking engagement, not a prediction one. The
instrumentation ask is a weekly usage export from the product itself. More CRM
fields would not help, because a CRM stores state, not history.

**V2: 33 churn events is a tenth of the floor.** The model's design matrix is 30
columns wide. At 33 events that is about one event per feature against a
conventional floor of ten. A model fitted here would memorise the 33 accounts.

*Scope change:* the health score is the deliverable. It is a weighted sum with no
fitting, so it needs no events. The 33 become the **test set**: do they rank near
the top? That is item 0.2b, and with so few events it will need confidence
intervals to mean anything.

**I: duplicates are worse than the planted ones.** The seeder planted 5 duplicate
groups (`ACC-100xx`). The connector found **40**: the 5 planted, plus 35 in the
churn-history series (`ACC-200xx`) that are on no planted list. They look like name
collisions from generating random company names, which is how a real CRM gets
them too: two reps creating the same company months apart.

26 of the 40 groups disagree on status. **In 7, one record is Churned and another
is Active or At Risk.** "Silver Health" has five records: Prospect, At Risk, Active,
Churned and blank. This is the I2 failure: one company is labelled both lost and
retained, and the connector keeps both, as ADR-0011 requires. The churn label is only
as trustworthy as the account list underneath it.

*Scope change:* a deduplication decision from the customer before any number is
reported per company. The connector already lists every group in
`ingest_report.json`. Merging stays the customer's call.

**D6: no recorded interventions.** `success_stories.csv` is empty, because
Salesforce has no record of which play was tried on which account and whether it
worked. `/customer/{id}/plays` will return nothing.

*Scope change:* recommendations are cut. Proposing one picklist and one dated field
on the Account ("intervention", "intervention date") would make them buildable once
a few quarters of outcomes accumulate.

## Smaller findings

| Finding | Measured | Consequence |
|---|---|---|
| **11 Salesforce sample accounts written as customers** (Edge Communications, GenePoint, United Oil & Gas ×3, …) | 11 of 150 | They have no usage, so no engagement row and ARR inferred or zero. The connector should exclude them, which is a fix for 1.5b, not done here |
| **26 tickets dated 2026-09-27** (the org's creation date), on 9 of those sample accounts | 26 of 233 | They fall inside the 30-day ticket window and inflate the support factor for 9 accounts. Goes away once the sample accounts are excluded |
| **Prospects with support cases** | 68 of 92 (74%) | Prospects do not raise support cases. Either the status is wrong or these are trial accounts. 5 also have a Closed Won opportunity. All 134 of their cases are dropped |
| `Customer_Status__c` blank on customers | 21 of 150 | The churn rule falls back to opportunities for these. None churned by that route |
| Loss reason on Closed Lost opportunities | 25 of 36 (69%) | Churn category falls back to the account field, else "Other" (8 of 33) |
| Open opportunities past their close date | 18 | Treated as pipeline hygiene, not churn (ADR-0011). Planted, partly |
| Competitor spelled several ways | 6 raw spellings → 4 names | Normalised by the alias table. Planted |

## Why D3 passing proves little

D3 is the item the instrument says fails most often. Here it passes cleanly: all 33
churned accounts carry both a status of Churned and a lost renewal. On all 28 that
have both a `Churn_Date__c` and a lost opportunity, **the two dates are identical**
(median gap 0 days, none non-zero).

Real CRMs do not agree like that. The worked example in `SITE_SURVEY_EXAMPLE.md`
assumes a six-week median gap between the commercial event and the status flip. A
gap of exactly zero on every account means the seeder wrote one date into both
places. So ADR-0011's reconciliation rule (status first, opportunity as fallback,
disagreements counted) has **never been exercised on disagreeing data**. The unit
tests cover it. This org does not.

## Baseline to beat

The org contains its own heuristic: **26 customers are marked `At Risk`** by hand.
That is the P3 baseline, the number the health score has to beat before it earns a
place in a CSM's week.

It cannot be backtested here, for the same reason as D2. The flag is a current value
with no history, so there is no way to ask what it said six months before an
account churned. Comparing it with the score *today* is possible and is part of
0.2b.

## Where the output goes

E3 has only one answer here, because Salesforce is the only system there is. **The
deliverable is a score and a queue written back onto the Account** (item 9.2), as
field updates. The org has 1 MB of 5 MB data storage left (`sf org list limits`,
2026-10-04), and field updates do not consume it.

## Recommended engagement

| Phase | Content | Prerequisite |
|---|---|---|
| **0: Hygiene** | Deduplication decision on 40 groups; exclude the sample accounts; resolve the 68 prospects with cases | Customer decision |
| **1: Rank** | Health score from the snapshot, exposure by ARR, write-back to the Account | Phase 0 |
| **2: Explain** | Evidence over case and task text (75% of tasks carry text) | Phase 1 |
| **3: Instrument** | Weekly usage export; intervention picklist and date | Customer engineering |
| **4: Predict** | Survival model | ~2 years of weekly usage and ~300 churn events |

Phase 4 is listed so the gap is explicit, not because it is near. At this org's
churn rate it is years away, and a real engagement would say so in the first week
rather than the last.

## Order of work, stated plainly

The instrument says to run this survey before scoping, and before writing code.
Here the connector was written first and the survey second. The findings above
would have reached the same scope either way, but the D2 red would have been known
before a line of the connector existed, and on a real engagement that would have
been the cheaper order.
