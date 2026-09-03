# Status

Living record. See the maintenance rule in `CLAUDE.md`.
Last updated 2026-09-02 · `main` @ `be7d69b` · 133 commits since fork.

Phases re-derived against `docs/ARCHITECTURE.md` on 2026-08-31. The plan up to
that point was a remediation backlog; it is now ordered by the critical path to a
predicted churn date. See **Re-derivation** at the foot for what moved and why.

## In flight

Nothing. **The system is deployed and public.**

| Piece | Where | Independent of the laptop |
|---|---|---|
| Frontend | Vercel — `churn-guard-ai-nine.vercel.app` | ✅ |
| Backend | Render free tier, reached only through the `/api` rewrite | ✅ |
| Vector index | Qdrant Cloud free tier, us-west-2 | ✅ |
| AI chat | Render + OpenAI key | ✅ |

Verified 2026-09-01: `/health` reports **8/8 components healthy**, seven endpoints
return 200, and the live site renders 50 accounts and $2.23M ARR at risk with the
laptop's own backend stopped.

Free tier sleeps after ~15 minutes idle; a cold start takes ~50s and the UI now
says so while it waits.

2.2a (`terraform apply`) was **declined 2026-09-01**: it costs $5–10 and does not
achieve the actual goal. The ALB is HTTP-only and a browser blocks an HTTPS page
from calling it, so deploying to AWS would not have made the public site work.
Render supplies HTTPS free. The Terraform stays as the IaC artifact.

**Every zero-cost item in the plan is complete.** Steps 2 and 4 of
`docs/DEPLOYMENT_SPEC.md` — the `/api` rewrite and the heartbeat — shipped
2026-09-02 and were the last of them.

"Every recorded defect is closed" was true on 2026-09-01 and is a weaker claim
than it reads. Three defects were introduced and closed on 2026-09-02, and two
of them were found by looking at the running system rather than by any test:
`/ask` broken for a day (green tests throughout), and a probe hammering Qdrant
with ~51,000 requests a day (found in Render's logs). The suite is an alarm for
what it covers, not evidence that the deployed system works.

Remaining paid work totals **$2–5** (3.1 RAGAS) plus an optional **$19/year**
for a real domain. 2.2a (`terraform apply`, $5–10) stays declined on goal.
Verified against AWS on 2026-09-01: no ECS, EC2, load balancer or NAT gateway
exists, so nothing is accruing.

| Live | State |
|---|---|
| CI | green, 3 jobs, free public runners |
| Heartbeat | green, every 30 min against production, emails on failure |
| Vercel | green, one project, serving real data |
| Local stack | 9/9 components healthy (`retrieval_join` added 2026-09-02) |

## Completed

| Phase | Delivered | Commit | Measurably changed | FDE roadmap |
|---|---|---|---|---|
| **0.5** | Dataset rebuilt entity-first | `c3ad4e9` | Segment conflicts 89→0. interactions∩tickets 16→200. Labelled churn rows 25→71. | P1 · Data quality |
| **0.6** | Golden set derived from data, not an LLM | `060695c` | Questions grounded in real customers 0/54 → 32/65 | AI · Eval, inner loop |
| **0.4** | `.env.example` | `4aa90bc` | — | — |
| **0.7** | RAG points at the real corpus | `dcab498` | Indexed documents 25 → 771 | AI · RAG ingestion |
| **0.8** | RAGAS harness fixed | `a7cd8f2` | Harness ran for the first time at pinned `ragas==0.2.10` | AI · Eval, inner loop |
| **0.1** | Two backends collapsed into one | `7222ee2` | `api_simple.py` deleted (660 LOC). `/ready` added. | — |
| **0.10** | KG load fixed, stale graph dropped | `5ff002a` | 68 phantom customers no longer reachable by agents | — |
| **0.11** | Subset eval outputs untracked | `ae2f838` | — | — |
| **0.2** | Scoring from observed data | `96b28a2` | risk_score was `np.random.uniform(70,92)`; now AUC 0.791. Detail endpoint byte-identical across requests. | AI · Proof mechanisms |
| **0.3** | 94.7% claim removed | `21e4788` | 7 README claims + 2 sales scripts corrected; false baseline CSV deleted | AI · Proof mechanisms |
| **1.1–1.3** | dbt warehouse, scoring in SQL | `5ce3cbb` | 14 models, 52 dbt tests, SQL/Python parity on 200/200 | P1 · Medallion, modeling, SQL |
| **1.4** | Gold layer to S3 + Athena | `27f9d81` | 4 tables in Glue; 6/6 queries agree across engines | P2 · Cloud data architecture |
| **5.0** | README rewrite + 7 ADRs | `3d208a3` | README 469→198 lines; 7 stale claims removed | P3 · ADR artifact |
| **4.1** | Hybrid BM25 + semantic retrieval | `f41bfe6` | Single-entity hit 0.735→0.971, recall 0.544→0.941 | AI · Hybrid search |
| **2.1** | Async unblocked; data baked into image | `f3c2570` | 5 concurrent requests 2.03s→0.42s (4.9x). Container runs standalone with no volumes. | P2 · Deployment readiness |
| **2.5** | Runtime deps split from eval/notebook/viz | `5a8a33f` | Backend image 1.99 GB → 1.11 GB (−44%). 5 unused packages dropped. | P2 · Deployment readiness |
| **2.3** | API key auth, rate limit, token cap | `9ab57a2` | Auth + sliding-window limit on all non-probe routes; output capped at 1024 tokens in one factory, not 6 call sites. Wired into the ECS task definition; `terraform validate` passes. 20 new tests. | P2 · Enterprise security |
| **2.4** | CI on every push; deploy pipeline written | `a423dbc` | 3 CI jobs (contracts+warehouse+65 tests, typecheck+build, terraform validate) on free public runners. Deploy is `workflow_dispatch` only and authenticates by OIDC — no long-lived AWS key in a public repo. | P2 · DevSecOps |
| **D1** | Evaluation 404 + evidence tests | `aea2a34` | `/evaluation-results` 500→404. Evidence gains 10 tests, which exposed BM25 returning **zero evidence** when a customer has few passages — IDF collapses to 0 on a tiny corpus. | AI · RAG grounding |
| **3.2a** | Retrieval regression gate in CI | `1273a62` | Retrieval could have degraded 0.971→0.6 with every test still green. Now fails the build. Keyword-only, so **$0 per run** — verified it fires on a degraded retriever and not on a working one. | AI · Eval, inner loop |
| **2.7** | LLM spend caps for a public deployment | `ee4b907` | Paid routes gain a per-visitor hourly limit and a **shared daily budget** — a per-caller limit caps one person, not a crowd. ~$0.20/day ceiling. Free routes untouched. | P2 · Enterprise security |
| **2.8** | Render blueprint; bind the host-assigned PORT | `f0623da` | App read `BACKEND_PORT` only; every managed host assigns `PORT`, so the container was unreachable. Free-tier deploy, secrets `sync:false`, health check on `/health` not `/ready`. | P2 · Deployment |
| **2.9** | Backend live on Render; site works with the laptop off | `989eb89`, `b82345c`, `786a758` | **The demo is now reachable by anyone.** Three bugs no local run could catch: model and warehouse absent from the image, `scikit-learn` and `duckdb` not installed at runtime, and `jupyter` being the last Docker stage so a target-less build shipped the wrong image. Fetch timeout 10s → 90s for free-tier cold starts. | P2 · Deployment |
| **2.10** | Vector index persisted; cold start 66s → 2s | `e8ffac3`, `79c51d9` | Startup dropped and re-embedded 771 documents on **every wake** — most of a 66s cold start, ~1¢ each time. Index now lives in a free Qdrant Cloud cluster and startup binds to it. Measured 2026-09-02 after 17 min idle. | P2 · Deployment |
| **2.2w** | Terraform written, not applied | `782c291` | 26 resources across 643 lines; `terraform validate` passes. $0 spent. | P2 · IaC, networking |

### Architecture

| Phase | Delivered | Commit | Measurably changed | FDE roadmap |
|---|---|---|---|---|
| **A** | Architecture doc + ADR-0008 | `1efc4ff` | Layering settled: warehouse owns numbers, vector store owns narrative. Model approach chosen. | P3 · Agentic deployment architecture |
| **9.1** | Recommended plays from recorded outcomes | `b884e47` | The app now answers "what has worked on accounts like this", not only "who is at risk" | P3 · Last-mile integration |
| **8.1** | Operator console: rail, queue, detail pane | `3ac11f8` | Accounts visible without scrolling 1.5 → 14. Selection replaces navigation. | P3 · Technical demo |
| **8.2** | All pages on shadcn + shared shell; light/dark | `554236c` | 5 routes on one design system; charts read theme tokens | P3 · Technical demo |
| **8.3** | Degraded ≠ offline in the UI | `6f078b8` | A 503 from an LLM route no longer claims the backend is down | — |
| **8.4** | Honest empty states; integrations marked roadmap | `ee8a0cc` | Six fabricated "live" connectors relabelled | AI · Proof mechanisms |
| **4.2** | Provider abstraction | `c551ba1` | 9 vendor constructions → 1 factory. 5 providers configurable; `/health` reports which is active. | AI · Model-agnostic |
| **4.4** | Evidence scoped to one customer's record | `731e023` | Explanations keyed by customer_id, so another account's story cannot surface | AI · RAG grounding |
| **4.5** | Knowledge graph deleted; agents on hybrid | `a089a2c` | ~700 lines removed. Agents and /ask default to hybrid (0.971 vs 0.735). | — |
| **7.3** | Walk-forward backtest; date claim retracted | `70be342` | Dates unfixable (median survival 482d at h=0.04). Replaced with a quarter band: 27.8% vs 8.7% baseline in the top band. | AI · Eval, outer loop |
| **7.2** | Discrete-time survival model | `7e1a664` | AUC 0.877 held out by customer, calibrated (2.26% predicted vs 1.93% actual). Dates median 196d off — not usable. | AI · Modelling |
| **5.4** | Cost of inaction: exposure in dollars | `f05625e` | Expected loss $1.21M/qtr (8.81% of ARR). 12 accounts = 12.6% of ARR carry **69.4%** of it. Exposure order differs from likelihood order. | P3 · Artifacts |
| **5.2** | Site survey instrument + PRD | `d32e017` | Every survey item traced to a named code dependency. Minimum-data thresholds derived, not asserted: **9.5 events/feature**, ~300 events, 2yr weekly history. | P3 · Site survey, scoping |
| **7.5b** | Heuristic churn date removed from the queue | `b2e4cfe` | The queue showed `~180d` from `np.interp` over a heuristic score, unlabelled, on the screen operators work from. Replaced by the modelled band where one exists. | AI · Proof mechanisms |
| **0.9** | Vercel deploying green | settings change, no commit | Two settings, not one: **Framework Preset was `FastAPI`** (persisted from auto-detection) as well as Root Directory being unset. Fixing only the root directory left the build failing identically. Live at `churn-guard-ai-nine.vercel.app`, serving real data. | — |
| **5.3** | MVA (4 tiers, mermaid) + exec status report | `75b6a50` | Measured that **11 of 12 GET routes serve with no LLM key** — detect, value and explain need no model provider. Scoping now leads with the deterministic tiers. | P3 · MVA, exec reporting |
| **7.1** | Point-in-time features + survival labels | `4f01b1b` | 15,711 training rows, 284 hazard positives (1.81%). Leakage verified by rebuild-on-truncated-data. | P1 · Feature engineering |

## Current metrics

| Metric | Value | Measured | Source |
|---|---|---|---|
| Retrieval — single-entity hit rate (hybrid) | **0.971** | 2026-08-31 | `scripts/benchmark_retrieval.py` |
| Retrieval — single-entity recall (hybrid) | **0.941** | 2026-08-31 | same |
| Retrieval — single-entity hit rate (naive) | 0.735 | 2026-08-31 | same |
| Retrieval — all-answerable recall (hybrid) | 0.609 | 2026-08-31 | same |
| Concurrency — 5 requests × 0.4s work | **0.42s** (was 2.03s) | 2026-08-31 | threadpool harness, 2.1 |
| Backend image size | **1.11 GB** | 2026-08-31 | `docker images` |
| Health scorer AUC vs churn label | **0.791** | 2026-08-30 | `CustomerHealthScorer.scorer_auc()` |
| SQL/Python scoring parity | 200/200 within 0.1 | 2026-08-30 | `tests/test_warehouse_parity.py` |
| Dataset contracts | 28/28 pass | 2026-08-31 | `scripts/validate_dataset.py` |
| dbt tests | **67/67 pass** | 2026-08-31 | `dbt test` |
| pytest | ~~45~~ → ~~65~~ → ~~75~~ → ~~80~~ → ~~85~~ → **94/94 pass** | 2026-09-02 | `pytest tests/` |
| Training rows / hazard positives | 15,711 / 284 (1.81%) | 2026-08-31 | `main_gold.train_survival` |
| Best single feature (point-in-time) | AUC **0.769** `engagement_mean_4w` | 2026-08-31 | rank AUC vs `event_in_next_period` |
| Survival model — held out by customer | AUC **0.877**, Brier 0.019 | 2026-08-31 | `scripts/train_survival_model.py` |
| Survival model — held out by time | AUC 0.665, Brier 0.049 | 2026-08-31 | same — underpredicts, see gaps |
| Predicted churn date accuracy | **196 days median abs error** | 2026-08-31 | 10 held-out churners, predicted 180d ahead |
| DuckDB vs Athena | 6/6 queries agree | 2026-08-30 | `warehouse/verify_athena.py` |
| Expected quarterly loss | **$1,208,213** (8.81% of ARR) | 2026-09-01 | `scripts/report_exposure.py` |
| Loss concentration — worst 10 accounts | **69.5%** of expected loss | 2026-09-01 | same |
| Distinct calibrated probabilities (200 rows) | **12** | 2026-09-01 | `rank_book` on `train_survival` |
| Corpus size | 771 documents | 2026-08-31 | `ChurnDataLoader.get_all_documents()` |
| Backend cold start | ~~66s~~ → **2s** | 2026-09-02 | timed `curl /health` after 17 min idle |
| Retriever startup | ~~93s~~ → **13.6s** | 2026-09-02 | `load_and_process_documents` against the live cluster |
| Dataset | 200 customers, 71 churned (35.5%) | 2026-08-31 | `data/customers.csv` |
| AWS spend to date | **< $0.01** | 2026-09-01 | S3 139.5 KiB / 40 objects; Glue 4 tables (free tier); Athena 20 queries, 9,876 bytes scanned but billed at a 10 MB per-query floor ⇒ ~$0.001. No ECS/EC2/ALB/NAT exists. |
| OpenAI spend to date | **unverified** | — | No programmatic access to billing. Read at platform.openai.com/usage. |
| Vercel spend | $0 | 2026-09-01 | Hobby plan |
| Render spend | $0 | 2026-09-01 | Free tier; sleeps when idle |
| LLM spend ceiling | **200 questions/day** ≈ $0.20 | 2026-09-01 | `LLM_DAILY_BUDGET`, plus a $20/month hard cap set in the OpenAI dashboard |

Superseded:

- Total spend ~~"~$0.05"~~ — repeated across several summaries and **never
  measured**. Replaced by the verified AWS figure above plus an explicitly
  unverified OpenAI figure. A number restated often enough starts to look
  sourced; this one was not.

- Retrieval context recall ~~0.150 (2026-08-30, RAGAS, 10-question subset)~~ —
  a different metric on a different sample; not comparable to the current
  figures. It overstated the problem.
- Backend image size ~~1.99 GB (2026-08-31)~~ → 1.11 GB after 2.5.
- dbt tests ~~52 (2026-08-30)~~ → 67 after 7.1.
- Retrieval accuracy ~~94.7%~~ — fabricated, retracted. See ADR-0007.
- Prediction accuracy ~~0.947~~ — was retrieval recall mislabelled. Removed in `21e4788`.

**Not measured:** end-to-end latency. Full 65-question RAGAS baseline (needs 3.1,
~$2–5). Churn *prediction* accuracy — no classifier has been trained; the score
is a weighted heuristic.

## FDE roadmap coverage

Tracks https://github.com/pierpaolo28/Awesome-FDE-Roadmap, translated GCP→AWS
(BigQuery→Athena, Cloud Run→ECS Fargate, Vertex→Bedrock).

| Roadmap area | Status | Where |
|---|---|---|
| **Phase 1 — Data Engineering** | ✅ complete | 1.1–1.4, 0.5 |
| ├ Medallion, dimensional modeling, advanced SQL | ✅ | `5ce3cbb` |
| ├ Data quality & observability | ✅ | 67 dbt tests + 28 contracts |
| └ Distributed compute (Spark/Ray) | ❌ skipped | 2.0 MB dataset — see Deferred |
| **Applied AI** | ✅ complete | |
| ├ Predictive modelling | ✅ | 7.1–7.3, `7e1a664` |
| ├ Hybrid search (BM25 + vectors) | ✅ | `f41bfe6` |
| ├ Eval, inner loop | ✅ | golden set + benchmark harness |
| ├ Eval, outer loop | ❌ | 3.3 / 3.4, unscheduled |
| ├ Multi-agent orchestration | ✅ | pre-existing, now served |
| └ Model-agnostic providers | ✅ | 4.2, `c551ba1` |
| **Phase 2 — Cloud Architecture** | 🔄 mostly complete | |
| ├ Cloud data architecture | ✅ | 1.4, S3 + Glue + Athena |
| ├ IaC / Terraform | ✅ written, validated, unapplied | `782c291` |
| ├ Networking, VPC, IAM | ✅ written, validated, unapplied | `782c291` |
| ├ Enterprise security | ✅ auth, rate limits, spend caps | 2.3, 2.7, ADR-0010 |
| ├ DevSecOps / CI-CD | ✅ CI green on every push; deploy pipeline written | 2.4 |
| ├ Containerised deployment, running | ✅ **live on Render**, laptop-independent | 2.9 |
| └ Container orchestration (ECS/Fargate) | ❌ written, never applied | 2.2a — declined, see Deferred |
| **Phase 3 — Consulting** | ✅ **7 of 7 artifacts** | |
| ├ Technical demo as value narrative | ✅ | Phase 8, `3ac11f8` |
| ├ ADRs | ✅ | 10 records, `3d208a3` + `1efc4ff` + ADR-0009/0010 |
| ├ Agentic deployment architecture | ✅ | `docs/ARCHITECTURE.md` |
| ├ Cost of inaction / value case | ✅ | 5.4, `docs/COST_OF_INACTION.md` |
| ├ Site Survey | ✅ | 5.2, `docs/SITE_SURVEY.md` + worked example |
| ├ Technical Scoping / PRD | ✅ | 5.2, `docs/PRD.md` |
| └ MVA + Exec Status Report | ✅ | 5.3, `docs/MVA.md` + `docs/EXEC_STATUS.md` |
| **Air-gapped / tactical edge** | ❌ | Phase 6, deferred |

Note: 2.1 and 2.5 were deployment *readiness*, not Phase 2 competency. That
changed on 2026-09-01 — the service is deployed, public and independent of any
laptop, so everything in Phase 2 except AWS-specific orchestration is now
demonstrated rather than described.

**What ECS would add that Render does not:** orchestration on the platform the
roadmap names, and evidence the Terraform runs. It was declined because it costs
$5–10 and could not have served the public site — the ALB is HTTP-only and
browsers block an HTTPS page from fetching HTTP. The Terraform stands as the IaC
artifact; running it would prove orchestration and nothing else.

## Known gaps and dead code

| Item | Detail |
|---|---|
| ~~No predictive model~~ **CLOSED** | Was: `risk_score` a weighted sum, `days_until_churn` an `np.interp` over it. Closed by 7.2 — a fitted discrete-time survival model now serves the likelihood band. The heuristic `days_until_churn` remains in `health_scoring.py` and is not surfaced. |
| `days_since_last_interaction` sentinel | Uses 9999 when a customer has no prior interaction, which distorts its distribution (AUC 0.569 despite a large mean gap). 7.2 must impute or flag rather than treat it as a number. |
| **Absolute probability underpredicts ~2x** | The hazard rate rises across the window, so a model fitted earlier cannot know it. Mitigated by reporting a lift, which is invariant to a level error. |
| **Only 12 distinct probabilities across 200 rows** | Isotonic maps whole input regions to one level, and the lift distribution is bimodal — nothing between 0.73 (p75) and 4.1 (p90), so the **"High" band is empty by construction** and 12 accounts share `p=0.574`. Ordering inside a band is therefore driven entirely by ARR, which is fine for a work queue but is not model signal. Measured 2026-09-01. |
| ~~Cold start on the free tier~~ **mostly closed** | Was ~66s; **2s measured 2026-09-02** after moving the index to Qdrant Cloud. The "waking the server" message is now near-redundant, kept because it costs nothing. |
| ~~`:memory:` Qdrant re-embeds on every wake~~ **CLOSED** | Closed by 2.10. |
| **Cold start measured from outside** | The 2s reading cannot distinguish "container genuinely restarted" from "Render never fully slept". Confirm against Render's own startup logs before quoting it. |
| **Index staleness is a point count, not a checksum** | Startup reuses any populated collection. Change the corpus and the index is silently stale until `REINDEX_ON_START=true` is set once. |
| **Rate limit is per process, not per service** | Behind >1 replica the effective limit is the configured value times `desired_count`. Stated in `/health`, `.env.example`, the module docstring and ADR-0010. Needs shared state (ElastiCache or the ALB's own limiter); deferred until there is more than one replica. |
| **Static shared API keys** | No rotation, no per-user attribution, revocation only by editing the secret and restarting. The floor, not the ceiling — 2.6 (Cognito) is the ceiling. |
| **No test covers the evidence layer** | `CustomerEvidence` is keyed by `customer_id`, so cross-account leakage is structurally unreachable — but nothing asserts it. Found while writing the PRD's success criteria on 2026-09-01; S4 is marked true-by-construction rather than verified. A test belongs in 4.4. |
| **`api.py` never calls `load_dotenv`** | A key in `.env` is ignored unless exported, so RAG, the agent and the multi-agent system come up unavailable on a stock checkout. Pre-existing, verified against `main` on 2026-09-01. Contradicts the `.env` instruction in CLAUDE.md. One-line fix, not applied — out of scope for 5.4. |
| **Recoverable figure is survivorship-biased** | `success_stories.csv` records only interventions that worked. The recovery estimate is an upper bound and is labelled as one everywhere it is returned. Removing the bias needs a recorded failure set. |
| **No renewal calendar in exposure** | A 13-week horizon is applied uniformly; `contract_end_date` exists but is unused, so an account 3 weeks from renewal is weighted like one 11 months out. |
| **40% of accounts sit at the calibrator floor** | Isotonic collapses everything below its lowest knot, so the Low band carries no ordering inside it. |
| **Hazard is non-stationary** | Rises monotonically 0%→5.22% across quarters, so a model trained on early data underpredicts later. A generator artifact: every customer has a declining trajectory, so churn concentrates at the end of the window. → 7.3 |
| In-sample AUC 0.996 vs 0.877 held out | Expected on grouped data — one customer contributes ~100 near-identical rows — but means in-sample metrics carry no information here. |
| `engagement_slope_4w` is noise | AUC 0.503 against the hazard label. Drop it or widen the window in 7.2. |
| **`/evaluation-results` returns 500, not 404** | ~~Returns 404 by design~~ — **wrong, corrected 2026-09-01.** A bare `except Exception` catches the deliberately raised `HTTPException(404)` and re-emits it as a 500 with the 404 text in the body. Same shape as the knowledge-graph bug: a broad except swallowing a meaningful error. Two-line fix (re-raise `HTTPException` before the generic handler), not applied — out of scope for 5.3. |
| Reranking | `COHERE_API_KEY` not set. `langchain_cohere` **is** importable, so `COHERE_AVAILABLE=True` and the Cohere path is attempted. Behaviour unverified with the current benchmark. |
| No keyboard navigation in the queue | No j/k or command palette. Expected in an operator tool. |
| No per-feature telemetry | Feature-usage chart derived deterministically from one adoption rate |
| Vercel builds red | Cosmetic. → 0.9 |

## Next — ordered by the critical path

**Phase numbers are identifiers, not a sequence.** They are referenced by commit
messages and ADRs, so they never change; the order does, and did. Read the
**Step** column for execution order.

The critical path is **point-in-time features → survival model → predictions
surfaced**. Everything else supports that or waits.

Phases 7, 4 and 5 are complete and their rows have moved to **Completed**. What
remains is listed below, in execution order, with cost stated.

### Cancelled

| # | Item | Why |
|---|---|---|
| 7.4 | Serve predicted date + interval from the API | **ADR-0009.** The date was retracted after the backtest — 4.36x hazard underprediction, dates 222 days late, structurally unfixable. `/customer/{id}/likelihood` serves a band and a lift instead. |
| 7.5 | Surface date + interval in the UI | **Partly.** The *date* is cancelled for the same reason. "Replace the heuristic" was live and unfinished — the queue still rendered a bare `~180d` from `np.interp` over the risk score. Closed separately, see Completed. |

5.4 was specified as *ARR × predicted horizon* and depended on 7.4. With no
horizon to multiply by it was delivered as **P(churn within a quarter) × ARR** —
the same question against a figure the data supports. The 7.4 dependency is
dropped, not outstanding.

### Remaining — free

Every zero-cost item in the *original roadmap* is complete. Two things sit
outside it, added 2026-09-02.

| Step | Item | Effort | Why |
|---|---|---|---|
| **1** | Ingest KKBox subscription data | ~1w | The one substantive gap, named when the FDE question was put directly on 2026-09-02. Every figure in the app currently comes from data this project generated, so the model looks good partly because the data was designed to make it look good. KKBox is real subscription data with **no churn column** — the label has to be derived, and the auto-renew and cancellation flags contradict the obvious definition. Having that argument, and defending the choice, is the FDE job; it is exactly the D3 failure `docs/SITE_SURVEY.md` warns about and the one thing this project cannot currently demonstrate. Size and file layout **not yet checked** — the week is an estimate, not a measurement. |
| **2** | `DEPLOYMENT_SPEC.md` Step 3 — staging | ½d | A branch and a second free Render service, so a broken change cannot reach the live site. Worth doing *before* the KKBox work, which is the first change large enough to break things. |

`DEPLOYMENT_SPEC.md` Step 1 (a real domain) is **~$19/year**, cosmetic, and
belongs in the paid list below in spirit — it changes nothing except what the
link looks like on a CV.

### Remaining — costs money

Excluded by the standing constraint. Listed so the decision is explicit rather
than the work being forgotten.

| Step | # | Item | Effort | Cost | Depends on | FDE roadmap |
|---|---|---|---|---|---|---|
| **2** | 3.1 | Full 65-question RAGAS baseline | ½d | **$2–5** | 4.4 | AI · Eval, inner loop |
| **3** | 3.2b | Answer-faithfulness gate (needs a judge) | ½d | $0 | 3.1 | AI · Eval, inner loop |
| **4** | 2.2a | `terraform apply`, verify, destroy | ½d | **$5–10** | 2.3 | P2 · Orchestration |
| **5** | 3.3 | OTel → CloudWatch / X-Ray | 1d | **<$1** | 2.2a | AI · Eval, outer loop |
| **6** | 3.4 | LLM-as-judge on sampled traffic | ½d | **$1–2** | 3.3 | AI · Eval, outer loop |
| **7** | 2.6 | Cognito user accounts (signup/login) | 2d | $0 | 2.2a | P2 · Enterprise security |

3.2, 2.6 cost nothing themselves but each depends on a paid step.

### Open defects — free, unscheduled

Found in passing and recorded rather than fixed, per the flag-don't-add rule.

| # | Item | Effort | Status |
|---|---|---|---|
| — | ~~`api.py` never calls `load_dotenv`~~ | 1 line | **Fixed** `6a377e8` — health 5/8 → 8/8 components |
| — | ~~`/evaluation-results` returns 500 where it intends 404~~ | 2 lines | **Fixed** `aea2a34` — also anchored its path to the repo root and dropped a stale "54 test questions" note |
| — | ~~No test covers the evidence layer's cross-account guarantee~~ | ½h | **Fixed** `aea2a34` — 10 tests; found and fixed a real BM25 IDF collapse |

**All recorded defects are closed.**

## Done 2026-09-02 (later)

| Item | Outcome |
|---|---|
| Vercel rewrite (`/api/*` → Render) | `docs/DEPLOYMENT_SPEC.md` Step 2, **shipped** (`85ba4c8`, merged `5a76b0e`). Was blocked while the cold start was 66s, since a proxy in front of that turns a slow first load into a broken one; unblocked at 2s. All five `NEXT_PUBLIC_BACKEND_URL` fallbacks removed. Verified on production: 0 occurrences of `onrender.com` across the served HTML and all 10 JS chunks. `BACKEND_ORIGIN` is resolved at **build** time into `routes-manifest.json`, so it must be a build-time variable on Vercel — set it at runtime only and every `/api/*` call silently proxies to localhost. |
| Parent ids survive a restart | **Regression I introduced and did not catch** (`b7de02b`). The 2026-09-02 index-reuse change kept the child vectors in Qdrant but not the parent docstore, which is process memory; the random parent UUIDs became unresolvable, `_bind_existing_collection` left `parent_retriever` as `None`, and `/ask` — which defaults to `parent_document` — raised on every question for roughly a day. Ids are now sha256 of the parent's own text, so a fresh process rebuilds the docstore for free and matches what is indexed. Cold start stays fast: 6.6s to bind and answer, no paid call. Cost one rebuild (~$0.005) since the old ids were unrecoverable. |
| CORS default flipped to deny | Removing `CORS_ALLOW_ORIGINS` from `render.yaml` hit a fallback of `allow_origins=["*"]`, which after the rewrite would have let any page on the web spend the LLM budget from a visitor's browser. Default is now no origins. CORS is not access control — curl ignores it — the rate limiter is what bounds spend. |
| Step 4: heartbeat | **Shipped** (`6c26fd6`). `.github/workflows/heartbeat.yml` probes production every 30 minutes and fails loudly; GitHub emails on a failed scheduled run, so there is no monitoring account and no cost. The endpoint change is the load-bearing half: `/ready` used to ask whether the retriever object existed, which was true throughout the outage. `readiness_problem()` now samples child chunks from the collection and confirms each parent id resolves in the docstore — the invariant a restart breaks — at the cost of one Qdrant scroll, no embedding, no LLM call. 30 minutes is deliberate: Render sleeps after ~15 idle, so the gap lets it sleep and makes nearly every probe exercise a cold start, which is the path the outage lived on, while using about half the 750 free instance-hours. A 15-minute cadence would keep it permanently awake and never test that path. |

### What the outage cost, and why nothing caught it

`/ask` was broken for about a day. All 85 tests passed throughout: the
cold-start work measured `/health` and never asked a question, and the health
check reported a different object than the one `/ask` uses.

`tests/test_parent_reuse.py` now holds seven tests (92 total). Three cover id
stability and the child-to-parent join; four cover the readiness probe,
including one that reproduces 2026-09-02 exactly — a populated index behind an
empty docstore. That last one is the test that turns this from a story into an
alarm.

Separately, the fix itself caused a ~20 minute index outage: the rebuild dropped
the collection before the upsert succeeded, and the free-tier cluster timed out
partway through at LangChain's default batch of 64 with no retry. Indexing is
now hand-rolled in batches of 32 with backoff. The sequencing was the real
error — deleting before having a working replacement.

**This is the argument for Step 4 (the heartbeat check).** It is free, and it is
the step that catches this class of failure.

## Deferred

| Item | Reason |
|---|---|
| 2.2a `terraform apply` | **Declined 2026-09-01 on goal, not cost.** The goal is a link a recruiter can open. The ALB is HTTP-only — HTTPS needs a certificate, which needs a domain — and browsers block an HTTPS page from fetching HTTP, so the deployed AWS backend could not have served the Vercel site. Render gives HTTPS on a free tier. Terraform remains written and validated as the IaC artifact; running it would prove orchestration and nothing else. |
| Precomputed static snapshot | Considered as a $0 alternative to hosting a backend at all — 2.2 MB of precomputed responses shipped with the site. Not needed once Render was chosen, since that keeps the AI question box working, which a snapshot cannot. Revisit if the free tier's cold starts prove unacceptable. |
| Duplicate `frontend` Vercel project | **Self-inflicted, resolved 2026-09-01.** Running `vercel build` from inside `frontend/` silently created and Git-linked a second Vercel project named after the directory. It had no Root Directory set, so it failed on every push while `churn-guard-ai` succeeded — one red X and one green on the same commit. Project deleted and `frontend/.vercel` removed. Lesson: `vercel build` is not read-only; it links. |
| 4.3 Vector migration off Qdrant | No target until Phase 2 deploys. OpenSearch Serverless has a 2-OCU minimum ≈ **$350/mo** for 137 KB — use pgvector (~$15/mo) or Qdrant Cloud free tier instead. |
| Phase 6 air-gap / edge | Only worth it for defence or regulated clients. |
| Knowledge graph rebuild | Superseded — ARCHITECTURE.md schedules deletion in 4.5, not a rebuild. |
| Distributed compute (Spark/Ray) | Roadmap item, deliberately skipped — ~~1.6 MB~~ → **2.0 MB** dataset (re-measured 2026-09-01, `du -sh data/`). Conclusion unchanged. |

## Re-derivation, 2026-08-31

The plan up to this point was a remediation backlog — it fixed what was broken
without deciding what the system should be. `docs/ARCHITECTURE.md` settled that:
the product predicts a churn date with a confidence interval, the warehouse is
authoritative for numbers, the vector store for narrative.

What changed:

| Change | Reason |
|---|---|
| **Phase 7 added** and placed first | The core prediction does not exist. `risk_score` is a weighted sum and `days_until_churn` is `np.interp` over it — never fitted to observed churn timing, so it cannot distinguish this quarter from next |
| **Phase 2 demoted** below 7, 4, 5 | Deploying a system whose central claim is unimplemented proves nothing. Terraform is written, so the cost of waiting is zero |
| **4.4 / 4.5 added** | Retrieval's role narrowed to explaining a specific prediction; four of five strategies and the knowledge graph become dead weight |
| **3.1 now depends on 4.4** | Measuring retrieval before its scope is settled measures the wrong thing — the mistake that produced the 94.7% claim |
| **5.4 now depends on 7.4** | Cost-of-inaction needs a predicted horizon to multiply ARR by. It was unbuildable as originally sequenced |
| **SQL router moved out of Rejected** | It was rejected as scope creep against the old plan. Under the architecture it is not a feature but the boundary between layers, and 7.4 subsumes it |

Nothing completed was invalidated. The dataset, warehouse, hybrid retrieval,
image and Terraform all stand; they are simply no longer the critical path.

## Rejected

| Item | Reason |
|---|---|
| Stage 7 UI phase | Proposed; not adopted. UI items tracked under Known gaps instead. |
| `SALESFORCE_COMPARISON.md` | Deleted 2026-08-31. Compared to Einstein on unsupportable numbers, including sales scripts asserting "we're 94.7% accurate". |
| Keeping the stale comprehensive doc live | Archived, not deleted — ADRs cite what it claimed. |
