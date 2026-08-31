# 0007 — Retract the 94.7% accuracy claim publicly

**Status:** Accepted · **Date:** 2026-08-30 · **Commit:** `21e4788`

## Context

The dashboard displayed `prediction_accuracy: 0.947` as "Prediction Accuracy" on
two pages. The README advertised 94.7% in seven places, including a badge and a
competitor comparison table. Two sales scripts asserted "we're 94.7% accurate" to
a prospect.

The number was never a prediction accuracy. It was `parent_document` **context
recall**, copied from `metrics/ragas_evaluation_results.csv`. That file was
produced by:

1. evaluating against an LLM-generated golden set whose questions referenced
   companies present in no data file (ADR-0002),
2. over a 25-document corpus consisting of the legacy export alone, and
3. using harness code that raises `AttributeError` at its own pinned
   `ragas==0.2.10`, because it treated `EvaluationResult` as a dict.

Three independent reasons the figure could not have been a real measurement.
Measuring the same retrieval method against the rebuilt golden set and corpus
gives **0.150**.

## Decision

Remove the claim from every surface, delete the false baseline CSV rather than
leave it in the repository, and **add a note to the README recording that the old
figure was wrong and why**.

## Consequences

`/evaluation-results` now returns 404 with "run evaluation first", which is the
honest state until a real baseline exists.

Retracting explicitly rather than silently is the load-bearing part. A number that
has been in a public README propagates into forks, screenshots and conversations;
deleting it quietly leaves every copy intact with nothing to contradict it. The
note also states the replacement number is *worse*, which is the only version of
this that is credible.

The cost is a README that leads with a poor retrieval score. That is preferable to
one that leads with a good fabricated one.

## Alternatives considered

**Replace with the subset measurement.** Rejected: 10 questions is not a baseline,
and substituting one under-evidenced number for another repeats the original
mistake.

**Delete quietly.** Rejected — see above.
