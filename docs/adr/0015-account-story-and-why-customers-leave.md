# 0015 · The account story, and "Why customers leave": making the cause of churn obvious

**Status:** Accepted 2026-10-18 (the owner approved the mockup; branch `feat/ux-story`) · **Date:** 2026-10-18 · **Phase:** 8.5

## Context

The owner's verdict on 2026-10-17: "the website is still very hard to understand…
it feels very scattered. Customers must see what is causing churn." A walkthrough
of every page on the Salesforce data (2026-10-18, desktop and phone) found why:

- The queue shows 132 rows and 109 of them say "Low engagement". A reason that is
  the same on five of six rows tells nobody anything.
- The account pane's "Why" box is the scorer's debug output (`engagement 0.034,
  adoption 0.082`). The customer page's "Why this score" is a weight table
  (`35% × 97% = 33.8`). Both explain the maths, not the account.
- The most useful facts are hidden or missing: the renewal date (on every
  account, never shown), the CSM notes ("Champion changed roles", "Evaluating
  Vantage Analytics", at the bottom of the page), the dollars at stake, what to
  do (below the fold).
- Analytics never says what caused churn. The 55 churned accounts carry the
  answer (Competition 15, Champion left 12, Poor support 7, Budget 5; Clarity CRM
  named five times) and the page shows a donut of 27 Medium and 105 Low instead.
- The engagement chart draws one point: the org holds one week of usage.
- On a phone the home page is the list only, overflowing to the right; the
  header overflows on every page.

The root problem is not layout. The site presents a *score* and asks the reader
to infer a cause, while the data holds causes.

## Decision in one paragraph

Every account gets **one story, composed on the server**: a headline sentence
(renewal, dollars, band, the two or three signals that matter), a list of
**signals with their evidence and date** (usage, CSM notes, contact, tickets,
competitors), **what to do** (the plays that worked on accounts with the same
dominant signal), and the weight table behind a disclosure. The queue rows carry
the renewal date and the evidence phrase, not the scorer's label. Analytics
becomes **"Why customers leave"**, built from the accounts that actually churned.
Assistant and Evaluation leave the navigation until the assistant redesign. Every
page works at phone width. **The scorer does not change**: the score, band and
top reason stay what they are and are written back to Salesforce unchanged
(ADR-0014).

## 1. Principles

1. **One story per account, in sentences.** No raw factor numbers on a first
   screen.
2. **Causes come from evidence, not weights.** A signal is shown only when
   something in the record supports it, with its source and date.
3. **The book view answers "what is driving churn here"** from the churned
   accounts, not from the scorer's labels.
4. **Honest about thin data.** One week of usage is said once, small; no empty
   chart.
5. **Nothing invented** (ADR-0007). The headline is a template over real fields;
   no LLM writes anything.
6. **Works on a phone.**

## 2. The story (`GET /customer/{id}/story`)

```
headline   "Starlight Freight Co renews in 262 days. $56K at risk. Medium risk:
            2 of 163 seats active, champion changed roles in August, no open tickets."
risk       score, band, at_risk (top 20% of the book), top_reason (the scorer's)
renewal    contract_end_date, days
arr
signals    [{kind: adverse | neutral | good, text, source, date}]
plays      [{action, renewed, tried, rate_floor}]   (the playbook, as today)
how_built  the contributing factors (the weight table), as_of, history_weeks
salesforce the Account's URL in the org, when the sync's landings can resolve it
```

**Signals**, each from one source, thresholds borrowed from the scorer so the
story and the score never disagree:

| Signal | Source | Adverse when | Otherwise |
|---|---|---|---|
| Seats active | latest engagement snapshot × `seats` | utilisation < 0.3 | neutral (0.3–0.6) / good |
| Feature adoption | latest snapshot | < 0.4 | neutral / good |
| Usage trend | 12-week slope | declining | "only N weeks of usage" when < 3 weeks |
| Last contact | newest interaction | > 60 days | good |
| Tickets, 30 days | cases | ≥ 3, or a High severity one | good: "no tickets in 30 days" |
| Satisfaction | mean CSAT | < 3 | good; omitted when no rated ticket |
| CSM notes | Task subjects matched to a vocabulary (§3) | a risk note in the last 180 days | the newest positive note as good |
| Competitor | `Competitor__c`, lost-deal competitors, "Evaluating X" notes | named | good: "no competitor recorded" |
| Renewal | `contract_end_date` | ≤ 90 days and band ≥ Medium | neutral |

Adverse first, then neutral, then good; the headline takes the first three
adverse (or the first two and the first good when there are fewer).

## 3. Notes as signals

The 278 CSM notes on the org have 25 distinct subjects; the connector already
reads the ones that start "Save play:". A **vocabulary**, kept in
`src/core/signals.py`, maps a subject to a signal and a polarity:

- risk: usage drop, not renewing, pricing pushback, discount requested,
  cancellation terms asked, support complaint, escalation, missed QBR, exec
  sponsor left, champion changed roles, evaluating a competitor (the competitor's
  name is kept)
- positive: QBR completed, positive feedback, expansion discussion, feature
  roadmap shared, onboarding check-in, training, renewal planning call

A note that matches nothing is not a signal; it stays in the timeline. This is
rules over text, not a model; the 4.7 LLM extraction can replace it with the same
output shape. The vocabulary is seeded by the owner's own seed script, which is
stated on the site (ADR-0007).

## 4. The book (`GET /book/causes`)

- **What caused churn**: churned accounts by `churn_category`, with count and ARR
  lost, and the competitors named on them.
- **Where the risk is now**: active accounts by their first adverse signal, with
  count and ARR; renewals due within 90 days.
- **What has worked**: every recorded play with renewed / tried, from the
  playbook.
- Segment table unchanged. The risk donut is dropped.

## 5. The queue (`GET /book/queue`)

One row per active account: id, name, segment, ARR, score, band, at_risk,
renewal days, and the evidence phrase (the first adverse signal's text, or the
first good one). Sorted by score; the page filters by renewal window and segment.

## 6. Pages

| Page | After |
|---|---|
| At risk (home) | Queue rows per §5; the story pane per §2; on a phone the list, and a tap opens the story as a full page |
| Customer | The story at full width, then the ticket list, the note timeline (newest first, risk notes marked), and the usage chart only with 4 or more weeks |
| Why customers leave (was Analytics) | §4 |
| Navigation | At risk, Why customers leave, Integrations, Team. Assistant and Evaluation routes stay; they return with the assistant redesign |
| Every page | Header stacks at phone width; no horizontal scroll |

## 7. What does not change

The scorer, its weights, the at-risk share, the playbook ranking, the write-back
fields, the Integrations and Team pages, every existing route (the new ones are
additions; the old ones keep working until the pages stop calling them).

## 8. Delivery

| Step | Work | Proof |
|---|---|---|
| **8.5.1** | `src/core/signals.py`, `src/core/story.py`; routes story, queue, causes; tests on the Salesforce CSVs and the synthetic set | pytest |
| **8.5.2** | Home: queue rows, story pane, phone layout | Walkthrough on staging, desktop and phone |
| **8.5.3** | Why customers leave; navigation; customer page | Same |
| **8.5.4** | Header at phone width on every page; record; merge | Live site walkthrough |

Cost $0. Effort 4–5 days.

## Consequences

- A CSM reads a sentence and a dated list instead of a number and a label.
- A VP sees what caused the churn the org already had.
- Two more composed endpoints to keep in step with the scorer; the thresholds
  are imported from it, not copied.
- The site says less where the data is thin, which is the honest shape of it.
