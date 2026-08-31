# Architecture

## What this system is

Given everything a SaaS platform knows about a customer, predict **when** that
customer will churn — a date, with a confidence interval — and explain why.

That sentence determines the whole architecture, and it is worth being precise
about what it rules out:

- It is **not** a document-QA system. Retrieval explains a prediction; it does
  not produce one.
- It is **not** a churn classifier. "Will they churn" is the wrong question when
  the answer for almost everyone is "eventually". The question is *when*.
- It is **not** an agent platform. Agents compose an explanation from the
  prediction and the evidence. They are the last layer, not the centre.

The previous architecture had retrieval at the centre and a hand-tuned risk score
beside it. That is why aggregate questions had nowhere sensible to go, why the
knowledge graph could rot unnoticed, and why the headline metric measured
retrieval rather than prediction.

## Layers, and what each is authoritative for

```
                    ┌────────────────────────────────────────┐
   presentation     │  API / UI — customers ranked by         │
                    │  predicted churn date and interval      │
                    └───────────────┬────────────────────────┘
                                    │
                    ┌───────────────▼────────────────────────┐
   explanation      │  Agents — compose narrative + action    │
                    │  RAG — evidence about THIS customer     │
                    └───────────────┬────────────────────────┘
                                    │
                    ┌───────────────▼────────────────────────┐
   inference        │  Survival model — hazard per period,    │
                    │  survival curve, date + interval        │
                    └───────────────┬────────────────────────┘
                                    │
                    ┌───────────────▼────────────────────────┐
   feature store    │  dbt gold — point-in-time features      │
                    │  and labels, one row per customer-week  │
                    └───────────────┬────────────────────────┘
                                    │
                    ┌───────────────▼────────────────────────┐
   system of record │  dbt bronze/silver — customers, events  │
                    └────────────────────────────────────────┘
```

**The warehouse is authoritative for every number.** Counts, sums, rates, scores,
predictions. If a question has a numeric answer, SQL answers it.

**The vector store is authoritative for narrative.** What was said, by whom, in
what tone. It answers "why did they leave", never "how many left".

That single split resolves the ambiguity that produced most of this project's
defects. "Which segment has the highest churn rate" was never a retrieval
question, and no amount of retrieval tuning would have made it one.

## The prediction

### Why survival analysis

64.5% of customers are **right-censored** — still active as of the last
observation. A classifier trained on "churned yes/no" labels them negative, which
asserts something the data does not say: that they will never churn. That single
mistake inflates apparent accuracy and destroys calibration.

Survival analysis is built for exactly this: some subjects have experienced the
event, others have not *yet*, and both carry information.

### Discrete-time survival

The data is already in person-period form — 15,840 customer-weeks. That makes
discrete-time survival the natural fit, and it is unusually simple:

1. One row per customer per week, features computed **as of that week**
2. Label: did this customer churn in the period immediately following?
3. Fit any binary classifier to that — the output is a **hazard**, the
   probability of churning in each period given survival to it
4. Chain hazards into a survival curve per customer
5. Read the date off the curve: median survival is the point estimate, the 25th
   and 75th percentiles give the interval

The model underneath is an ordinary classifier, so it stays explainable and
debuggable, while the framing handles censoring correctly.

Alternatives considered: Cox proportional hazards (assumes hazards stay
proportional over time, which engagement collapse violates), and parametric AFT
(assumes a distributional form we have no reason to believe).

### Point-in-time features, and the trap

Features must be computed **as of the observation date**, using nothing later.

This is where churn projects usually leak. The current `health_scoring.py`
computes features from each customer's *latest* snapshot — fine for a dashboard,
fatal for training, because for a churned customer the latest snapshot is the one
immediately before they left. A model trained on that learns "customers who
churned looked terrible right before churning", scores ~0.99, and is useless.

The dbt feature model must therefore be windowed: at week *t*, use only weeks
≤ *t*. Available label volume:

| Horizon | Positive labels |
|---|---|
| ≤ 90 days | 848 |
| ≤ 180 days | 1,710 |
| ≤ 270 days | 2,456 |

### Validation

Backtest by time, never by random split. Train on everything up to quarter *Q*,
predict *Q+1*, walk forward. A random split leaks the future into the past through
customers appearing on both sides.

Report concordance (C-index) for ranking and calibration for the dates. A model
that ranks well but predicts dates three months early is not usable by a CS team
planning a quarter.

## What the existing components become

| Component | Role now | Change |
|---|---|---|
| dbt warehouse | System of record **and feature store** | Add point-in-time feature and label models |
| `health_scoring.py` | Dashboard heuristic | Superseded by the model; keep until it is replaced |
| Hybrid retrieval | Evidence for a specific customer | Keep, scope to per-customer explanation |
| Multi-agent system | Compose explanation and action | Demote — one agent, not two teams |
| Knowledge graph | — | Delete. Nothing rebuilt it, agents consumed it blind, and the model covers what it was for |
| 5 retrieval strategies | — | Keep hybrid, drop the rest once the benchmark confirms |

## What is deliberately not here

- **Multi-tenancy.** One tenant. Real deployment needs isolation in schema,
  vector store and API.
- **Real data ingestion.** All data is synthetic. A customer needs connectors.
- **Real-time.** Weekly batch. Nothing here needs streaming.
- **Deep learning.** 200 customers and 15,840 rows. Gradient boosting is correct
  and a neural network would be a costume.

## The consequence for planning

The phase plan up to this point was a remediation backlog: it fixed what was
broken without asking what the system should be. Judged against this
architecture, deployment and retrieval tuning were never the critical path.

**The critical path is: point-in-time features → survival model → predictions
surfaced.** Everything else supports that or waits.
