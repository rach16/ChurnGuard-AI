# 0004 — Delete the stale knowledge graph rather than load it

**Status:** Accepted · **Date:** 2026-08-30 · **Commit:** `5ff002a`

## Context

`api.py` called `ChurnKnowledgeGraph.load()`, a classmethod that has never
existed; the real API is the instance method `load_graph()`. A bare `except`
swallowed the `AttributeError`, so the knowledge graph had silently never loaded.

Fixing the call was a one-line change. Inspecting the cached pickle first turned
out to matter: it contained 68 customers — *Sequoia Capital Operations*,
*Mirantis Inc*, *WeTransact* — with **zero overlap** against `customers.csv` or
the legacy export. They come from a dataset not present in this repository.

The agents genuinely consume the graph: `_query_knowledge_graph` is a node in the
churn agent's LangGraph workflow, feeding `get_churn_patterns()` into reasoning.

## Decision

Fix `load_graph()`, and **move the stale pickle out of `cache/`** rather than load
it. Startup now reports "no cached graph" honestly. Add `cache/*.pkl` to
`.gitignore` — a pickle built from transient data is not a source artifact.

## Consequences

Had the call been fixed without inspecting the cache, the "fix" would have
injected 68 non-existent customers into agent reasoning. The bug was protecting
the system; repairing it naively would have been a regression that produced
confident, well-formed, entirely fabricated analysis.

The general lesson: when a long-silent error surfaces, check what it was
suppressing before restoring the path. Six months of silent failure is evidence
about the value of the thing failing.

Agents now receive `None` for the graph, so `get_churn_patterns()` contributes
nothing. Rebuilding it requires porting `build_churn_knowledge_graph` off the
legacy Salesforce schema. Tracked, not done.

## Alternatives considered

**Fix the call and keep the cache.** Rejected — see above.

**Rebuild the graph immediately.** Deferred: the builder expects a schema the
project no longer uses, and the graph is not on any critical path.
