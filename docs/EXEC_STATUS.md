# ChurnGuard — status

**Reporting date 2026-09-01 · Prepared for the executive sponsor · One page**

---

## Position

**The system detects, values, explains and recommends. It does not predict a date,
and it has never run against real data.**

Both halves of that sentence matter. The first is the product working. The second
is the honest limit on what can be claimed, and it is the thing to fix next.

| | |
|---|---|
| **Status** | 🟡 On track, one claim retracted |
| **Delivered this period** | Cost-of-inaction model; site survey; PRD; minimum viable architecture |
| **Spend to date** | ~$0.05 |
| **Next decision needed** | Whether to fund a deployment (~$10) or a real-data pilot |

---

## What changed this period

**We retracted the predicted churn date.** A walk-forward backtest showed the model
underpredicts churn hazard by 4.36x, and 95% of its predicted dates landed late by
a median of 222 days. Calibration halved the miscalibration and made the dates
*worse*, at 287 days.

The cause is structural, not a tuning failure: when hazard is small, a survival
curve's midpoint sits far in the future however well the model ranks. No amount of
further work moves it.

**What replaced it is more useful.** The system now reports how much more likely
than average an account is to leave *this quarter*. The top band churns at 27.8%
against an 8.7% baseline — a twelve-fold spread between the extremes. That is the
question a retention decision actually turns on, and unlike the date, the data
supports it.

**We put a number on the risk.** Expected loss over one quarter is **$1.21M, 8.8%
of ARR under management**. The finding worth acting on: **twelve accounts holding
12.6% of ARR carry 69.4% of that loss.**

---

## The number to plan against

| Band | Accounts | ARR | Expected quarterly loss | Share |
|---|---|---|---|---|
| Very high | 12 | $1.73M | $838k | **69.4%** |
| Moderate | 42 | $3.82M | $295k | 24.4% |
| Low | 75 | $8.16M | $75k | 6.2% |

Two caveats travel with these figures and should travel with them into any deck:

- The **dollar totals are conservative by a measured factor of 1.80**. Treat $1.21M
  as a floor and $2.17M as the upper bound.
- The **share column is the durable figure.** It is a ratio, so it is unaffected by
  that bias. Concentration is the finding; the absolute total is context.

Ranking by money is not the same list as ranking by likelihood. One account sits in
the *Low* band and is still the ninth largest exposure in the book, because it is
worth $487k. A team with finite hours needs the money ordering.

---

## Confidence

| Claim | Evidence | Confidence |
|---|---|---|
| Ranks at-risk accounts better than chance | AUC 0.877 held out by customer | **High** |
| Ranking holds over time | AUC 0.665 held out by time — **below our 0.70 bar** | **Medium** |
| Bands separate real outcomes | 27.8% vs 8.7% (Q4); narrows to 1.59x vs 0.76x (Q1) | Medium |
| Explanations are the right account's | True by construction — selected by account key | High |
| Recommendations reflect what works | Only successful interventions were ever recorded | **Low — upper bound only** |
| Any of this holds on real data | Never tested. All data is synthetic | **None** |

The last row is the one that governs everything above it.

---

## Risks

| Risk | Status | Response |
|---|---|---|
| Out-of-time performance below bar | **Open** — 0.665 vs 0.70 | Reported, not smoothed. Likely a property of synthetic data whose churn concentrates late |
| Recovery estimate is survivorship-biased | **Accepted** | Labelled an upper bound everywhere it appears. Only a holdout would fix it |
| Never validated on real data | **Open, largest** | Needs a pilot. This is the recommendation below |
| Adopted then abandoned | **Not yet live** | Design answer: deliver into the CS team's existing tool, not a second dashboard |

---

## Recommendation

**Fund a real-data pilot, not more model work.**

The model is at the ceiling of what this dataset supports; further tuning would
produce better numbers about generated data. The unquantified risk is entirely in
whether the approach survives contact with a real customer environment, and the
site survey delivered this period is the instrument for finding out cheaply.

Three questions decide it, and they can be answered in one meeting: is churn
recorded with a *date*, were interventions and their outcomes ever written down,
and are there enough churn events to fit a model. Most environments fail at least
one, and knowing which one is worth more than another quarter of engineering.

The alternative ask on the table — ~$10 to deploy the written infrastructure once
and capture evidence it runs — is cheap and proves an engineering point rather than
a product one. Worth doing, but not instead of the above.

---

## Delivered to date

Data foundation, warehouse and feature engineering complete. Survival model fitted,
backtested and honestly bounded. Retrieval improved from 0.735 to 0.971 on
single-entity questions. Operator console rebuilt. Provider lock-in removed —
five model providers configurable, including a fully local one, so a "customer data
cannot leave our estate" constraint is a config change rather than a rewrite.

Nine architecture decision records. All seven consulting artifacts complete.

**One prior claim retracted:** a 94.7% retrieval accuracy figure inherited from the
original repo was wrong three independent ways and had propagated into sales
material. It is publicly corrected rather than quietly deleted.

---

*Figures regenerate from `scripts/report_exposure.py`. Every number here names the
script and date that produced it in `.claude/STATUS.md`.*
