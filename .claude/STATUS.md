# Status

Living record. See the maintenance rule in `CLAUDE.md`.
Last updated 2026-08-31 · `main` @ `8a8bee5` · 16 commits since fork.

## In flight

Nothing. Phase 2.1 landed; 2.2w is next and not started.

## Completed

| Phase | Delivered | Commit | Measurably changed |
|---|---|---|---|
| **0.5** | Dataset rebuilt entity-first | `c3ad4e9` | Segment conflicts 89→0. interactions∩tickets 16→200. Labelled churn rows 25→71. |
| **0.6** | Golden set derived from data, not an LLM | `060695c` | Questions grounded in real customers 0/54 → 32/65 |
| **0.4** | `.env.example` | `4aa90bc` | — |
| **0.7** | RAG points at the real corpus | `dcab498` | Indexed documents 25 → 771 |
| **0.8** | RAGAS harness fixed | `a7cd8f2` | Harness ran for the first time at pinned `ragas==0.2.10` |
| **0.1** | Two backends collapsed into one | `7222ee2` | `api_simple.py` deleted (660 LOC). `/ready` added. |
| **0.10** | KG load fixed, stale graph dropped | `5ff002a` | 68 phantom customers no longer reachable by agents |
| **0.11** | Subset eval outputs untracked | `ae2f838` | — |
| **0.2** | Scoring from observed data | `96b28a2` | risk_score was `np.random.uniform(70,92)`; now AUC 0.791. Detail endpoint byte-identical across requests. |
| **0.3** | 94.7% claim removed | `21e4788` | 7 README claims + 2 sales scripts corrected; false baseline CSV deleted |
| **1.1–1.3** | dbt warehouse, scoring in SQL | `5ce3cbb` | 14 models, 52 dbt tests, SQL/Python parity on 200/200 |
| **1.4** | Gold layer to S3 + Athena | `27f9d81` | 4 tables in Glue; 6/6 queries agree across engines |
| **5.0** | README rewrite + 7 ADRs | `3d208a3` | README 469→198 lines; 7 stale claims removed |
| **4.1** | Hybrid BM25 + semantic retrieval | `f41bfe6` | Single-entity hit 0.735→0.971, recall 0.544→0.941 |
| **2.1** | Async unblocked; data baked into image | `2278cee` | 5 concurrent requests 2.03s→0.42s (4.9x). Container runs standalone with no volumes. |

## Current metrics

| Metric | Value | Measured | Source |
|---|---|---|---|
| Retrieval — single-entity hit rate (hybrid) | **0.971** | 2026-08-31 | `scripts/benchmark_retrieval.py` |
| Retrieval — single-entity recall (hybrid) | **0.941** | 2026-08-31 | same |
| Retrieval — single-entity hit rate (naive) | 0.735 | 2026-08-31 | same |
| Retrieval — all-answerable recall (hybrid) | 0.609 | 2026-08-31 | same |
| Concurrency — 5 requests × 0.4s work | **0.42s** (was 2.03s) | 2026-08-31 | threadpool harness, 2.1 |
| Backend image size | 1.99 GB | 2026-08-31 | `docker images` |
| Health scorer AUC vs churn label | **0.791** | 2026-08-30 | `CustomerHealthScorer.scorer_auc()` |
| SQL/Python scoring parity | 200/200 within 0.1 | 2026-08-30 | `tests/test_warehouse_parity.py` |
| Dataset contracts | 28/28 pass | 2026-08-31 | `scripts/validate_dataset.py` |
| dbt tests | 52/52 pass | 2026-08-30 | `dbt test` |
| DuckDB vs Athena | 6/6 queries agree | 2026-08-30 | `warehouse/verify_athena.py` |
| Corpus size | 771 documents | 2026-08-31 | `ChurnDataLoader.get_all_documents()` |
| Dataset | 200 customers, 71 churned (35.5%) | 2026-08-31 | `data/customers.csv` |

Superseded:

- Retrieval context recall ~~0.150 (2026-08-30, RAGAS, 10-question subset)~~ —
  a different metric on a different sample; not comparable to the current
  figures. It overstated the problem.
- Retrieval accuracy ~~94.7%~~ — fabricated, retracted. See ADR-0007.
- Prediction accuracy ~~0.947~~ — was retrieval recall mislabelled. Removed in `21e4788`.

**Not measured:** end-to-end latency. Full 65-question RAGAS baseline (needs 3.1,
~$2–5). Churn *prediction* accuracy — no classifier has been trained; the score
is a weighted heuristic.

## Known gaps and dead code

| Item | Detail |
|---|---|
| Knowledge graph always `None` | `build_churn_knowledge_graph` expects the legacy Salesforce schema. `churn_agent.py` and `research_team.py` call `get_churn_patterns()`, which contributes nothing. Stale cache removed in `5ff002a`. |
| No committed eval baseline | `/evaluation-results` returns 404 by design |
| Reranking | `COHERE_API_KEY` not set. `langchain_cohere` **is** importable, so `COHERE_AVAILABLE=True` and the Cohere path is attempted. Behaviour unverified with the current benchmark. |
| All LLM calls hardcoded to OpenAI | 8 files construct `ChatOpenAI`/`OpenAIEmbeddings` directly. No provider abstraction. → 4.2 |
| `/integrations` page | Static hardcoded array, zero API calls |
| `/evaluations` page | Renders an error since the baseline CSV was deleted |
| No per-feature telemetry | Feature-usage chart derived deterministically from one adoption rate |
| Vercel builds red | Cosmetic. → 0.9 |
| Backend image is 1.99 GB | ML dependencies dominate. Slow ECR pulls and cold starts. Not addressed. |

## Next: Phase 2 — AWS deployment

| # | Item | Effort | Cost | Depends on |
|---|---|---|---|---|
| 2.2w | Write Terraform (ECR, VPC, ECS, ALB) | 2d | $0 | 2.1 |
| 2.2a | `terraform apply` | — | **$5–10 test / ~$105mo** | 2.2w |
| 2.3 | Cognito auth, rate limiting, token cap | 1d | $0 to write | 2.2 |
| 2.4 | GitHub Actions → ECR → ECS | 1d | $0 (free tier) | 2.2 |

Also unstarted, no dependency on Phase 2:

| # | Item | Effort | Cost |
|---|---|---|---|
| 4.2 | LiteLLM provider abstraction | 1d | $0 |
| 3.1 | Full 65-question RAGAS baseline | ½d | **$2–5** |
| 3.2 | pytest regression gate | ½d | $0 (needs 3.1) |
| 5.2–5.4 | Site survey, PRD, MVA, cost-of-inaction | 1½d | $0 |

## Deferred

| Item | Reason |
|---|---|
| 0.9 Vercel fix | User deferred 2026-08-30. Needs dashboard access to set Root Directory to `frontend`, or disable the integration. |
| 4.3 Vector migration off Qdrant | No target until Phase 2 deploys. OpenSearch Serverless has a 2-OCU minimum ≈ **$350/mo** for 137 KB — use pgvector (~$15/mo) or Qdrant Cloud free tier instead. |
| Phase 6 air-gap / edge | Only worth it for defence or regulated clients. |
| Knowledge graph rebuild | Needs the builder ported off the legacy schema. Not on any critical path. |
| Distributed compute (Spark/Ray) | Roadmap item, deliberately skipped — 1.6 MB dataset. |

## Rejected

| Item | Reason |
|---|---|
| SQL router for aggregate questions | Proposed twice as 4.4; user instructed no scope additions. Would route aggregate questions to `customer_health_score` instead of retrieval. Not built. |
| Stage 7 UI phase | Proposed; not adopted. UI items tracked under Known gaps instead. |
| `SALESFORCE_COMPARISON.md` | Deleted 2026-08-31. Compared to Einstein on unsupportable numbers, including sales scripts asserting "we're 94.7% accurate". |
| Keeping the stale comprehensive doc live | Archived, not deleted — ADRs cite what it claimed. |
