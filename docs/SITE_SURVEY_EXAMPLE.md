# Site survey — worked example

> **This prospect is invented.** "Meridian Logistics Software" does not exist and no
> real customer environment has been surveyed. It is here to show what the
> instrument produces and how a finding turns into a scope change. Treating a
> constructed example as evidence would be the same error as the retracted 94.7%
> claim (ADR-0007).

**Surveyed:** hypothetical, 2026-09-01 · **Instrument:** [`SITE_SURVEY.md`](SITE_SURVEY.md) v1

## Prospect

B2B logistics SaaS. ~1,400 accounts, $48M ARR, mostly mid-market with a long SMB
tail. Snowflake warehouse, dbt already in production and owned by a four-person
data team. CS team of nine, using Gainsight for workflow and Salesforce for the
commercial record.

**Presenting problem, in their words:** *"We find out an account is leaving when
they tell us, and by then it's a save-or-lose conversation."*

## Disposition: 🟡 Amber

Blocking items are present. Two quality items are missing and one blocking item
is present but weaker than it first appeared. **Scope a reduced system.**

| Section | Result | Note |
|---|---|---|
| D1 customer dimension | 🟢 | Salesforce, clean keys |
| D2 weekly telemetry | 🟡 | Present, but reconstructed — see below |
| D3 churn dates | 🟡 | Two competing definitions, neither authoritative |
| D4 support | 🟢 | Zendesk, CSAT at 22% response |
| D5 interactions | 🟢 | Gainsight timeline, free text available |
| D6 interventions | 🔴 | Not recorded in any queryable form |
| V1 history | 🟡 | 26 months usable, not 42 |
| V2 events | 🟢 | ~310/yr |
| I identity | 🟢 | Salesforce ID is canonical everywhere |
| S security | 🟢 | Hosted LLM approved, DPA in place |
| E environment | 🟢 | Snowflake + dbt |
| P people | 🟡 | No single owner for the number |

## The three findings that changed the scope

**D2 — the telemetry is reconstructed, and retention is 24 months.** Their product
analytics stack keeps raw events for two years. The weekly series can be rebuilt
from it, but only that far back, and the rebuild has to be re-run rather than
queried. This is the V1 constraint in disguise: 26 usable months after the 12-week
rolling window and 13-week label horizon consume both ends, which leaves roughly
five walk-forward folds. Enough to fit and to detect drift, not enough to be
confident about a seasonal effect.

*Scope change:* the backtest reports fewer folds and the report says so. Budget two
weeks for the reconstruction job, and treat extending event retention as a
customer-side prerequisite for the next refit.

**D3 — "churned" has two definitions and nobody owns the difference.** Salesforce
records a renewal opportunity closing as Lost. Gainsight flips a health status to
Churned. On the accounts they spot-checked in the meeting, the two dates differed
by a median of about six weeks, and roughly one in nine accounts had one without
the other.

This is exactly the D3 failure the instrument warns about, and it is not a data
problem, it is an unmade decision. Someone has to choose which event the model is
predicting.

*Scope change:* a definition workshop in week one, before any modelling. The
chosen definition goes in an ADR. At four-week periods a six-week disagreement is
material — it moves an account by one to two periods.

**D6 — no recorded interventions.** CS actions live in Gainsight timeline notes as
free text. What was done is often recoverable by reading; whether it worked is not
recorded anywhere, and unsuccessful plays are systematically less likely to be
written up at all.

*Scope change:* **the playbook is cut from phase one.** Selling "what should I do"
against unrecorded outcomes would produce exactly the fluent, unevidenced advice
this system exists to avoid. Replaced with a lightweight outcome-logging change in
Gainsight — one picklist and one dated field — which makes the playbook buildable
in about two quarters and is worth proposing on its own merits.

## Baseline to beat

Their existing heuristic is a spreadsheet: accounts with no login in 30 days and an
open severity-1 ticket. The CS lead estimates it catches "maybe half" of churners.

**Measure it before building anything.** If it recovers 50% of churn at reasonable
precision, a model that ranks slightly better is not worth a quarter of work, and
the honest recommendation might be to keep the spreadsheet and build only the
exposure view on top of it. That view is cheap, and it is the part they do not have.

## Where the output goes

E3 answered decisively: the CS team lives in Gainsight and will not adopt a second
dashboard. **The integration is a scored field and a queue written back into
Gainsight**, not the console.

The console remains valuable for the sponsor and for debugging, but it is not the
delivery surface, and scoping it as one would have been the expensive kind of
wrong. Worth asking in the first meeting rather than the fourth.

## Recommended engagement

| Phase | Content | Prerequisite |
|---|---|---|
| **0 — Definition** | Churn-event workshop, ADR, telemetry reconstruction job | — |
| **1 — Detect** | Warehouse models, survival model, exposure, Gainsight writeback | Phase 0 |
| **2 — Explain** | Evidence layer over Gainsight timeline text | Phase 1 |
| **3 — Recommend** | Playbook | ~2 quarters of logged outcomes |

Phase 1 is contingent on the phase 0 definition holding. If the workshop concludes
the two churn definitions cannot be reconciled, the model predicts the commercial
event (Lost opportunity), the interval widens, and the report says which event was
chosen and why.
