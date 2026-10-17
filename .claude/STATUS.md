# Status

Living record. See the maintenance rule in `CLAUDE.md`.
Last updated 2026-10-16 · `feat/salesforce-integration` and `staging` @ `69c4af7` (unmerged) · `main` @ `a9a2e99`.

Phases re-derived against `docs/ARCHITECTURE.md` on 2026-08-31. The plan up to
that point was a remediation backlog; it is now ordered by the critical path to a
predicted churn date. See **Re-derivation** at the foot for what moved and why.

## In flight

**Positioning, decided 2026-10-04:** a production-grade churn product for B2B SaaS
companies. The reference customer is a Salesforce org plus a Mixpanel project, both
real services holding **seeded** data. "Production" describes the engineering
standard (real connectors, scheduled sync, monitoring, security). It is not a claim
of real customers, and nothing may be written as if it were (ADR-0007).

**Since 2026-10-15 the live site runs on the Salesforce data** (`fc64bb3`, production `CHURNGUARD_DATA_DIR=data/salesforce`; rollback is setting it back to `data`, a restart). The synthetic set is test data only. Verified live that day: source Salesforce, 27 at risk / $3.45M of 132 active, 294 accounts read → 187 customers, no invented fields, `/ready?require_ai_stack=true` ready with the AI stack off by design.

**Log in with Salesforce works on staging** (1.9, 2026-10-16, ADR-0012 Accepted). Connect, Test connection and Disconnect on the Integrations page against `churnguard-dev`; the refresh token is sealed under AWS KMS (`alias/churnguard-staging-master`, the project's first paid component at ~$1/month) and stored in Neon Postgres (free tier, branch `staging`); every action is audited. Operator actions are gated by a shared `OPERATOR_KEY` the preview's server attaches, the named stand-in until login (2.6). **Not on the live site:** production has no `DATABASE_URL`, so it reports connections off. The site's data still arrives by the laptop pull until 1.10.

**1.11 Operations, in flight** (started 2026-10-16). **1.11.1 built, awaiting staging:** `POST /connections/sync-due` runs every active connection whose last success is older than 24 h (`SYNC_INTERVAL_HOURS`); called by `.github/workflows/sync.yml` once a day with the operator key from a repository secret, and on every start of the backend. Defers under 20% API allowance (`sync_deferred` audit), backs off after `REQUEST_LIMIT_EXCEEDED` (1 h doubling, cap 12 h, jitter). The workflow fails loudly, and GitHub emails, on a connection needing a login or a failed or degraded run. Needs the `STAGING_OPERATOR_KEY` repository secret to be set by hand. **1.11.2 built, awaiting staging:** `GET /connections/{id}/metrics` (30 days: runs, success rate, records changed, API calls per day, median duration, last success) and a metrics block on the page; `attention` (a connection needing a login, or two failed or degraded runs in a row) on `GET /connections`, as `errors.sync` on `/health`, and as a banner on the page; every page's header now reads \"synced <date, time>\". Next: 1.11.3 record.

**The site runs on the sync** (1.10, 2026-10-16, see Completed). On staging every page shows the last promoted snapshot, restored from Neon on every start; the laptop pull is the fallback where no database or no promoted snapshot exists, which is production today.

**Live at https://churnguard.banik.dev** (2026-09-03).

| Piece | Where | Independent of the laptop |
|---|---|---|
| Domain | `banik.dev` at Porkbun, $8.75 then $12.87/yr. `churnguard` A → 76.76.21.21 | ✅ |
| Certificate | Let's Encrypt via Vercel, auto-renews, valid to 2026-12-02 | ✅ |
| Frontend | Vercel. `churn-guard-ai-nine.vercel.app` still resolves, deliberately | ✅ |
| Backend | Render free tier, reached only through the `/api` rewrite | ✅ |
| Data | Salesforce org `churnguard-dev` (seeded test org), pulled 2026-10-15 | ✅ |
| Vector index | Qdrant Cloud free tier, us-west-2 | ✅ |
| AI chat | Render + OpenAI key | ✅ |
| Staging | `churnguard-backend-staging.onrender.com` (free, branch `staging`, no OpenAI key) behind a Vercel preview of `staging`, login-gated | ✅ |

Verified on the new domain 2026-09-03: page 200 in 1.0s, `/api/health` healthy
and not degraded, `/api/dashboard-stats` returns $2,234,200 across 129 accounts,
and `onrender.com` appears **0 times** in the served page. **Not** verified on
that domain: a chat question. It uses the identical `/api` path, so it should
work — that is inference, not a measurement.

### The cold start, corrected

STATUS.md has carried **2s**, from a single measurement on 2026-09-02. That
number is wrong as a general claim. Measured 2026-09-03 after a long idle:
**two consecutive 150s attempts timed out before the third succeeded** — over
five minutes to wake. Warm, it answers in 1–2s.

The frontend gives up at 90s, so a visitor arriving cold sees "Backend
unreachable" rather than the app. This is now the **single largest gap between
the project and its stated goal** (a link a recruiter can open), and it is not a
code defect: startup work added by the parent-id fix measures 0.11s locally
(771 documents, 869 parents, all ids hashed), so the time is Render's own
container boot of a 1.11 GB image on a 0.1-CPU instance.

**Fix, not yet done:** an external pinger (cron-job.org or UptimeRobot, free)
hitting `/health` every 5 minutes so the service never idles into sleep. Needs
an account signup. Uses ~730 of Render's 750 free instance-hours per month,
which fits with little margin; a second free service there would exceed it, at
which point Render's $7/month always-on is the simpler answer. GitHub Actions is
**not** a substitute — its cron fired once in 4.5 hours when asked for every 30
minutes.

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
| Heartbeat | **first scheduled run failed on a false alarm, fixed 2026-09-03.** The cron asks for 30 min; it fired once in 4.5 hours. GitHub schedules are best-effort, so the real cadence is "a few times a day". The long idle meant a genuine cold start, which blew the single 120s attempt and reported a healthy backend as down. Now 3 attempts of 150s — a sleeping service is not a broken one. |
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
| **1.5a** | Salesforce connector; first pull from `churnguard-dev` | `61ea46c` | **First data in the project this repo did not generate itself.** 244 accounts read → 150 customers, 94 prospects excluded, 33 churned. 40 duplicate-name groups flagged (5 planted, 35 not). Raw pull gitignored; canonical CSVs and `ingest_report.json` tracked. Figures in ADR-0011. | P1 · Data quality, ingestion |
| **5.5** | Site survey of the Salesforce org | `8e9ab65` | **🔴 Red**, first survey against a real org (seeded data, marked as such). Binding reds D2 (1 week of usage) and V2 (33 events vs ~300 floor), not D3, so the scope becomes ranking, not prediction. D3 passes too neatly: status and opportunity churn dates identical on 28/28. 4 new known gaps. `docs/SITE_SURVEY_SALESFORCE.md` | P3 · Site Survey |
| **1.5b** | Contracts, dbt and scoring pass on Salesforce data | `84d54a8` | Contracts 22 pass + 1 fail + crash → **22 pass, 6 skipped**; dbt 37/83 → **83/83**; parity **139/139** on Salesforce. One data setting now picks the CSVs, warehouse and model file. Running parity per source exposed two latent scorer bugs (SQL NULL handling, Python NaN) that synthetic data never hit. | P1 · Data quality |
| **1.7** | Integrations page shows what the Salesforce connector did | `45df3ef` | The page listed six planned connectors and a disclaimer; it now leads with the one that exists. Every figure comes from `data/salesforce/ingest_report.json` via `GET /integrations/salesforce`: 244 accounts read, 92 prospects and 13 Salesforce demo records excluded, 139 customers, plus each decision with an example. `tests/test_integrations_report.py` reads every key the connector can record from its source and fails on any without a label. pytest 134 → 139. Verified on staging 2026-10-14. | P1 · Data quality, ingestion |
| **1.8** | Site runs on Salesforce data, and shows nothing invented | `2feab3a`, `cc5520c` | Staging serves Salesforce data on every page. Removed on every source because they were invented: per-feature usage (eight figures made up from one adoption rate, with `hash()` offsets that changed on restart), the heuristic churn date behind "median warning" and "~N days", and a `churn_probability` that was the score divided by 100. Where Salesforce cannot support a figure the API says so: likelihood and exposure return `available: false` (1 week of usage against 104 needed, `docs/SITE_SURVEY.md` V1); the assistant and evaluation are off on non-synthetic data. "At risk" is the top 20% of the active book (the fixed line of 60 flagged no Salesforce account). Queue loads every active account; each shows "In Salesforce since". `GET /source` and a data-source label on every page. pytest 139 → 160. Verified on staging 2026-10-15. Live site not switched. | P1 · Data quality, ingestion |
| **1.5c** | Second seeded batch; save plays and their outcomes | `b2df893` | `scripts/seed_salesforce.py` (committed, deterministic, tagged `ACC-300xx`, dry run by default) wrote 300 records to `churnguard-dev` with planted mess. The connector reads "Save play:" tasks and takes the first renewal after each as the outcome; "What has worked" reports renewals after each play, failures counted, ranked by the floor of a 90% Wilson interval. Outcomes are planted by the seed and the script states the rates. Exposed and fixed a connector gap: 16 usage readings after the churn date. Salesforce calls go through the CLI login (`sf api request rest`); no token in code. | P1 · Data quality, ingestion |
| **1.9** | Log in with Salesforce: connections and token custody | `ef513dc`, `2db7679`, `ffd50f2`, `3d5b6e1`, `356be17`, `e8ad528` | Connect, Test connection and Disconnect on the Integrations page, proven on staging 2026-10-16 against `churnguard-dev` (org `00Dg700000J62qbEAB`). OAuth web-server flow with PKCE and a single-use `state`; the host is checked by a parsed suffix rule (the org's real host carries a `develop.` label a one-label wildcard rejected) and pinned; the org is pinned. The refresh token is envelope-encrypted: AES-256-GCM under a per-connection data key, the data key wrapped by AWS KMS with an encryption context KMS enforces (a token sealed to one connection is refused for any other, verified against the real key). Refresh is single-flight under a Postgres advisory lock, rotated tokens re-sealed in place, `invalid_grant` moves the row to `needs_reauth`. Disconnect revokes at the org's own host: the first three disconnects posted to `login.salesforce.com` and Salesforce did not confirm, leaving three orphaned tokens (runbook); `revoke_status: 200` since `e8ad528`. Audit log append-only and secret-free by construction; a log filter on every handler redacts tokens and the callback's code. Found and fixed on the way: a refused-state audit row rolled back by the pool, uvicorn's access formatter broken by the first redaction filter, Neon's five-minute suspend killing pooled connections. pytest 160 → 237. `docs/RUNBOOK_SALESFORCE_CONNECTION.md`, `scripts/rewrap_tokens.py`. | P2 · Enterprise security; P1 · Ingestion |
| **1.10** | Sync engine: landing, candidate, promotion, rollback | `c0073c3`, `487718a`, `5c64360`, `00daa37`, `69c4af7` | Sync now on the Integrations page replaces the laptop pull on staging, proven 2026-10-16 against `churnguard-dev`. **1.10.1** reads Account, Opportunity, OpportunityCompetitor, Case and Task through the stored connection, full on first read and incremental on `SystemModstamp` with a five-minute overlap after that, lands every record by Id, keeps deletes as tombstones, and advances each object's watermark in the same transaction as its records; the run row is the lock (one unfinished run per connection, a second trigger joins it, abandoned runs swept after 30 minutes). First real run: 1,640 records in 5 API calls and 1.9 s; an unchanged org syncs in 5 calls and under a second. **1.10.2** transforms the landed records with the connector's `transform()`, runs the 28 data contracts (moved to `src/core/contracts.py`, shared with the script and CI) and stores a snapshot either way: pass → `succeeded`, fail → `degraded` with the contracts named, never served. The first snapshot matched the laptop pull table for table (187 / 239 / 278 / 48 / 171). **1.10.3** promotes a passing snapshot in the same transaction as its result (`serving` table, previous id kept, audited), writes it to `data/serving/<id>` and reloads the scorer, playbook and evidence; the same restore runs on every start because Render's disk is wiped. Dashboard figures identical from the restored snapshot and the files. **1.10.4** Roll back (pointer to the previous snapshot, a toggle, refused for a snapshot that failed its contracts), retention (last 10 snapshots never the serving pair, tombstones and disconnected connections' rows after 30 days, audited), ADR amendments for the three places the build differs from the text. Found on staging: the serving line did not refetch after a run finished. pytest 237 → 269. | P1 · Ingestion; P1 · Data quality |

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
| **7.2** | Discrete-time survival model | `7e1a664` | AUC ~~0.877~~ held out by customer, calibrated (2.26% predicted vs 1.93% actual), **not reproducible**, see Known gaps; 0.708 on the fixed-order model, 2026-10-14. Dates median 196d off — not usable. | AI · Modelling |
| **5.4** | Cost of inaction: exposure in dollars | `f05625e` | Expected loss ~~$1.21M/qtr (8.81% of ARR)~~ → $1.04M (7.55%, 2026-10-14). 12 accounts = ~~12.6%~~ 12.1% of ARR carry ~~69.4%~~ **74.8%** of it. Exposure order differs from likelihood order. | P3 · Artifacts |
| **5.2** | Site survey instrument + PRD | `d32e017` | Every survey item traced to a named code dependency. Minimum-data thresholds derived, not asserted: **9.5 events/feature**, ~300 events, 2yr weekly history. | P3 · Site survey, scoping |
| **7.5b** | Heuristic churn date removed from the queue | `b2e4cfe` | The queue showed `~180d` from `np.interp` over a heuristic score, unlabelled, on the screen operators work from. Replaced by the modelled band where one exists. | AI · Proof mechanisms |
| **0.9** | Vercel deploying green | settings change, no commit | Two settings, not one: **Framework Preset was `FastAPI`** (persisted from auto-detection) as well as Root Directory being unset. Fixing only the root directory left the build failing identically. Live at `churn-guard-ai-nine.vercel.app`, serving real data. | — |
| **5.3** | MVA (4 tiers, mermaid) + exec status report | `75b6a50` | Measured that **11 of 12 GET routes serve with no LLM key** — detect, value and explain need no model provider. Scoping now leads with the deterministic tiers. | P3 · MVA, exec reporting |
| **7.1** | Point-in-time features + survival labels | `4f01b1b` | 15,711 training rows, 284 hazard positives (1.81%). Leakage verified by rebuild-on-truncated-data. | P1 · Feature engineering |

## Current metrics

| Metric | Value | Measured | Source |
|---|---|---|---|
| Retrieval — single-entity hit rate (hybrid) | **0.971** | 2026-08-31, reproduced 2026-09-04 | `metrics/retrieval_benchmark.csv` |
| Retrieval — single-entity recall (hybrid) | **0.941** | 2026-08-31, reproduced 2026-09-04 | same |
| Retrieval — single-entity hit rate (naive) | 0.735 | 2026-08-31, reproduced 2026-09-04 | same |
| Retrieval — all-answerable recall (hybrid) | 0.609 | 2026-08-31, reproduced 2026-09-04 | same |
| Retrieval — single-entity MRR (hybrid) | **0.853** | 2026-09-04 | same — first recorded; the baseline now carries all three methods × two cohorts |
| Concurrency — 5 requests × 0.4s work | **0.42s** (was 2.03s) | 2026-08-31 | threadpool harness, 2.1 |
| Backend image size | **1.11 GB** | 2026-08-31 | `docker images` |
| Health scorer AUC vs churn label | **0.791** | 2026-08-30 | `CustomerHealthScorer.scorer_auc()` |
| SQL/Python scoring parity | 200/200 within 0.1 | 2026-08-30 | `tests/test_warehouse_parity.py` |
| Dataset contracts | 28/28 pass | 2026-08-31 | `scripts/validate_dataset.py` |
| dbt tests | **67/67 pass** | 2026-08-31 | `dbt test` |
| pytest | ~~45~~ → ~~65~~ → ~~75~~ → ~~80~~ → ~~85~~ → ~~94~~ → ~~107 (2026-09-04)~~ → ~~118 (2026-10-04, 1.5a)~~ → ~~128 (2026-10-04)~~ → ~~133~~ → ~~134~~ → ~~139~~ → ~~160 (2026-10-15)~~ → ~~237 (2026-10-16, 1.9)~~ → **269/269 pass** | 2026-10-16 | `pytest tests/` with `--extra model`, on `feat/salesforce-integration`. 14 of them need `DATABASE_URL` and run in a throwaway schema; CI skips those |
| Salesforce org: customers / churn events | ~~150~~ → ~~139 / 33 (2026-10-04)~~ → **187 / 55** | 2026-10-15 | `data/salesforce/ingest_report.json`; 139 accounts created 2026-10-04, 48 created 2026-10-15 (`crm_created_date`) |
| Contracts on Salesforce data | **22 pass, 6 skipped**, 0 fail; the same on every promoted sync snapshot | ~~2026-10-15~~ 2026-10-16 | `CHURNGUARD_DATA_DIR=data/salesforce scripts/validate_dataset.py`. Failed once that day (16 usage rows after churn) until the connector dropped them. Skipped = narrative-corpus checks |
| dbt on Salesforce data | **83/83** | 2026-10-15 | `dbt build`, warehouse `churnguard_salesforce.duckdb`, now also built in the image |
| SQL/Python parity | 200/200 synthetic, ~~139/139~~ → **171/171 Salesforce** (customers with a usage reading) | 2026-10-15 | `tests/test_warehouse_parity.py` |
| Salesforce org: weeks of usage history | **1** | 2026-10-04 | `scripts/survey_salesforce.py`. Blocks the survival model on this source |
| Salesforce at risk (top 20% of active book) | **27 accounts, $3,450,579 ARR** of 132 active | 2026-10-15 | `GET /dashboard-stats` on staging |
| Salesforce save plays | **48 read**: 16 renewed, 24 churned, 8 pending; 2 unrecognised, dropped | 2026-10-15 | `data/salesforce/interventions.csv`, `ingest_report.json`. Outcomes planted by `scripts/seed_salesforce.py` |
| Salesforce org: data storage | ~~1 MB of 5 MB left (2026-10-04)~~ → **5 of 5 MB used, full** | 2026-10-15 | `sf org list limits` (whole-MB granularity). No room for another batch |
| Training rows / hazard positives | 15,711 / 284 (1.81%) | 2026-08-31 | `main_gold.train_survival` |
| Best single feature (point-in-time) | AUC **0.769** `engagement_mean_4w` | 2026-08-31 | rank AUC vs `event_in_next_period` |
| Survival model — held out by customer | AUC ~~0.877 (2026-08-31)~~ → **0.708**, Brier 0.020 | 2026-10-14 | `scripts/train_survival_model.py`, reproducible since `68c81e1` |
| Survival model — held out by time | AUC ~~0.665 (2026-08-31)~~ → 0.631, Brier 0.046 | 2026-10-14 | same — underpredicts, see gaps |
| Predicted churn date accuracy | **196 days median abs error** | 2026-08-31 | 10 held-out churners, predicted 180d ahead |
| DuckDB vs Athena | 6/6 queries agree | 2026-08-30 | `warehouse/verify_athena.py` |
| Expected quarterly loss | ~~$1,208,213 (2026-09-01)~~ → **$1,035,495** (7.55% of ARR) | 2026-10-14 | `scripts/report_exposure.py` |
| Loss concentration — worst 10 accounts | ~~69.5% (2026-09-01)~~ → **72.2%** of expected loss | 2026-10-14 | same |
| Distinct calibrated probabilities (200 rows) | **12** | 2026-09-01 | `rank_book` on `train_survival` |
| Corpus size | 771 documents | 2026-08-31 | `ChurnDataLoader.get_all_documents()` |
| Backend cold start | ~~66s~~ → ~~2s~~ → **>300s cold, 1–2s warm** | 2026-09-03 | 2s was one sample after 17 min idle. After a multi-hour idle, two 150s attempts timed out before the third succeeded. Treat the warm and cold figures as different numbers. |
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
| ├ Data quality & observability | ✅ | 67 dbt tests + 28 contracts, the contracts also gating every promotion at runtime (1.10) |
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
| ├ Enterprise security | ✅ auth, rate limits, spend caps; OAuth + PKCE, KMS envelope encryption, audit log | 2.3, 2.7, ADR-0010; 1.9, ADR-0012 |
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
| **Salesforce app allows self-authorisation; connection runs as the admin user** | Deferred 2026-10-15 while the owner is the only user. Production setting is pre-authorised users plus a read-only permission set on an integration user (ADR-0012 §2). Must land before any org that is not the owner's connects. |
| **Operator key is a shared secret, not a login** | The ADR's named stand-in until 2.6. One key on Render and Vercel (Preview, branch `staging`); the browser never holds it. Rotation is in the runbook. |
| **Three orphaned refresh tokens at Salesforce** | The first three Disconnects (2026-10-16, 17:14–17:37) deleted the row but posted the revoke to `login.salesforce.com`, which Salesforce did not confirm. Live until the org's 30-day idle limit or a manual revoke (Setup → Users → OAuth Connected Apps). |
| **One `OAUTH_INVALID_NONCE` from Salesforce** | 2026-10-16 17:14:36, three seconds after a disconnect whose revoke failed: Salesforce redirected the popup back with `expired authorization code` before issuing one. Nothing stored. Did not recur in four later connects. Cause unknown; watch. |
| **Salesforce warehouse in the image lags the site** | The site serves the promoted snapshot; `warehouse/churnguard_salesforce.duckdb` is built from the committed pull at image build (dbt not in the serving image, see Deferred). Nothing on the site reads it for Salesforce today. Found 2026-10-16, 1.10.3. |
| **Rewrap script not yet run against the staging database** | Verified against the local provider and a real KMS re-wrap from a laptop; the staging rows have never been re-wrapped. |
| **No scheduled use of the connection** | The org expires a refresh token idle for 30 days. Until 1.11 schedules a sync, only a manual Test connection resets the timer. |
| **Model figures before 2026-10-14 are not reproducible** | Found 2026-10-14. Training depended on row order (early stopping holds out 15% of rows by position) and the warehouse returned rows unordered, so every build trained a different model. Same code and data gave 0.877, 0.773, 0.712 on unseen customers; one account moved from High (1.93×) to Low (0.74×). Fixed in `68c81e1`: rows sorted before fitting, and `tests/test_survival_determinism.py` fails if order matters again. Figures re-measured that day are marked so. **Still to re-run on the fixed model:** the walk-forward backtest (top band 27.8% vs 8.7%, 1.8× understatement, 4.36× underprediction, dates 222 days late), calibration (2.26% vs 1.93%), and the distinct-probability and calibrator-floor counts. |
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
| In-sample AUC 0.996 vs 0.877 held out (2026-08-31, not reproducible, not re-run) | Expected on grouped data — one customer contributes ~100 near-identical rows — but means in-sample metrics carry no information here. |
| `engagement_slope_4w` is noise | AUC 0.503 against the hazard label. Drop it or widen the window in 7.2. |
| **`/evaluation-results` returns 500, not 404** | ~~Returns 404 by design~~ — **wrong, corrected 2026-09-01.** A bare `except Exception` catches the deliberately raised `HTTPException(404)` and re-emits it as a 500 with the 404 text in the body. Same shape as the knowledge-graph bug: a broad except swallowing a meaningful error. Two-line fix (re-raise `HTTPException` before the generic handler), not applied — out of scope for 5.3. |
| Reranking | `COHERE_API_KEY` not set. `langchain_cohere` **is** importable, so `COHERE_AVAILABLE=True` and the Cohere path is attempted. Behaviour unverified with the current benchmark. |
| No keyboard navigation in the queue | No j/k or command palette. Expected in an operator tool. |
| No per-feature telemetry | Feature-usage chart derived deterministically from one adoption rate |
| Vercel builds red | Cosmetic. → 0.9 |
| ~~Salesforce: 11 sample accounts written as customers~~ **CLOSED by 1.5b** | The connector excludes prospects but not Salesforce's own sample records (Edge Communications, GenePoint, …). They carry no usage and 26 tickets dated 2026-09-27, the org's creation date, which land inside the 30-day ticket window. Found 2026-10-04 by 5.5. Fix belongs in 1.5b. |
| **Health score on a single snapshot: trend factor silently zero** | With one engagement row the slope is 0, so the 15% trend weight contributes nothing and every account reads "stable". Nothing in the output says so. Found 2026-10-04 by 5.5. | **2026-10-15:** still open; its visible effect (no Salesforce account above 60, so "0 at risk") is avoided by ranking the top 20% instead of a fixed line.
| ~~Salesforce `engagement_score` double-counts adoption~~ **CLOSED by 1.5b** — now seat utilisation only | The connector builds it as ½ utilisation + ½ adoption, and the scorer weights adoption again: effective weight 0.375, not 0.20. Found 2026-10-04 by 5.5. |
| ~~Two names for the data folder~~ **CLOSED by 1.5b** — `CHURNGUARD_DATA_DIR` drives the API, contracts and dbt via `src/core/datasource.py`; the warehouse and survival-model files follow it. `DATA_FOLDER` still read | The API reads `DATA_FOLDER`; the contracts and dbt read `CHURNGUARD_DATA_DIR` (added by 1.5a). Easy to point them at different data. Unify in 1.5b. |
| ~~No Salesforce model or warehouse in the image~~ **CLOSED by 1.8** | The image builds and tests the Salesforce warehouse, and the training script follows `CHURNGUARD_DATA_DIR`. No model is trained on Salesforce: 1 week of usage cannot support one, and the API says so (`available: false`) rather than erroring. Needs 1.6 and 7.6. |
| **Salesforce scorer AUC 1.0 is leakage** | Measured 2026-10-04 (`CustomerHealthScorer.scorer_auc()` on `data/salesforce`). The usage fields are the *current* snapshot, so a churned account's usage is read after it left: the score sees the outcome. **Do not quote it.** A fair figure needs point-in-time usage, so 0.2b must follow 1.6. |
| ~~SQL scorer treated missing CSAT as maximally unhappy~~ **CLOSED by 1.5b** | DuckDB's `least()`/`greatest()` skip NULL, so `coalesce(greatest(0, least(1, NULL)), 0.5)` gave 1.0, not 0.5: +7.5 risk on every account without CSAT. Python averaged an all-blank CSAT column to NaN and crashed. Neither showed on synthetic data, where every ticket is rated; found when parity was first run on Salesforce. |
| ~~Leftover from a mis-targeted dbt run~~ **CLOSED 2026-10-04**, deleted | `warehouse/churnguard_sf.duckdb` (gitignored) and `warehouse/target_sf/` (untracked) are from 2026-10-04, before the warehouse file followed the data setting. Safe to delete. |

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

Every zero-cost item in the *original roadmap* is complete. What follows was decided
2026-10-14: **every number the site shows comes from a connected integration**, and the
synthetic data becomes test data only. It builds on the Salesforce engagement
re-planned 2026-10-04 after 5.5.

**Done when** every figure on churnguard.banik.dev traces to an integration pull, and
the Integrations page shows each source, its last sync, and what was cleaned.

| Step | Item | Effort | Why |
|---|---|---|---|
| 1 | 1.5b Contracts and dbt on the Salesforce data — **done, see Completed** | 1d | Run the 28 contracts and `dbt build` with `CHURNGUARD_DATA_DIR=data/salesforce`; report failures before changing anything. Then the fixes 5.5 found: exclude the 11 Salesforce sample accounts, one name for the data folder (`DATA_FOLDER` vs `CHURNGUARD_DATA_DIR`), and stop the connector double-counting adoption. 1.5a and 5.5 are done (`61ea46c`, `8e9ab65`). |
| 2 | `DEPLOYMENT_SPEC.md` Step 3 — staging — **done 2026-10-14** (`6b44b92`) | ½d | Verified end to end 2026-10-14: the Vercel preview of `staging` answered `/api/health` with `OPENAI_API_KEY is not set`, which only the staging backend can say, so the per-branch `BACKEND_ORIGIN` routes to it. Production unchanged: same Vercel deployment as 38 days earlier, backend healthy with AI up. Two things found: Vercel did **not** build `staging` on push, because the commit had already been built for `feat/salesforce-ingest` (triggered by API from the GitHub branch instead); and the preview is login-gated, so checking it needs a logged-in browser. To use it: push to `staging`, Render and Vercel both rebuild. First use, 2026-10-14: staging switched to `CHURNGUARD_DATA_DIR=data/salesforce` and failed as predicted. Scorer, playbook and evidence work on the 139 Salesforce customers; `survival_model` and `exposure` are down (`no model at models/survival_salesforce.joblib`). Staging is left on the Salesforce data so later work shows up there. See Known gaps. |
| 3 | 1.7 Integrations page as the hub, **done 2026-10-14, see Completed** | ½d | Salesforce panel from the connector's own report. |
| 4 | 1.8 Site runs on Salesforce data, **done 2026-10-15, see Completed** | ½–1d | Every page on Salesforce data, nothing invented. |
| 5 | 1.9 Log in with Salesforce, **done 2026-10-16, see Completed** | ~~1–1.5d~~ 3d | Built to ADR-0012 rather than the thin slice first sketched here: token custody, not a token discarded after the pull. |
| 6 | 1.10 Sync engine, **done 2026-10-16, see Completed** | ~~2–3d~~ 1d | ADR-0012 §5–6: sync runs with watermarks and tombstones through the stored connection, raw landings and snapshots in the Postgres 1.9 added, contract-gated atomic promotion of what the site serves. Replaces the laptop pull. |
| **7** | **1.11** Operations | 1–2d | ADR-0012 §8: scheduled sync (also the keep-alive, and what resets the org's 30-day idle limit), run metrics on the Integrations page, "last synced" on every page, alerts on `needs_reauth` and failed runs. |
| 8 | 1.6 Product usage from Mixpanel | 2d | Closes 5.5's D2 red: the CRM holds one usage snapshot, and weekly usage lives in product analytics in a real SaaS stack. Seed ~2.5 years of weekly usage for the customer accounts (seeded, and labelled so), connector joined to Salesforce on `AccountNumber`, every pull kept in our own warehouse. Mixpanel Free verified 2026-10-04, re-verified 2026-10-14: events backdated 1, 2 and 3 years imported and returned by the export API (`cg_retention_probe`, `scripts/mixpanel_retention_probe.py`; the first run was by hand and left nothing in the repo). PostHog Free rejected for this: 1-year query retention. |
| 9 | 4.7 Dated risk signals from CSM notes | 1d | LLM extraction over 157 task notes and 233 case descriptions (competitor mentioned, champion left, cancellation asked about) as time-varying features, not only narrative. < $0.05. Optional, and the only item that spends. |
| 10 | 7.6 Survival model sized to the events | 1–2d | 33 churn events support ~3 features at ten events per feature. Features fixed in advance, penalised discrete-time model, grouped walk-forward validation with bootstrap intervals, compared against the CSMs' own `At Risk` flag (26 accounts). Depends on 1.6. |
| **11** | **1.12** Synthetic data moves to `tests/fixtures/` | ½d | Once nothing on the site reads it. |

**Open, decided at their step:**

- What a visitor sees before any connection. Proposed: the last `churnguard-dev` sync,
  labelled as seeded data (ADR-0007).
- Whether Log in with Salesforce appears on the live site. Options: not on live, an
  admin key, or real accounts (2.6). **2026-10-16:** on staging it is gated by a
  shared operator key the preview's server attaches; the live site gets it only
  with its own Neon branch, KMS key, IAM user and operator key, and the decision is
  still open.

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
| — | ~~The Evaluation page named a script whose output no endpoint could read~~ | 2h | **Fixed** `9b61681` — three defects in series: `benchmark_retrieval.py` wrote nothing, `/evaluation-results` parsed only RAGAS columns the benchmark does not measure, and the card's "costs nothing" claim was wrong because the default collection forced a full re-embed of all 771 documents. Following the instruction on screen changed the screen not at all. tests/ 94 → 107 |

| — | ~~A cold visitor saw "Backend unreachable", which was never true~~ | 2h | **Fixed** `ebb5578` — the backend was healthy and asleep. Two ceilings sat under the cold start and only one was ours: our own 90s fetch timeout, and **Vercel's ~120s proxy timeout**, measured 2026-09-06 as a 502 at exactly 120.19s. Raising our timeout past 120s only moves the failure to a gateway 502, which is what I twice proposed before testing a cold request through the proxy. Replaced with short polling probes (8s each, 3s apart, 7-minute deadline) behind a `BootGate`, so no single request approaches the ceiling |

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

- **Production on the sync, and the operator buttons on the live site** (decided 2026-10-16, after 1.11.1). Production keeps the committed pull and no `DATABASE_URL` until 2.6 (login) exists; the buttons stay off the public site because the operator key would be the only thing between the internet and Connect, Sync, Roll back and Disconnect (ADR-0012 §8). Provisioning production like staging is a second Neon branch, a second KMS key (~$1/month), an IAM user, an operator key held only on Render and in a GitHub secret, and the production callback URL; recorded here so it is a half-hour of clicks when 2.6 lands. Also noted: the daily workflow's schedule fires from `main` only, so the merge is what turns the schedule on.
- **dbt tests at promotion time** (decided 2026-10-16, 1.10.2). ADR-0012 §5 gates promotion on the contracts *and* the dbt tests. The serving image does not carry dbt (about 200 MB more, memory on the 512 MB instance unmeasured), so the 28 data contracts gate promotion at runtime and dbt keeps gating every image build. Consequence recorded under Known gaps: the DuckDB warehouse in the image is built from the committed pull, not the promoted snapshot. Revisit when something on the site reads the Salesforce warehouse (today nothing does: no survival model exists for that source).

| Item | Reason |
|---|---|
| Present the project as a 0–6 FDE loop, plus agent least-privilege, an agent SQL tool over gold views, and an agent audit log | **Parked 2026-10-14**, not declined. Raised after comparing with a reference FDE project (legacy source → secured semantic view → agent with tools → audit log → UI → deploy). The three agent items are real gaps: nothing scopes the agent to clean views, it cannot query the warehouse, and no step it takes is recorded. Best after 1.8, so the agent works on integration data. |
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
| KKBox subscription data | **Rejected 2026-10-04.** Was Step 1 from 2026-09-02, to get prediction onto data this project did not design. KKBox is consumer music subscriptions, and the project is positioned as churn for B2B SaaS companies; its unit (a ~$5 subscriber) has no ARR, seats, CSM or save motion, so it could prove the model but not the product. The same gap is now closed on the B2B stack: Salesforce for the CRM (1.5) and Mixpanel for usage history (1.6). |
| `SALESFORCE_COMPARISON.md` | Deleted 2026-08-31. Compared to Einstein on unsupportable numbers, including sales scripts asserting "we're 94.7% accurate". |
| Keeping the stale comprehensive doc live | Archived, not deleted — ADRs cite what it claimed. |
