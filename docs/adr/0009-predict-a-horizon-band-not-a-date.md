# 0009 — Predict a likelihood band over a horizon, not a date

**Status:** Accepted · **Date:** 2026-08-31 · **Supersedes part of** [ADR-0008](0008-predict-churn-date-with-survival-analysis.md)

## Context

ADR-0008 committed to predicting a churn date with a confidence interval, read off
the median of a survival curve. The walk-forward backtest built in 7.3 shows that
is not supportable on this data.

Uncalibrated, the model underpredicts hazard by 4.36x and 95% of predicted dates
land late, by a median of 222 days. Isotonic calibration halves the miscalibration
to 1.80x, improves Brier, and makes the dates *worse* — 287 days — while dropping
concordance from 0.703 to 0.662.

The cause is structural rather than a tuning failure. On the rows that do churn in
the next period the calibrated hazard is 0.0395, against 0.0205 for everyone else.
That is genuine separation, but only 1.93x. At a hazard of 0.0395 the survival
curve does not reach 0.5 until 17 periods — 482 days. **The median of a survival
curve is far out whenever the hazard is small, however well the model ranks.** No
amount of calibration moves it, because it is a property of the arithmetic and not
of the fit.

## Decision

Predict **P(churn within one quarter)**, expressed as a likelihood band and a lift
against the book average, rather than a date.

Calibration is enabled for this output, because it is the probability that
calibration was meant to fix and the date it harmed is no longer produced.

The absolute probability is still low by roughly a factor of two, since the hazard
rate rises across the observation window and a model fitted on an earlier period
cannot know that. So the number the reader sees is a **lift**, which is invariant
to a multiplicative level error — both sides shift together — and a **band**
derived from it. A printed percentage would inherit the bias; a band does not.

## Consequences

Bands separate real outcomes monotonically. On the 2025-Q4 fold, accounts in
"Very high" churned at 27.8% against a 8.7% baseline, and "Low" at 5.0% — a
twelve-fold spread between the extremes. On 2026-Q1 the spread narrows to 1.59x
against 0.76x, which is weaker and reported rather than smoothed: the signal
degrades as the base rate rises.

The product no longer answers "what date will they leave". It answers "how much
more likely than average is this account to leave this quarter", which is the
question a retention decision actually turns on, and which the data supports.

Isotonic maps whole input regions to exactly zero where no event was observed.
With roughly 280 events that is thin evidence rather than proof of impossibility,
and a zero hazard makes the survival curve flat forever and any lift against it
undefined. Hazards are floored at 1e-4.

`survival_curve` and the date-crossing helpers remain, because the curve is still
the right internal representation and the backtest needs them to keep measuring
the date error. They are not surfaced.

## Alternatives considered

**More signal.** 1.93x separation is close to the ceiling for these features on
this dataset. Worth revisiting if real telemetry replaces the synthetic data.

**Report the date with a wide interval.** Rejected: an interval spanning a year is
not a prediction, and printing one implies a precision the model does not have.

**Ranking only, no horizon.** Defensible — the queue already ranks — but it
discards a real and measurable signal about *when*, which is the product question.
