# 0008 — Predict a churn date with survival analysis, not a churn class

**Status:** Accepted · **Date:** 2026-08-31

## Context

The product question is "when will this customer churn", answered as a date with
a confidence interval. The system had no model at all: `risk_score` was a
weighted sum of five signals, and `days_until_churn` was `np.interp` mapping that
score onto a days axis. Nothing in it had ever been fitted to observed churn
timing, so it could not distinguish this quarter from next.

Two framings were available. A multi-class classifier over horizon buckets
(this / next / later quarter), or survival analysis predicting time to event.

## Decision

Survival analysis, in discrete time.

64.5% of customers are right-censored — active as of the last observation. A
classifier must label them, and the only available label is negative, which
asserts they will never churn. The data does not say that. It says they had not
churned *yet*. Training on that assertion inflates apparent accuracy and destroys
calibration, and it is the single most common way churn projects fail quietly.

Discrete time specifically, because the data is already person-period: 15,840
customer-weeks. One row per customer per week, labelled with whether churn
followed in the next period, fitted with an ordinary binary classifier whose
output is a hazard. Chaining hazards gives a survival curve; its median is the
predicted date and its quartiles are the interval.

That keeps the model explainable and debuggable while handling censoring
correctly, and it produces a full curve rather than a bucket — so the horizon
buckets remain available for display without being baked into the model.

## Consequences

Features must be computed strictly as of each observation date. The existing
`health_scoring.py` uses each customer's latest snapshot, which for a churned
customer is the week before they left; training on that would learn "customers
about to churn look terrible" and score near-perfectly while being useless. The
dbt feature model must be windowed, and validation must walk forward by quarter
rather than splitting randomly.

The heuristic score stays in place until the model replaces it, so the dashboard
keeps working. Two scoring paths existing at once is a temporary state, and one
of them is scheduled for deletion.

Reporting needs two numbers, not one: concordance for ranking and calibration for
the dates. A model that ranks correctly but predicts three months early is not
usable by a team planning a quarter.

## Alternatives considered

**Multi-class horizon classifier.** Simpler, but mishandles censoring and cannot
express a confidence interval — the thing that was asked for.

**Cox proportional hazards.** Assumes hazards stay proportional over time.
Engagement collapse is precisely a case where they do not.

**Parametric AFT (Weibull, log-normal).** Assumes a distributional form for
survival times that nothing in the data justifies.
