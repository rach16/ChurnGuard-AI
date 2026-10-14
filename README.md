# ChurnGuard AI

**Live: [churnguard.banik.dev](https://churnguard.banik.dev)** · [Architecture](docs/ARCHITECTURE.md) · [PRD](docs/PRD.md) · [Site survey](docs/SITE_SURVEY.md) · [ADRs](docs/adr/)

A retention platform for B2B SaaS customer success teams: a dbt warehouse over
engagement data, a survival model over customer-weeks, hybrid retrieval over the
supporting documents, and LangGraph agents that turn all three into a defensible
weekly queue.

**This is a demonstration project.** The customer data is synthetic and generated
by a script in this repository. Every number below names the script that produced
it and the date it was taken. Where something has not been measured, it says so.

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14.2+-000000?logo=next.js&logoColor=white)
![dbt](https://img.shields.io/badge/dbt-1.11-FF694B?logo=dbt&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-1.1+-FFF000?logo=duckdb&logoColor=black)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-1C3C3C)

---

## 1. Users and the workflow this replaces

Four people, and only one of them opens the app every week. Full treatment in
[docs/PRD.md](docs/PRD.md#2-users).

| User | What they do today | What this changes |
|---|---|---|
| **CS manager** (primary) | Maintains a spreadsheet of accounts sorted by renewal date, re-sorted by whoever complained most recently | A queue ordered by expected loss, with a reason attached to each row |
| **CSM** | Walks into a save call with a number and no evidence for it | Walks in with the passages the number came from, and what worked on comparable accounts |
| **VP CS** (secondary) | Quotes a churn percentage in a board deck and hopes nobody asks how it was derived | Quotes a dollar figure they can trace to a SQL query |
| **Data team** (gatekeeper) | Distrusts any number computed inside somebody else's application | Reads the dbt models; every published number is queryable without the app |

The gatekeeper is the one most often forgotten and the one most able to end an
engagement. That is why scoring exists in SQL as well as Python, and why a parity
test fails the build when the two disagree.

**The trigger.** A customer success team usually learns an account is leaving when
the account tells them. By then it is a save-or-lose negotiation, and the levers
that would have worked a quarter earlier are gone.

## 2. Problem, baseline, and non-goals

**The decision is not "will this account churn".** It is where to spend a limited
number of CSM hours this quarter. A ranked list is a report; the team already
knows most of the names on it.

**Baselines this has to beat**, and does:

| Baseline | Measure | This system | Source |
|---|---|---|---|
| Churn base rate over the population | 8.7% | **27.8%** in the top predicted band | walk-forward backtest, 2026-08-31 |
| Dense-embedding retrieval alone | 0.735 single-entity hit rate | **0.971** with hybrid | `metrics/retrieval_benchmark.csv`, 2026-09-04 |
| "Sort accounts by ARR" | orders by size | orders by **expected loss**; the two orders differ | `scripts/report_exposure.py`, 2026-09-01 |

**Explicit non-goals.** Each of these was considered and declined, not overlooked:

- **Not a CRM.** It reads engagement data; it does not own the customer record.
- **Not multi-tenant.** One dataset, one warehouse, one vector collection. See
  §11 for what turning this into a product would actually cost.
- **No predicted churn date.** Retracted after backtesting, see
  [ADR-0009](docs/adr/0009-predict-a-horizon-band-not-a-date.md). The model ranks
  well and its dates were 222 days late. A band and a lift are served instead.
- **Not a replacement for CSM judgement.** Every recommendation cites the accounts
  it came from so the operator can disagree with it.
- **No PII handling.** The synthetic data carries none, and nothing in the
  pipeline is built to protect it. A real deployment needs that work first.

## 3. Why this technical approach

| Decision | Alternative rejected | Why | ADR |
|---|---|---|---|
| Discrete-time survival over customer-weeks | Binary classifier on a snapshot | A classifier answers "at risk" with no horizon; the decision needs "by when, roughly" | [0008](docs/adr/0008-predict-churn-date-with-survival-analysis.md) |
| Hybrid BM25 + dense retrieval | Dense embeddings alone | Embeddings match the *shape* of a question and ignore named entities. "How many tickets has DisasterRecovery Solutions raised" returned five unrelated customers. BM25 has the opposite bias, and a company name is exactly the rare exact term it is good at | — |
| Scoring in **both** Python and SQL | One implementation | The data team will not adopt a number they cannot query. Two implementations are only acceptable when something enforces agreement, so `tests/test_warehouse_parity.py` recomputes all 200 customers each way | [0005](docs/adr/0005-scoring-in-both-python-and-sql.md) |
| DuckDB locally, Athena in the cloud | Warehouse-native only | The same dbt models run on a laptop and in S3, so the pipeline is reviewable without cloud credentials | [0006](docs/adr/0006-duckdb-locally-athena-for-portability.md) |
| One backend with a degraded mode | Separate "demo" and "full" services | Two backends drift. 11 of 12 GET routes serve with no LLM key at all | [0003](docs/adr/0003-single-backend-with-degraded-mode.md) |
| Golden set derived from the data | LLM-generated questions | The previous LLM-generated set referenced companies present in no data file, and produced a retracted accuracy claim | [0002](docs/adr/0002-derive-golden-set-from-data.md), [0007](docs/adr/0007-retract-the-94-7-percent-claim.md) |

**The layering rule, in one line:** the warehouse is authoritative for every
number, the vector store for narrative only. Retrieval explains a prediction; it
never produces one.

## 4. Data, control, and evidence flows

```
                          ┌──────────────────────────────────────┐
 DATA        CSV source ─▶│ dbt: bronze → silver → gold          │─▶ DuckDB / Athena
                          │ 14 models · 67 tests · 28 contracts  │
                          └───────────────┬──────────────────────┘
                                          │ point-in-time features
                                          ▼
                              survival model (15,711 customer-weeks)
                                          │
 CONTROL     Browser ──▶ Next.js ──▶ /api/* (same-origin proxy) ──▶ FastAPI
                                                                     │
                                    ┌────────────────────────────────┤
                                    ▼                                ▼
                            deterministic routes              LLM routes
                            (11 of 12 GETs,                   (rate-limited,
                             no key needed)                    budget-capped)

 EVIDENCE    question ─▶ hybrid retrieval ─▶ passages scoped to ONE customer_id
                                                  │
                                                  ▼
                                         answer + the passages it used
```

**Data flow.** CSV → dbt bronze/silver/gold → DuckDB (and S3 + Athena from the
same definitions) → point-in-time features → survival model. The gold layer is
what both the API and any SQL client read.

**Control flow.** The browser only ever talks to its own origin. `/api/*` is
rewritten server-side to the backend, so the backend's address never appears in
the JavaScript bundle and no request crosses origins. Verified rather than
assumed: the backend host appears zero times across the served HTML and all ten
JS chunks.

**Evidence flow.** Every explanation is keyed by `customer_id` before retrieval,
not filtered after, so one account's story cannot surface in another's page.
`tests/test_evidence.py` asserts that boundary.

## 5. Permissions and threat model

This runs as a **public demo with no login**, which is a deliberate constraint:
you cannot ask a stranger evaluating a portfolio project for a credential. That
choice moves the entire defensive burden onto spend limits and blast radius.

| Threat | Control | Where |
|---|---|---|
| Cost exhaustion via the LLM route | Per-visitor hourly limit **and** a shared daily budget. A per-caller limit caps one person, not a crowd. ~$0.20/day ceiling | `src/backend/security.py`, 25 tests in `tests/test_security.py` |
| Runaway generation | Output capped at 1,024 tokens in one factory, not at six call sites | `src/core/llm.py` |
| Oversized prompts | 2,000-character question cap | `render.yaml` |
| Cross-account data leakage | Evidence scoped by `customer_id` at retrieval time, asserted by test | `src/core/evidence.py` |
| Browser-side origin abuse | CORS defaults to **deny**, not `*`. Note that CORS is not access control: it stops a browser, not `curl`. The rate limits are what actually bound spend | `src/backend/api.py` |
| Backend address disclosure | Same-origin proxy; `BACKEND_ORIGIN` has no `NEXT_PUBLIC_` prefix so it never reaches the client bundle | `frontend/next.config.js` |
| Secrets in a public repo | Every credential is `sync: false` in the blueprint and set in the host dashboard. Deploy authenticates by OIDC, so no long-lived AWS key exists in CI | `render.yaml`, `.github/workflows/` |

**API key auth and per-process rate limiting** are implemented and wired into the
ECS task definition ([ADR-0010](docs/adr/0010-api-key-auth-and-per-process-rate-limiting.md)),
and deliberately left unset on the public demo.

**What is not defended.** Prompt injection through corpus documents is not
mitigated: the corpus is synthetic and trusted, and a real deployment ingesting
customer-authored text would need that work. There is no audit log, no per-user
authorisation, and no tenant isolation.

## 6. Evaluation set and failure taxonomy

**65 questions**, derived from the data rather than generated by a model, so
every one names entities that actually exist.

| Query type | n | | Difficulty | n |
|---|---|---|---|---|
| customer_specific | 32 | | easy | 22 |
| segment_analysis | 11 | | medium | 35 |
| pattern_analysis | 8 | | hard | 8 |
| competitive_intelligence | 8 | | | |
| financial_analysis | 6 | | | |

Retrieval is scored against each question's recorded `expected_context` — ground
truth, not a judge model's opinion — so the benchmark costs only the query
embeddings and can gate CI.

**Failure taxonomy.** Named modes, each with a measurement or a test:

| Failure | Shape | Status |
|---|---|---|
| **Aggregate questions** | 27 of 65 name more than three contributing customers ("total ARR lost to churn"); 4 more have no expected context at all. The answer is a computation over the whole dataset, present in no document | Scored separately, never averaged in. Belongs in SQL routing, not built |
| **Entity blindness** | Dense retrieval matches question shape and drops the company name | Fixed by hybrid fusion, 0.735 → 0.971 |
| **BM25 IDF collapse** | On a small corpus a term appearing in one of two passages scores zero, so a customer with few passages silently gets no evidence | Found by a ranking test, fallback pinned in `tests/test_evidence.py` |
| **Coarse parent hits** | `parent_document` returns whole parents, so a hit is broad and a miss is total. It scores *below* naive on hit rate (0.706 vs 0.735) | Visible in the committed baseline rather than explained away |
| **Docstore / vector desync** | Child vectors persist in Qdrant; the parent docstore lived in process memory and died with the container. Random UUIDs joined them, so after a restart every vector pointed at a parent that could not be rebuilt | Fixed with content-derived SHA-256 ids; probe in `/ready` and 9 tests in `tests/test_parent_reuse.py` |
| **Green tests, broken product** | The above broke `/ask` for a day while all 85 tests passed. Nothing in the suite asked the product a question | New tests assert the join between components, not each half |

## 7. Run it locally, and the test evidence

```bash
git clone https://github.com/rach16/ChurnGuard-AI.git
cd ChurnGuard-AI
uv sync
cp .env.example .env     # add OPENAI_API_KEY for the AI routes; optional
```

```bash
uv run python src/backend/api.py                 # API on :8000
cd frontend && npm install && npm run dev        # UI on :3000
```

Dashboard at `http://localhost:3000`, API docs at `http://localhost:8000/docs`.
The frontend proxies `/api/*` to `BACKEND_ORIGIN`, resolved **at build time** and
frozen into the routes manifest, so changing it requires a rebuild rather than a
restart.

**The full check, all of it free and offline:**

```bash
uv run --extra dev --extra warehouse pytest tests/     # 108 tests
python3 scripts/validate_dataset.py                    # 28 data contracts
cd warehouse && DBT_PROFILES_DIR=$PWD uv run --project .. dbt build   # 67 dbt tests
uv run python scripts/benchmark_retrieval.py           # retrieval; query embeddings only
```

| Suite | Result | Measured |
|---|---|---|
| pytest | **108 / 108** | 2026-09-04 |
| dbt tests | **67 / 67** | 2026-08-31 |
| Data contracts | **28 / 28** | 2026-08-31 |
| SQL ↔ Python scoring parity | 200 / 200 within 0.1 | 2026-08-30 |
| DuckDB ↔ Athena | 6 / 6 queries agree | 2026-08-30 |

CI runs contracts, the warehouse and the test suite on every push, plus a
retrieval regression gate that fails the build if hit rate degrades. The gate is
keyword-only, so it costs **$0 per run** — verified by confirming it fires on a
deliberately degraded retriever and not on a working one.

## 8. Fault injection and fallback

Most degradations below are exercised by a test that constructs the fault rather
than asserted in prose. The last three are not, and say so.

| Fault injected | Fallback | Covered by |
|---|---|---|
| Missing provider credentials | Named explicitly rather than swallowed; the embedding provider never falls back silently to a different vendor | `tests/test_llm_provider.py` |
| Rate limit exhausted | 429 with `Retry-After` pointing past the oldest hit; the message says which routes still work | `tests/test_security.py` |
| Daily budget exhausted | Whole-deployment cap, not per-caller, so a crowd cannot walk past it | same |
| Parent docstore empty after restart | Probe reports the broken join and says why; `ai_ready` is false rather than optimistically true | `tests/test_parent_reuse.py` |
| Vector collection empty | Probe catches it before a question does | same |
| Vector store times out mid-index | Batched upserts (32) with 4 attempts and exponential backoff, after a free-tier timeout at LangChain's default batch left the collection with zero points | `src/core/rag_retrievers.py` |
| Corpus too small for BM25 | IDF-collapse fallback returns evidence instead of nothing | `tests/test_evidence.py` |
| Corrupt evaluation baseline | 500 naming the file, never a silent "nothing measured yet" | `tests/test_evaluation_baseline.py` |
| No `OPENAI_API_KEY` | Degraded mode: 11 of 12 GET routes still serve from CSV and LLM routes return 503 naming the dependency | **implemented, not tested** — see §10 |
| No `COHERE_API_KEY` | Reranking unavailable; the fallback currently scores 0.0 recall — a real gap, not a graceful one | §10 |
| Backend unreachable | UI distinguishes **degraded from offline**; a 503 on one route does not claim the whole backend is down | `frontend/src/app/ask/page.tsx` |

**A scheduled heartbeat** probes the live site every 30 minutes and requires the
`retrieval_join` key to be *present and true* — an absent key previously read as
`None` and printed "retrieval join intact" against a backend with no probe at
all. Also worth knowing: GitHub Actions `schedule:` is best-effort. Asked for
every 30 minutes, it once fired once in 4.5 hours.

**What is not injected.** There is no systematic chaos testing: no network
partition, no latency injection, no dependency-failure matrix. The faults above
are the ones that actually occurred and were then pinned.

## 9. Results

| Metric | Value | Measured | Source |
|---|---|---|---|
| Retrieval — single-entity hit rate (hybrid) | **0.971** | 2026-08-31, reproduced 2026-09-04 | `metrics/retrieval_benchmark.csv` |
| Retrieval — single-entity recall (hybrid) | **0.941** | same | same |
| Retrieval — single-entity hit rate (naive) | 0.735 | same | same |
| Survival model — ranking, held out by customer | AUC **0.877**, Brier 0.019 | 2026-08-31 | `scripts/train_survival_model.py` |
| Survival model — top band vs base rate | **27.8%** vs 8.7% | 2026-08-31 | walk-forward backtest |
| Health score vs churn label | AUC **0.791** | 2026-08-30 | `CustomerHealthScorer.scorer_auc()` |
| Expected quarterly loss | **$1,208,213** (8.81% of ARR) | 2026-09-01 | `scripts/report_exposure.py` |
| Loss concentration — worst 10 accounts | **69.5%** of expected loss | 2026-09-01 | same |
| Concurrency — 5 requests × 0.4s work | **0.42s** (was 2.03s) | 2026-08-31 | threadpool harness |
| Backend cold start | **>300s cold, 1–2s warm** | 2026-09-03 | free-tier measurement |

The live evaluation baseline is visible at
[churnguard.banik.dev/evaluations](https://churnguard.banik.dev/evaluations),
with the date it was measured shown beside it.

## 10. Known limitations

Tracked, not hidden. The full register is in [`.claude/STATUS.md`](.claude/STATUS.md).

- **Predicted churn dates are retracted.** 222 days late, structurally
  unfixable at this hazard level. A band and a lift are served instead.
- **Absolute probability underpredicts roughly 2×.** The hazard rate rises across
  the window and a model fitted on an earlier period cannot know that. Mitigated
  by reporting a lift, which is invariant to a level error.
- **Only 12 distinct probabilities across 200 rows.** Isotonic calibration maps
  whole input regions to one level, so the "High" band is empty by construction
  and 12 accounts share `p=0.574`. Ordering inside a band is driven entirely by
  ARR — fine for a work queue, but it is not model signal.
- **Degraded mode is implemented but not asserted by any test.** No test in
  `tests/` contains a 503, and none exercises the service with the LLM stack
  absent. The behaviour is real and manually verified; nothing stops a refactor
  from silently removing it. This is precisely the gap that let `/ask` fail for
  a day with a green suite, and it is the next test worth writing.
- **Aggregate questions are unanswerable by retrieval** and no SQL routing exists.
- **Reranking needs `COHERE_API_KEY`**; the fallback scores 0.0 recall.
- **Cold start exceeds the frontend's 90-second timeout.** A first visitor to a
  sleeping free-tier instance can see "backend unreachable". This is a hosting-tier
  fact, not a code defect, and the fix is a keep-alive pinger.
- **The data is synthetic with no ingestion path.** This is the largest gap between
  this project and something a customer could use.
- **Single-tenant by construction**, on 512 MB and 0.1 CPU.
- No keyboard navigation in the queue.

## 11. What I would do next in a real customer environment

In order, and none of it speculative — each item names the constraint it removes.

1. **Run the site survey before writing code.**
   [`docs/SITE_SURVEY.md`](docs/SITE_SURVEY.md) derives its minimum-data
   thresholds from this build rather than from a textbook: **9.5 events per
   feature**, ~300 events, two years of weekly history. **D3 — churn events with
   dates — is the question that most often ends the conversation**, because most
   CRMs record that an account closed and not when it decided to. A red on D3
   does not end the engagement; it changes the deliverable to ranking today
   rather than prediction.
2. **Ingest one real dataset before promising anything.** Every figure here comes
   from data this project generated, so the model looks good partly because the
   data was designed to make it look good. Real subscription data has no churn
   column; the label has to be derived, and auto-renew and cancellation flags
   routinely contradict the obvious definition. Having that argument and
   defending the choice is the job.
3. **Bring-your-own-data ingestion** — upload or connect, map their columns to the
   canonical schema, run the existing 28 contracts as a gate, and degrade
   honestly when a table is missing. A customer with no engagement telemetry
   should see the dashboard work and health scoring say precisely what it needs,
   not return zeros.
4. **A staging environment** before the first change large enough to break the
   live site.
5. **Authentication and per-tenant isolation**, which the current 512 MB single
   process cannot support and which no amount of application code fixes.
6. **Observability with traces**, then LLM-as-judge on sampled real traffic. Two
   defects in this project were found by reading logs and none by the test suite;
   that ratio is the argument.
7. **Cost and latency budgets agreed up front.** The daily budget here is $0.20.
   In a customer environment that number is a conversation with a finance owner,
   not a constant in a config file.

## Layout

```
src/
├── backend/api.py           FastAPI — the single entrypoint, 16 routes
├── core/
│   ├── health_scoring.py    weighted risk model (mirrors the SQL)
│   ├── rag_retrievers.py    retrieval strategies over Qdrant
│   ├── evidence.py          per-customer explanation, scoped at retrieval
│   ├── exposure.py          expected loss in dollars
│   └── plays.py             recommendations from recorded outcomes
├── model/survival.py        discrete-time survival model
├── agents/                  LangGraph research + writing teams
└── evaluation/              RAGAS harness

warehouse/                   dbt — bronze / silver / gold, incl. point-in-time features
infra/                       Terraform for ECS Fargate (written, validated, never applied)
scripts/                     generation, validation, retrieval benchmark, S3 publish
frontend/                    Next.js 14 operator console (shadcn/ui, light + dark)
metrics/                     committed evaluation baseline
docs/adr/                    10 architecture decision records
```

## Design decisions

Ten decisions are recorded in [docs/adr/](docs/adr/) — what forced each one, what
it cost, and what was rejected. Notably why the 94.7% claim was retracted, why a
predicted date was replaced with a band, and why fixing a long-silent
knowledge-graph bug would have made the system worse than deleting it.

## Licence

MIT — see [LICENSE](LICENSE).
