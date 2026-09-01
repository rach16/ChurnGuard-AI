# ChurnGuard — product requirements

**v1 · 2026-09-01 · Status: describes the built system and the first deployable increment**

Companion to [`SITE_SURVEY.md`](SITE_SURVEY.md) (what must be true of the
environment) and [`ARCHITECTURE.md`](ARCHITECTURE.md) (how it is put together).
This document says what the product does, who for, what is deliberately excluded,
and how we will know it worked.

Written against the system as it actually is. Where something is not built, it says
so rather than describing it in the present tense.

---

## 1. Problem

A customer success team finds out an account is leaving when the account tells
them. By then the conversation is a save-or-lose negotiation rather than a
retention action, and the levers that would have worked three months earlier are
gone.

Existing tooling in this category fails in three specific ways, each of which this
product is shaped to avoid:

1. **It says who, not what.** A ranked list of at-risk accounts is a report. The
   team already knows most of the names on it.
2. **It cannot be argued with.** A score with no attributable evidence is either
   believed uncritically or ignored, and both are bad.
3. **It answers a question nobody asked.** "Will this account churn, yes or no" is
   not the decision. The decision is where to spend a limited number of CSM hours
   this quarter.

## 2. Users

| User | Needs | Success looks like |
|---|---|---|
| **CS manager** (primary) | A prioritised weekly queue and a defensible reason for each entry | Opens it Monday, works the top of it, does not maintain a parallel spreadsheet |
| **CSM** | Why this account, and what has worked on ones like it | Walks into a call with evidence, not a score |
| **VP CS / sponsor** (secondary) | What the risk is worth and whether it is moving | Uses the exposure figure in a board deck without a caveat they cannot defend |
| **Data team** (gatekeeper) | To trust the numbers and own the pipeline | Reads the dbt models and does not need to rebuild them |

The gatekeeper is easy to forget and can end an engagement. Every number is
computed in the warehouse and queryable in SQL for exactly this reason.

## 3. What it does

### 3.1 Detect — built

Ranks active accounts by likelihood of churning within one quarter, as a **band and
a lift against the book average** rather than a date or a bare percentage.

The date was attempted, measured and retracted (ADR-0009): the model underpredicts
hazard by 4.36x and 95% of predicted dates landed late by a median of 222 days,
for structural reasons that no calibration fixes. The lift survives the residual
level bias because both sides shift together.

*Measured:* AUC 0.877 held out by customer, 0.665 held out by time. Top band
churns at 27.8% against an 8.7% baseline.

### 3.2 Value — built

Converts likelihood into money: expected loss is `P(churn within a quarter) x ARR`,
aggregated by band, with the concentration made explicit.

This is what makes the queue actionable rather than merely ordered. Likelihood
ranking and exposure ranking produce different lists, and the second is the one a
team with nine CSMs and 1,400 accounts needs. See [`COST_OF_INACTION.md`](COST_OF_INACTION.md).

### 3.3 Explain — built

For any prediction, quoted passages **from that account's own record**, selected by
key and ranked with BM25 within the account.

Not a vector search across the corpus. Each account has around five documents, so
the problem is finding the right passage inside a known account, and selecting by
key makes returning a different account's story structurally impossible — which was
the original failure of the general Q&A path.

### 3.4 Recommend — built, evidence-limited

What has worked on comparable accounts, computed from recorded outcomes rather than
generated. Every play carries its case count, how many were the same segment, and
the measured movement. Below three comparable cases it returns nothing.

*Constraint:* the underlying records are successes only, so effect sizes are
survivorship-biased upward. Stated at every boundary that returns them.

### 3.5 Ask — built, degrades cleanly

Hybrid retrieval (BM25 + dense, reciprocal rank fusion) over the document corpus,
for questions the structured views do not answer.

*Measured:* single-entity hit rate 0.971, against 0.735 for dense-only. Without an
LLM provider configured, this returns 503 with a specific reason while every
CSV-backed endpoint continues to serve.

## 4. Explicitly out of scope

Listed because each was considered and rejected, not overlooked.

| Excluded | Why |
|---|---|
| **A predicted churn date** | Measured and retracted. ADR-0009. Revisit only with materially more signal, not with more tuning |
| **Automated outreach** | A model that both decides and acts removes the judgement that makes it safe. It recommends; a person sends |
| **Renewal-date weighting** | Real gap, not built. `contract_end_date` exists and is unused, so a quarterly horizon is applied uniformly |
| **Multi-tenancy, SSO, auth** | Single-tenant deployment assumed. Not a demo shortcut, a scope boundary |
| **Real-time scoring** | Weekly matches the data's natural period. Faster is presentation, not signal |
| **Distributed compute** | 2 MB of data. Knowing when not to reach for Spark is part of the product |
| **Causal claims about interventions** | Needs a holdout. Everything recovery-related is labelled a sensitivity |

## 5. Success criteria

Ordered by how hard they are to game.

| # | Criterion | Threshold | How measured | Status |
|---|---|---|---|---|
| **S1** | Beats the customer's existing heuristic | Precision at the queue length they actually work | Backtest both on their history | **Not yet run — no customer** |
| **S2** | Ranking holds out of time | Held-out-by-time AUC ≥ 0.70 | Walk-forward by quarter | 0.665 — **below threshold**, reported not smoothed |
| **S3** | Bands separate outcomes monotonically | Top band ≥ 2x baseline | Backtest folds | 27.8% vs 8.7% (Q4); 1.59x vs 0.76x (Q1) |
| **S4** | Every prediction carries evidence from its own account | 100% | Structural — `CustomerEvidence` indexes by `customer_id`, so a cross-account result is unreachable | True by construction; **no automated test covers it** |
| **S5** | Numbers agree between SQL and the API | 200/200 within 0.1 | `tests/test_warehouse_parity.py` | Met |
| **S6** | Degrades rather than fails | Core endpoints serve with no LLM | Smoke test over the 16 registered routes | Met |
| **S7** | The queue is actually worked | Sustained weekly use, no parallel spreadsheet | Usage, 8 weeks post-deploy | **Not measurable without a deployment** |

S1 and S7 are the ones that matter commercially and neither can be claimed today.
S2 is below its own threshold and is stated that way in `STATUS.md`.

## 6. First deployable increment

Assumes a 🟡 amber survey result — blocking data present, playbook not yet
supportable — which is the common case.

| Milestone | Content | Exit criterion |
|---|---|---|
| **M0 · Definition** | Churn-event workshop; ADR fixing the definition; source mapping to the six inputs | One dated churn event per churned account, agreed by CS and RevOps |
| **M1 · Warehouse** | Port bronze/silver/gold to the customer's warehouse; data contracts | Contracts pass on customer data; row counts reconciled to source |
| **M2 · Model** | Point-in-time features, fit, walk-forward backtest | Backtest report delivered including where it underperforms |
| **M3 · Value** | Exposure over their ARR; cost-of-inaction memo | Sponsor accepts the figure with its stated bounds |
| **M4 · Delivery** | Writeback to the tool the CS team already uses | Queue visible where they work; no second dashboard |
| **M5 · Measure** | Baseline the existing heuristic; compare | S1 answered either way |

M5 can conclude the model does not beat the heuristic. That is a real outcome and
the engagement should be priced so that finding it is a success rather than a
failure to be argued away.

**Not in the first increment:** the playbook (needs D6), renewal weighting, the
console as a primary surface.

## 7. Risks

| Risk | Likelihood | Consequence | Mitigation |
|---|---|---|---|
| Churn definition unresolvable | Medium | Model predicts the wrong event | M0 exits on it; ADR records the choice |
| Too few events (V2 < ~300) | Medium | Cannot fit; 9.5 events/feature is already the floor | Survey catches it; fall back to the health score and say so |
| Telemetry retention < 2 years | High | Few walk-forward folds; drift undetectable | Report fold count; propose retention change |
| Hazard non-stationarity | **Observed here** | Absolute probabilities understate by ~1.8x | Report lift and bands, not percentages; re-measure per refit |
| Adopted then abandoned | Medium | Silent failure, worst outcome | M4 puts it where they work; S7 measures it |
| Sponsor quotes a number it cannot bear | Medium | Credibility loss on first miss | Every total ships with its bound and basis string |

## 8. Dependencies on the survey

| PRD element | Survey item | If absent |
|---|---|---|
| §3.1 Detect | D2, D3, V1, V2 | No predictive layer; health score only |
| §3.2 Value | D1 `arr` | No exposure figure |
| §3.3 Explain | D5 free text | Predictions unexplained |
| §3.4 Recommend | D6 | Cut from scope; propose outcome logging |
| §6 M4 | E3 | Ship the console and expect low adoption |

## 9. Open questions

- **Does the exposure view alone justify an engagement?** It is cheap, needs only
  ARR and a risk rate, and is the piece customers most consistently lack. It may be
  a better wedge than the model.
- **What is the right horizon?** A quarter is asserted, matching a QBR cycle. It has
  not been tested against 6 or 13 weeks.
- **Should the queue be capped at the team's actual capacity?** A list of 40 for a
  team that can work 12 is a list of 12 and 28 accounts of guilt.
