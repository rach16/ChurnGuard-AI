# Site survey

**Instrument, v1 · 2026-09-01**

What has to be true in a customer's environment before ChurnGuard produces anything
worth acting on. Run this before scoping, not after.

Every question below earns its place because a named part of the system fails or
degrades without the answer. That is the difference between a survey and a
questionnaire: each item cites what breaks, so a "no" has a consequence you can
price rather than a box left unticked.

The three questions that most often end the conversation are **D3** (churn dates),
**D6** (recorded interventions) and **V2** (event count). Ask them in the first
meeting. Everything else is negotiable or buildable; those three are either present
in the customer's systems or they are not, and no amount of engineering conjures
them.

---

## A. Disposition

Score each section, then take the worst.

| | Meaning | What to do |
|---|---|---|
| 🟢 **Green** | Every blocking item present | Scope the full system |
| 🟡 **Amber** | Blocking items present, quality items missing | Scope a reduced system; name what is cut |
| 🔴 **Red** | A blocking item absent | Do not scope a prediction. Offer the instrumentation engagement instead |

A red on D3 or V2 means **the predictive layer cannot be built at all**. The
retrieval, evidence and reporting layers still can, and saying so is more useful
than declining the work. See §H.

---

## D. Data availability

The system reads six inputs. Bronze models in `warehouse/models/bronze/` define
the contract; `scripts/validate_dataset.py` enforces 28 assertions over it.

### D1 — Customer dimension · **blocking**

One row per account, with a stable key.

| Field | Used by | Consequence if absent |
|---|---|---|
| `customer_id` | every join | Nothing works |
| `segment` | model feature, play matching | Loses the size tier; `plays_for` cannot prefer same-segment outcomes |
| `industry` | model feature | One of two categoricals; degrades the fit |
| `arr` | exposure | **No cost-of-inaction figure at all** |
| `contract_start_date`, `tenure_months` | `tenure_weeks` feature | Loses the strongest non-engagement feature |
| `contract_end_date` | not yet used | Would fix the uniform-horizon gap in `COST_OF_INACTION.md` |

Deliberately **not** required: `seats`. ARR and seats are near-unique per account
(193 and 96 distinct values across 200 customers) and constant over time, so the
model memorises them as identity — it scored 0.9997 in-sample against 0.666 held
out. ARR is used for money, never as a feature.

### D2 — Weekly engagement telemetry · **blocking**

`active_users`, `sessions`, `feature_adoption_rate`, `engagement_score`, per
account per week.

Eight of the model's seventeen numeric features derive from this one table, and
the single strongest feature in the whole set is `engagement_mean_4w` at AUC
0.769. Without it there is no model.

**Weekly is the unit, and it matters.** The model is discrete-time over
customer-weeks and aggregates to four-week periods. Monthly snapshots halve the
row count and destroy the 4- and 12-week rolling windows. Daily is fine and gets
downsampled.

Ask specifically: *is this a snapshot table, or is it reconstructed from an event
log?* A reconstructed series is usually only as long as log retention, which is
the most common cause of failing V1.

### D3 — Churn events with dates · **blocking, most often absent**

`is_churned` and `churn_date`.

A boolean is not enough. Survival analysis needs to know *when*, because that is
what places an account in a period, and right-censoring — an account still active
at the cutoff — has to be distinguishable from an account that never churned.
64.5% of this dataset is censored.

**This is the question that fails most often.** Most CRMs record an opportunity
closing as lost, or a subscription flipping to inactive, and those dates can be
months apart and neither may be the date the customer stopped using the product.
Ask which system is authoritative, and whether anyone has ever reconciled them.

If churn dates exist only as "the month the contract lapsed", say so in the
scoping document. It is workable at four-week periods, and it is a different
promise from a dated prediction.

### D4 — Support tickets · quality

`created_date`, `severity`, `csat_score`. Feeds four features
(`tickets_30d`, `tickets_90d`, `csat_mean_90d`, `severe_tickets_to_date`) worth
30% of the health score's weight.

CSAT is usually sparse. That is tolerable — it becomes NaN and the model handles
it — but ask what the response rate is, because a 5% response rate makes
`csat_mean_90d` a measure of who answers surveys.

### D5 — Interaction history · quality

`interaction_date` at minimum. Produces `days_since_last_interaction`, and its
"never contacted" case is carried as a separate flag rather than a sentinel
because 9999 days distorts every split that touches it.

Free-text content is what the evidence layer quotes. Without it, predictions are
unexplained: `CustomerEvidence` returns nothing and `/customer/{id}/evidence`
is empty.

### D6 — Recorded interventions with outcomes · **blocking for the playbook**

Which play was applied to which account, when, and what happened after.

**Almost no customer has this**, and it is worth knowing before you promise the
"what should I do" half of the product. This dataset has 60 such records; both
`/customer/{id}/plays` and the recoverable figure in `/book/exposure` come
entirely from them.

Two follow-ups that matter more than the first answer:

1. **Are failures recorded too?** If only successes were written down, the
   recovery estimate is survivorship-biased and can only ever be an upper bound.
   That is the state of this repo, stated in `COST_OF_INACTION.md`.
2. **Is the intervention dated?** Undated, you cannot establish it preceded the
   outcome, and the whole thing collapses into correlation.

If D6 is absent, the honest scope is detect-and-explain, with the playbook as a
phase-two deliverable contingent on the customer starting to record outcomes.
Proposing that instrumentation is often the more valuable engagement.

---

## V. Volume

Thresholds derived from this build, not from a textbook. Re-derive them if the
feature set changes.

### V1 — History depth · **blocking**

**Two years of weekly observations, minimum.** This model was fitted on
2022-12-19 → 2026-06-17 (3.5 years, 15,711 customer-weeks over 200 accounts).

The binding constraint is not the row count, it is that 12-week rolling features
plus a 13-week label horizon consume the first and last quarter of any window.
One year of history yields roughly two usable quarters of walk-forward folds,
which is not enough to detect the non-stationarity that this dataset showed.

### V2 — Event count · **blocking**

**~300 churn events, or the feature set must shrink.**

The encoded design matrix is 30 columns wide (17 numeric, plus 10 industries and 3
segments one-hot). At 284 events that is **9.5 events per feature** — right at the
conventional floor of ten, and the reason in-sample AUC (0.996) and held-out AUC
(0.877) diverge as far as they do.

A customer with 40 churn events cannot support this model. They can support a
health score, which is a weighted sum with no fitting and needs no events at all.
Say which one you are selling.

### V3 — Account count · quality

200 accounts here. The count matters less than V2, but very few large accounts
plus a long tail makes segment-level play matching thin: `MIN_CASES = 3` withholds
a recommendation below three comparable cases, so a narrow book returns empty
playbooks rather than bad ones.

---

## I. Identity and joins

- **I1.** Is there one account key across telemetry, CRM, and support? Where it is
  reconstructed by name matching, expect the failure this repo already had: 157
  companies present in documents and in no data file, and 89 with conflicting
  segments between sources.
- **I2.** Do accounts merge, split, or get renamed? A merged account looks like a
  churn event and a new customer, and will be labelled as both.
- **I3.** Is the hierarchy account, or contract, or subsidiary? Churn at one level
  is expansion at another.

---

## S. Security and procurement

- **S1. Can customer data reach a hosted LLM?** If not, set `LLM_PROVIDER` and
  `EMBEDDING_PROVIDER` to a local model. `/health` reports `data_leaves_host`, and
  embeddings deliberately do not fall back to a hosted provider. This is
  configuration, not a code change — the answer here should not affect the
  estimate.
- **S2.** Is a DPA in place, and does PII need redacting before indexing? The
  narrative corpus is where names and free text live.
- **S3.** Where does the warehouse run, and who may query it? Gold tables carry
  per-account risk, which is commercially sensitive internally as well as
  externally.
- **S4.** Is there a model-governance review? A retention model that ranks accounts
  by predicted churn is a decision system, and some organisations treat it as one.

---

## E. Environment

- **E1.** Warehouse in use (Snowflake, BigQuery, Redshift, Databricks)? The dbt
  models are DuckDB-flavoured; a port is real work, and mostly mechanical.
- **E2.** Is dbt already deployed, and by whom? If the customer's data team owns
  it, the silver and gold layers should be theirs and this becomes an integration.
- **E3.** How does the output get used — a UI, a CRM field, a queue in the tool the
  CS team already lives in? **Ask early.** A dashboard nobody opens is the standard
  failure mode of this category, and writing a score back into the CRM is often the
  higher-value integration and the cheaper one.
- **E4.** Refresh cadence expected? Weekly matches the data's natural period.
  Anything faster is presentation, not new signal.

---

## P. People

- **P1.** Who acts on a prediction, and how many accounts can they actually work in
  a week? That number sets the queue length and makes exposure ranking matter more
  than likelihood ranking.
- **P2.** Who owns the number when it is wrong? An unowned model is decommissioned
  by neglect.
- **P3.** What does the team do today? If it is a spreadsheet with a good heuristic
  in it, that heuristic is a baseline you must beat and should be measured before
  anything is built.
- **P4.** Is there an executive sponsor with a retention target? `COST_OF_INACTION.md`
  is written for that person.

---

## H. When the answer is red

A red on D3, D6 or V2 is not the end of the engagement, and treating it as one
wastes the survey.

| Red on | Still deliverable | Honest framing |
|---|---|---|
| D3 churn dates | Health score, evidence, exposure using a heuristic rate | "Ranking today, prediction once churn is dated" |
| V2 event count | Health score, retrieval, reporting | "Not enough events to fit; here is what to instrument" |
| D6 interventions | Detection and explanation | "Detect now, recommend once outcomes are recorded" |
| D2 weekly telemetry | Very little | The one red worth declining on |

The instrumentation engagement — defining the churn event, dating it, recording
interventions — is frequently worth more than the model, and it is the only path
to the model. Price it as its own phase.

---

## Filled example

A worked example against a **hypothetical** prospect is in
[`SITE_SURVEY_EXAMPLE.md`](SITE_SURVEY_EXAMPLE.md). It is clearly labelled as
constructed. No real customer environment has been surveyed, and inventing one
would be the same class of error as the retracted 94.7% claim.
