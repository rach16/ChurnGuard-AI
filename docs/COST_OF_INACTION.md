# Cost of inaction

**Measured 2026-09-01 · regenerate with `uv run --extra warehouse python scripts/report_exposure.py`**

What the churn risk in the book is worth over one quarter, where it concentrates,
and how much of it the recorded plays could plausibly reach. Every figure here is
produced by that script from `main_gold.train_survival` and the fitted survival
model; none is typed in by hand.

Read the two ratios before the two totals. The ratios are the durable finding; the
dollar totals inherit a known bias, stated below.

## Headline

| | |
|---|---|
| Active accounts | 129 |
| ARR under management | $13,715,500 |
| **Expected loss, one quarter** | **$1,208,213** (8.81% of ARR) |
| Upper bound | $2,174,783 |
| Recoverable, upper estimate | $212,863 (17.6% of expected loss) |
| Held by the worst 10 accounts | **69.5% of expected loss** |

Expected loss is `P(churn within 13 weeks) x ARR`, summed. That is an expectation
over a book. For a single account it is a probability times a number, and that
account will either renew or it will not — it is not a forecast, and it is not a
figure to quote to the customer.

The 8.81% is a useful sanity check rather than a coincidence: the observed
quarterly base rate in the 2025-Q4 backtest fold was 8.7%. An expected-loss share
that lands on the base rate is what a roughly-unbiased ranker should produce.

## Where it concentrates

| Band | Accounts | ARR | Expected loss | Share of loss |
|---|---|---|---|---|
| Very high | 12 | $1,731,200 | $838,366 | **69.4%** |
| Moderate | 42 | $3,820,100 | $295,032 | 24.4% |
| Low | 75 | $8,164,200 | $74,814 | 6.2% |

**Twelve accounts holding 12.6% of ARR carry 69.4% of the expected loss.** That is
the finding worth acting on, and it is a ratio, so the calibration bias below does
not touch it.

There is no "High" row because the band is empty, and empty by construction rather
than by chance — see Limitations.

## Why exposure is not the same list as likelihood

`StreamPro Group` sits in the **Low** band and is still the 9th largest exposure in
the book, because its ARR is $487,300. Ranking by likelihood alone never surfaces
it; ranking by exposure does. Equally, `IntegrationHQ Technologies` is Very high at
$57,900 ARR — real risk, but a worse use of a CSM's week than a moderate risk on a
half-million-dollar account.

This is the argument for the whole module. The queue answers "who is most likely to
leave". A book with a budget needs "where is the money", and the two orderings
genuinely differ.

## What the plays could reach

$212,863, or 17.6% of expected loss. **This is an upper estimate and a sensitivity,
not a promise**, for two independent reasons:

1. **Survivorship.** `success_stories.csv` records 60 accounts where a solution was
   applied and an outcome followed. Nothing recorded the accounts where the same
   play was tried and failed, so the median recorded gain is biased upward by an
   unknown amount. There is no way to correct this from the data available.

2. **It is the model's response, not the world's.** The figure is computed by
   raising each account's adoption features by the median gain its best-matched
   play recorded, then re-scoring. That measures how sensitive the model is to
   adoption moving. It does not establish that running the play moves adoption, nor
   that the movement causes retention.

Three of the top ten recover nothing: their play's adoption gain either does not
move the model or is capped out because the account is already at high adoption.
That is reported rather than smoothed — a recovery estimate that always finds
something is not measuring anything.

Only adoption is moved. The stories also record `engagement_increase`, but as a
relative percentage with no baseline, which cannot be applied to a bounded 0–1
engagement score without inventing one.

## Limitations

**The dollar totals are low by a measured factor.** The walk-forward backtest put
the calibrated understatement at 1.80x (ADR-0009), because the hazard rate rises
across the observation window and a model fitted on an earlier period cannot know
that. The point estimate and the 1.80x bound are both reported; treat the truth as
somewhere in between, and treat the band shares — which are ratios — as the stable
figures.

**The "High" band is empty by construction.** Isotonic calibration maps whole input
regions to a small number of output levels: across 200 rows the model emits **12
distinct probabilities**, and the lift distribution is bimodal, with nothing between
0.73 at the 75th percentile and 4.1 at the 90th. Twelve accounts share an identical
`p=0.574`. The consequence is that **ordering within a band is driven entirely by
ARR**, which is defensible for a work queue but is not additional model signal, and
should not be presented as one.

**The data is synthetic.** Every figure is real arithmetic over generated data. The
method transfers; the numbers do not.

**No renewal calendar.** A quarterly horizon is applied uniformly. In reality an
account 11 months from renewal and one 3 weeks out are different problems, and
`contract_end_date` exists in the data but is not used here.

## What would change the recommendation

- **A recorded failure set.** Success stories with negative or null outcomes would
  remove the survivorship bias and turn the recoverable figure from an upper bound
  into an estimate. This is the single highest-value addition.
- **A holdout intervention.** Withholding the play from a random subset of matched
  accounts is the only thing that would make the causal claim, and it costs a
  quarter of deliberately not acting on some accounts.
- **Refitting on later data.** The 1.80x understatement is a property of the window
  the model was fitted on, not a constant. It must be re-measured after any refit,
  and `CALIBRATION_UNDERSTATEMENT` in `src/core/exposure.py` updated with it.

## Interfaces

| | |
|---|---|
| `GET /book/exposure?horizon_weeks=13&top_n=10` | Whole book, aggregated by band |
| `GET /customer/{id}/exposure` | One account |
| `scripts/report_exposure.py` | The figures in this document |
| `src/core/exposure.py` | The model |
| `tests/test_exposure.py` | 14 tests, including the 0–100 to 0–1 reconciliation |
