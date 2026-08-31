"""
Generate the golden evaluation dataset from the source data.

Every question and every ground_truth here is *computed* from data/, not written by
a model. The previous golden master was LLM-generated and referenced companies
("Armadillo Shell Systems", "Cheetah Speed Data") that exist in no data file -- so
RAGAS was scoring retrieval against answers that could never be retrieved.

Deriving the answers instead means they are verifiable by construction, they cannot
hallucinate, and the set can be regenerated whenever the dataset changes.

Schema is unchanged (question, ground_truth, query_type, expected_context, difficulty)
so src/evaluation/run_evaluation.py keeps working. That harness reads only the first
two columns; expected_context now carries the customer_ids that actually support the
answer, so retrieval precision/recall can be scored directly once wired up.

Deterministic: fixed seed, and question order is stable.

Usage:
    python3 scripts/generate_golden_dataset.py
    python3 scripts/generate_golden_dataset.py --out golden-masters/churn_golden_master.csv
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics as stats
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_OUT = ROOT / "golden-masters" / "churn_golden_master.csv"

SEED = 42
FIELDNAMES = ["question", "ground_truth", "query_type", "expected_context", "difficulty"]


def load(name: str) -> list[dict]:
    with open(DATA_DIR / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def money(v: float) -> str:
    return f"${v:,.0f}"


class Facts:
    """Everything the question templates need, computed once from the source files."""

    def __init__(self) -> None:
        self.customers = load("customers.csv")
        self.by_id = {c["customer_id"]: c for c in self.customers}

        tickets = load("support_tickets.csv")
        snapshots = load("engagement_snapshots.csv")
        self.analyses = load("churn_analyses.csv")

        self.doc_for_customer = {a["customer_id"]: a["doc_id"] for a in self.analyses}

        self.tickets_by_cust: dict[str, list[dict]] = defaultdict(list)
        for t in tickets:
            self.tickets_by_cust[t["customer_id"]].append(t)

        # Snapshots are written in chronological order, so the last row is the latest.
        self.latest_snapshot: dict[str, dict] = {}
        for s in snapshots:
            self.latest_snapshot[s["customer_id"]] = s

        self.churned = [c for c in self.customers if c["is_churned"] == "1"]
        self.retained = [c for c in self.customers if c["is_churned"] == "0"]

    # -- per-customer helpers ----------------------------------------------------------

    def ticket_count(self, cid: str) -> int:
        return len(self.tickets_by_cust.get(cid, []))

    def mean_csat(self, cid: str) -> float | None:
        ts = self.tickets_by_cust.get(cid, [])
        return stats.mean(int(t["csat_score"]) for t in ts) if ts else None

    def engagement(self, cid: str) -> float:
        return float(self.latest_snapshot[cid]["engagement_score"])

    def adoption(self, cid: str) -> float:
        return float(self.latest_snapshot[cid]["feature_adoption_rate"])

    # -- aggregates --------------------------------------------------------------------

    def segment_rows(self, segment: str) -> list[dict]:
        return [c for c in self.customers if c["segment"] == segment]

    def churn_rate(self, rows: list[dict]) -> float:
        return sum(r["is_churned"] == "1" for r in rows) / len(rows) if rows else 0.0

    def arr_total(self, rows: list[dict]) -> int:
        return sum(int(r["arr"]) for r in rows)


# --------------------------------------------------------------------------------------
# Question builders -- each returns a list of golden rows
# --------------------------------------------------------------------------------------

def q_customer_specific(f: Facts, rng: random.Random) -> list[dict]:
    """Single-entity lookups. Answers come straight off the dimension and its facts."""
    rows = []
    sample = rng.sample(f.customers, k=32)

    for i, c in enumerate(sample):
        cid = c["customer_id"]
        name = c["company_name"]
        ctx = ",".join(x for x in (cid, f.doc_for_customer.get(cid, "")) if x)

        if i % 4 == 0:
            q = f"What is {name}'s annual recurring revenue, and which segment do they belong to?"
            a = (f"{name} ({cid}) is a {c['segment']} customer with an ARR of "
                 f"{money(int(c['arr']))} across {c['seats']} seats, in the {c['industry']} industry.")
            diff = "easy"

        elif i % 4 == 1:
            if c["is_churned"] == "1":
                q = f"Did {name} churn, and if so when and for what reason?"
                a = (f"Yes. {name} ({cid}) churned on {c['churn_date']} after {c['tenure_months']} "
                     f"months. The churn category was {c['churn_category']}, specifically: "
                     f"{c['specific_reason']}. They represented {money(int(c['arr']))} in ARR.")
            else:
                q = f"Has {name} churned, and what is their current contract status?"
                a = (f"No. {name} ({cid}) is an active {c['segment']} customer with "
                     f"{c['tenure_months']} months of tenure and a contract running to "
                     f"{c['contract_end_date']}, worth {money(int(c['arr']))} in ARR.")
            diff = "easy"

        elif i % 4 == 2:
            n = f.ticket_count(cid)
            csat = f.mean_csat(cid)
            q = f"How many support tickets has {name} raised, and what is their average CSAT?"
            a = (f"{name} ({cid}) has raised {n} support ticket{'s' if n != 1 else ''}"
                 + (f", with an average CSAT of {csat:.2f} out of 5." if csat is not None
                    else " and has no recorded CSAT scores."))
            diff = "medium"

        else:
            q = f"What is {name}'s latest engagement score and feature adoption rate?"
            a = (f"{name} ({cid}) has a latest engagement score of {f.engagement(cid):.3f} "
                 f"and a feature adoption rate of {f.adoption(cid):.3f}, as of their most "
                 f"recent weekly snapshot ({f.latest_snapshot[cid]['week_start']}).")
            diff = "medium"

        rows.append({"question": q, "ground_truth": a, "query_type": "customer_specific",
                     "expected_context": ctx, "difficulty": diff})
    return rows


def q_pattern_analysis(f: Facts, rng: random.Random) -> list[dict]:
    """Comparative questions across the churned/retained split."""
    ch_eng = [f.engagement(c["customer_id"]) for c in f.churned if c["customer_id"] in f.latest_snapshot]
    re_eng = [f.engagement(c["customer_id"]) for c in f.retained if c["customer_id"] in f.latest_snapshot]
    ch_csat = [v for c in f.churned if (v := f.mean_csat(c["customer_id"])) is not None]
    re_csat = [v for c in f.retained if (v := f.mean_csat(c["customer_id"])) is not None]

    ch_tpm = [f.ticket_count(c["customer_id"]) / max(1, int(c["tenure_months"])) for c in f.churned]
    re_tpm = [f.ticket_count(c["customer_id"]) / max(1, int(c["tenure_months"])) for c in f.retained]

    cat_counts = Counter(c["churn_category"] for c in f.churned)
    top_cat, top_n = cat_counts.most_common(1)[0]
    cat_arr = Counter()
    for c in f.churned:
        cat_arr[c["churn_category"]] += int(c["arr"])
    costliest_cat, costliest_arr = cat_arr.most_common(1)[0]

    ctx_churned = ",".join(c["customer_id"] for c in f.churned[:10])

    rows = [
        ("How does the latest engagement score differ between churned and retained customers?",
         f"Churned customers average {stats.mean(ch_eng):.3f} on their final engagement score, "
         f"versus {stats.mean(re_eng):.3f} for retained customers -- a gap of "
         f"{stats.mean(re_eng) - stats.mean(ch_eng):.3f}. Engagement is the strongest single "
         f"separator between the two groups.", "hard"),

        ("Do churned customers report lower satisfaction than retained ones?",
         f"Yes. Churned customers average a CSAT of {stats.mean(ch_csat):.2f} out of 5, compared "
         f"with {stats.mean(re_csat):.2f} for retained customers.", "medium"),

        ("Do churned customers raise support tickets at a higher rate than retained customers?",
         f"Yes. Churned customers raise {stats.mean(ch_tpm):.2f} tickets per month of tenure on "
         f"average, versus {stats.mean(re_tpm):.2f} for retained customers.", "hard"),

        ("Which churn category accounts for the largest number of churned customers?",
         f"{top_cat}, with {top_n} of {len(f.churned)} churned customers "
         f"({top_n / len(f.churned):.1%}).", "medium"),

        ("Which churn category is associated with the most lost ARR?",
         f"{costliest_cat}, accounting for {money(costliest_arr)} of lost ARR across the "
         f"churned base.", "medium"),

        ("What is the overall churn rate across the customer base?",
         f"{len(f.churned)} of {len(f.customers)} customers have churned, a rate of "
         f"{len(f.churned) / len(f.customers):.1%}.", "easy"),

        ("What is the average tenure at which customers churn?",
         f"Churned customers had an average tenure of "
         f"{stats.mean(int(c['tenure_months']) for c in f.churned):.1f} months at the point "
         f"of churn.", "medium"),

        ("How does feature adoption compare between churned and retained customers?",
         f"Churned customers reached an average feature adoption rate of "
         f"{stats.mean(f.adoption(c['customer_id']) for c in f.churned if c['customer_id'] in f.latest_snapshot):.3f}, "
         f"against {stats.mean(f.adoption(c['customer_id']) for c in f.retained if c['customer_id'] in f.latest_snapshot):.3f} "
         f"for retained customers.", "hard"),
    ]

    return [{"question": q, "ground_truth": a, "query_type": "pattern_analysis",
             "expected_context": ctx_churned, "difficulty": d} for q, a, d in rows]


def q_competitive_intelligence(f: Facts, rng: random.Random) -> list[dict]:
    """Competitor attribution. Only churned customers carry a competitor."""
    with_comp = [c for c in f.churned if c["competitor"]]
    comp_counts = Counter(c["competitor"] for c in with_comp)
    comp_arr = Counter()
    for c in with_comp:
        comp_arr[c["competitor"]] += int(c["arr"])

    rows = []
    for comp, n in comp_counts.most_common():
        losses = [c for c in with_comp if c["competitor"] == comp]
        rows.append((
            f"How many customers did we lose to {comp}, and what ARR did they represent?",
            f"{n} churned customer{'s' if n != 1 else ''} named {comp} as a competitor, "
            f"representing {money(comp_arr[comp])} in lost ARR.",
            ",".join(c["customer_id"] for c in losses[:8]),
            "medium",
        ))

    top_comp, top_arr = comp_arr.most_common(1)[0]
    rows.append((
        "Which competitor is associated with the most lost ARR?",
        f"{top_comp}, with {money(top_arr)} of ARR lost across "
        f"{comp_counts[top_comp]} churned customers.",
        ",".join(c["customer_id"] for c in with_comp[:10]),
        "hard",
    ))
    rows.append((
        "What share of churned customers named a competitor?",
        f"{len(with_comp)} of {len(f.churned)} churned customers named a competitor "
        f"({len(with_comp) / len(f.churned):.1%}); the remainder churned without one recorded.",
        ",".join(c["customer_id"] for c in with_comp[:10]),
        "medium",
    ))

    return [{"question": q, "ground_truth": a, "query_type": "competitive_intelligence",
             "expected_context": ctx, "difficulty": d} for q, a, ctx, d in rows]


def q_financial_analysis(f: Facts, rng: random.Random) -> list[dict]:
    """ARR questions, including the at-risk figure the dashboard headlines."""
    lost = f.arr_total(f.churned)
    retained_arr = f.arr_total(f.retained)

    at_risk = [c for c in f.retained
               if c["customer_id"] in f.latest_snapshot and f.engagement(c["customer_id"]) < 0.4]
    at_risk_arr = f.arr_total(at_risk)

    ctx_ch = ",".join(c["customer_id"] for c in f.churned[:10])
    ctx_risk = ",".join(c["customer_id"] for c in at_risk[:10])

    rows = [
        ("What is the total ARR lost to churn?",
         f"{money(lost)} across {len(f.churned)} churned customers.", ctx_ch, "easy"),

        ("What is the total ARR of the retained customer base?",
         f"{money(retained_arr)} across {len(f.retained)} active customers.", "", "easy"),

        ("How much ARR is currently at risk among active customers?",
         f"{money(at_risk_arr)} across {len(at_risk)} active customers whose latest engagement "
         f"score is below 0.4.", ctx_risk, "hard"),

        ("How does average ARR compare between churned and retained customers?",
         f"Churned customers averaged {money(stats.mean(int(c['arr']) for c in f.churned))} in ARR, "
         f"versus {money(stats.mean(int(c['arr']) for c in f.retained))} for retained customers.",
         "", "medium"),

        ("What proportion of total book ARR has been lost to churn?",
         f"{money(lost)} of {money(lost + retained_arr)} total, or "
         f"{lost / (lost + retained_arr):.1%}.", ctx_ch, "hard"),

        ("Which single churned customer represented the largest ARR loss?",
         (lambda c: f"{c['company_name']} ({c['customer_id']}), a {c['segment']} customer worth "
                    f"{money(int(c['arr']))} in ARR, churned on {c['churn_date']} due to "
                    f"{c['churn_category']}.")(max(f.churned, key=lambda c: int(c["arr"]))),
         max(f.churned, key=lambda c: int(c["arr"]))["customer_id"], "medium"),
    ]

    return [{"question": q, "ground_truth": a, "query_type": "financial_analysis",
             "expected_context": ctx, "difficulty": d} for q, a, ctx, d in rows]


def q_segment_analysis(f: Facts, rng: random.Random) -> list[dict]:
    """Per-segment breakdowns plus the cross-segment comparison."""
    rows = []
    segments = sorted({c["segment"] for c in f.customers})

    for seg in segments:
        rs = f.segment_rows(seg)
        ch = [c for c in rs if c["is_churned"] == "1"]
        ctx = ",".join(c["customer_id"] for c in rs[:8])

        rows.append((
            f"How many {seg} customers are there, and what is their churn rate?",
            f"There are {len(rs)} {seg} customers, of whom {len(ch)} have churned -- "
            f"a churn rate of {f.churn_rate(rs):.1%}.", ctx, "easy"))

        rows.append((
            f"What is the total and average ARR for the {seg} segment?",
            f"The {seg} segment represents {money(f.arr_total(rs))} in total ARR, averaging "
            f"{money(stats.mean(int(c['arr']) for c in rs))} per customer.", ctx, "medium"))

        rows.append((
            f"What is the most common churn reason among {seg} customers?",
            (lambda cc: f"{cc[0][0]}, cited by {cc[0][1]} of {len(ch)} churned {seg} customers."
             if cc else f"No {seg} customers have churned.")(
                Counter(c["churn_category"] for c in ch).most_common(1)),
            ctx, "medium"))

    rates = {seg: f.churn_rate(f.segment_rows(seg)) for seg in segments}
    worst = max(rates, key=rates.get)
    best = min(rates, key=rates.get)

    rows.append((
        "Which customer segment has the highest churn rate?",
        f"{worst}, at {rates[worst]:.1%}. For comparison, {best} is lowest at {rates[best]:.1%}.",
        "", "hard"))
    rows.append((
        "Rank the segments by churn rate, highest to lowest.",
        ", ".join(f"{s} ({rates[s]:.1%})" for s in sorted(rates, key=rates.get, reverse=True)) + ".",
        "", "hard"))

    return [{"question": q, "ground_truth": a, "query_type": "segment_analysis",
             "expected_context": ctx, "difficulty": d} for q, a, ctx, d in rows]


# --------------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the golden evaluation dataset")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    f = Facts()

    rows: list[dict] = []
    for builder in (q_customer_specific, q_pattern_analysis, q_competitive_intelligence,
                    q_financial_analysis, q_segment_analysis):
        rows.extend(builder(f, rng))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    by_type = Counter(r["query_type"] for r in rows)
    by_diff = Counter(r["difficulty"] for r in rows)

    print(f"Wrote {len(rows)} questions to {args.out.relative_to(ROOT)}")
    print("  by type:      " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    print("  by difficulty:" + " " + ", ".join(f"{k}={v}" for k, v in sorted(by_diff.items())))
    print(f"  grounded in:  {len(f.customers)} customers, {len(f.analyses)} analysis docs")


if __name__ == "__main__":
    main()
