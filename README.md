# ChurnGuard AI

A customer-churn analysis platform for B2B SaaS: a dbt warehouse over customer
engagement data, a RAG pipeline over the supporting documents, and a multi-agent
system that turns both into retention recommendations.

**This is a demonstration project.** The customer data is synthetic and generated
by a script in this repository. Every number below is reproducible from the code —
where something has not been measured, it says so rather than guessing.

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14.2+-000000?logo=next.js&logoColor=white)
![dbt](https://img.shields.io/badge/dbt-1.11-FF694B?logo=dbt&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-1.1+-FFF000?logo=duckdb&logoColor=black)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-1C3C3C)

---

## What it does

| | |
|---|---|
| **Predicts when a customer will churn** | Discrete-time survival over 15,711 customer-weeks. Hazards chain into a survival curve; its median is the date, its quartiles the interval. Ranks unseen customers at **AUC 0.877**. |
| **Scores retention risk today** | A documented weighted model over observed engagement, adoption, trend, support volume and CSAT. Ranks actual churners at **AUC 0.791**. |
| **Answers questions over the corpus** | RAG across 771 documents — customer profiles, churn analyses, success stories, support and interaction history. |
| **Generates retention plans** | LangGraph agents combine the score, retrieved context and an LLM into specific recommendations. |
| **Publishes to a warehouse** | dbt models land in DuckDB locally and S3 + Athena in the cloud, from one set of definitions. |

## Honest status

Being explicit about what is and isn't proven, because an earlier version of this
README was not.

| Claim | Status |
|---|---|
| Health score separates real churners | ✅ AUC **0.791**, asserted in `tests/test_warehouse_parity.py` |
| SQL and Python scoring agree | ✅ all 200 customers within 0.1, enforced by test |
| Dataset referential integrity | ✅ **28** contract checks in `scripts/validate_dataset.py` |
| Warehouse correctness | ✅ **52** dbt tests |
| DuckDB and Athena agree | ✅ 6/6 queries, `warehouse/verify_athena.py` |
| Retrieval quality | ✅ **0.971 hit rate, 0.941 recall** on single-entity questions (`scripts/benchmark_retrieval.py`, 2026-08-31) |
| Survival model — ranking | ✅ AUC **0.877** held out by unseen customers, calibrated (2.26% predicted vs 1.93% actual) |
| Survival model — **predicted dates** | ❌ **196 days median error.** Ranks well, dates unusable. Calibration pending |
| End-to-end latency | ❌ not benchmarked |

> **On the previous "94.7% accuracy" claim.** Earlier revisions of this README
> advertised 94.7% retrieval accuracy. That figure came from an evaluation run
> against an LLM-generated golden set whose questions referenced companies present
> in no data file, over a 25-document corpus, using harness code that could not
> execute at its own pinned RAGAS version. It did not measure anything. The golden
> set, the corpus and the harness have since been rebuilt. Retrieval is now measured
> against the golden set's `expected_context` — ground truth rather than an LLM's
> opinion — and reported with the date it was taken.

**How retrieval was fixed.** Dense embeddings match the *shape* of a question and
ignore named entities, so "how many tickets has DisasterRecovery Solutions raised"
returned five unrelated customers. BM25 has the opposite bias — it matches rare
exact terms, which is what a company name is. Fusing the two by reciprocal rank
took single-entity hit rate from 0.735 to 0.971.

**What retrieval still cannot do.** 27 of the 65 golden questions name more than
three contributing customers ("total ARR lost to churn"). Those are aggregates
over the whole dataset rather than facts in any document, so a retriever scores
partial credit it cannot really earn. They belong in SQL against the warehouse.
A further 4 questions have no expected context at all.

---

## Architecture

The design of record is [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). In short: the
**warehouse is authoritative for every number, the vector store for narrative**.
Retrieval explains a prediction; it does not produce one.


```
┌──────────────┐         ┌─────────────────────────────────────────────┐
│  Next.js UI  │────────▶│              FastAPI (single app)           │
│  dashboard   │◀────────│  degraded mode when no LLM stack available  │
└──────────────┘         └──────┬─────────────────────┬────────────────┘
                                │                     │
                  ┌─────────────▼──────┐   ┌──────────▼──────────────┐
                  │  Health scoring    │   │  LangGraph agents       │
                  │  (weighted model)  │   │  research + writing     │
                  └─────────┬──────────┘   └──────────┬──────────────┘
                            │                         │
                  ┌─────────▼──────────┐   ┌──────────▼──────────────┐
                  │   dbt warehouse    │   │   Qdrant vector store   │
                  │ bronze→silver→gold │   │   771 documents         │
                  │  DuckDB  ·  Athena │   │   OpenAI embeddings     │
                  └────────────────────┘   └─────────────────────────┘
```

Scoring lives in **both** Python and SQL. `src/core/health_scoring.py` serves the
API; `warehouse/models/gold/customer_health_score.sql` makes the same number
queryable by anyone with a SQL client. A parity test recomputes all 200 customers
each way and fails on divergence — two implementations are only acceptable when
something enforces that they agree.

---

## Quick start

```bash
git clone https://github.com/rach16/ChurnGuard-AI.git
cd ChurnGuard-AI
uv sync
cp .env.example .env     # then add your OPENAI_API_KEY
```

**Backend:**

```bash
uv run python src/backend/api.py
```

**Frontend:**

```bash
cd frontend && npm install && npm run dev
```

Dashboard at `http://localhost:3000`, API docs at `http://localhost:8000/docs`.

### Running without an API key

The API starts in **degraded mode**: the dashboard, health scoring and customer
detail pages work from CSV alone, and the LLM endpoints return 503 naming the
missing dependency. `ENABLE_RAG=false` selects this deliberately.

```bash
curl localhost:8000/health   # reports per-component status
curl localhost:8000/ready    # 503 when the service cannot serve
```

### The warehouse

```bash
cd warehouse && DBT_PROFILES_DIR=$PWD uv run --project .. dbt build
```

See [warehouse/README.md](warehouse/README.md) for the S3 + Athena path.

---

## The data

Synthetic, generated by `scripts/generate_synthetic_rag_data.py`, deterministic
under a fixed seed and `AS_OF_DATE`.

| | |
|---|---|
| Customers | 200 (**71 churned**, 35.5%) |
| Engagement snapshots | 15,840 weekly observations |
| Support tickets · interactions | 1,311 · 2,953 |
| RAG corpus | 771 documents |
| Golden eval set | 65 questions, derived from the data |

Each customer carries a latent health trajectory, and engagement, tickets, CSAT
and the churn label all derive from it — so the published features genuinely
predict the target (AUC 0.72–0.80 across five features, with tenure deliberately
uninformative at 0.51). Without that, no model could learn anything and the
project would be theatre.

```bash
python3 scripts/generate_synthetic_rag_data.py   # regenerate
python3 scripts/validate_dataset.py              # 28 contract checks
```

---

## Layout

```
src/
├── backend/api.py           FastAPI — the single entrypoint
├── core/
│   ├── health_scoring.py    weighted risk model (mirrors the SQL)
│   ├── rag_retrievers.py    5 retrieval strategies over Qdrant
│   └── knowledge_graph.py   NetworkX entity graph
├── model/survival.py        discrete-time survival model
├── agents/                  LangGraph research + writing teams
└── evaluation/              RAGAS harness

warehouse/                   dbt — bronze / silver / gold, incl. point-in-time features
infra/                       Terraform for ECS Fargate (written, never applied)
scripts/                     generation, validation, retrieval benchmark, S3 publish
frontend/                    Next.js 14 operator console (shadcn/ui, light + dark)
docs/adr/                    8 architecture decision records
```

## Testing

```bash
uv run --extra dev --extra warehouse pytest tests/
python3 scripts/validate_dataset.py
cd warehouse && DBT_PROFILES_DIR=$PWD uv run --project .. dbt test
```

## Known gaps

Tracked, not hidden:

- **Predicted churn dates are 196 days out** and not surfaced anywhere. The model
  ranks correctly; its hazards are too low, so survival curves decay too slowly
- **The hazard rate is non-stationary** — it rises from 0% to 5.2% across quarters
  because the generator gives every customer a declining trajectory. A model fitted
  on an early period underpredicts a later one
- Aggregate questions (27 of 65) are not answerable by retrieval; no SQL routing exists
- No committed evaluation baseline; `/evaluation-results` returns 404 by design
- Reranking requires `COHERE_API_KEY`; without it the fallback scores 0.0 recall
- The knowledge graph builder still expects a legacy schema and is not rebuilt
- Terraform is written and validated but has never been applied, so orchestration
  and IAM are unproven
- The data is synthetic with no ingestion path — the largest gap between this and
  something a customer could use
- No keyboard navigation in the queue

## Design decisions

Seven decisions from this rebuild are recorded in [docs/adr/](docs/adr/) — what
forced each one, what it cost, and what was rejected. Notably why the 94.7% claim
was retracted, and why fixing a long-silent knowledge-graph bug would have made
the system worse.

## Licence

MIT — see [LICENSE](LICENSE).
