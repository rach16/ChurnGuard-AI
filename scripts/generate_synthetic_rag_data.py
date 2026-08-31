"""
Generate the ChurnGuard synthetic dataset.

Entity-first: `customers.csv` is the dimension and every other file references it
by `customer_id`. Attributes that belong to a customer (segment, ARR, tenure) are
resolved once on the dimension and never re-randomised in a child table.

Each customer carries a latent health trajectory. Engagement snapshots, support
ticket volume, CSAT and feature adoption are all derived from that trajectory, and
so is the churn label -- so the published features genuinely predict the target
rather than being independent noise.

Deterministic: fixed seed and a fixed AS_OF_DATE, so repeated runs are byte-identical.
Standard library only -- the generator does not need the ML stack to run.

Usage:
    python3 scripts/generate_synthetic_rag_data.py
    python3 scripts/generate_synthetic_rag_data.py --customers 500 --seed 7
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DOCS_DIR = DATA_DIR / "churn_analysis_docs"

SEED = 42
NUM_CUSTOMERS = 200

# Fixed rather than date.today() so the dataset is reproducible. dbt tests, the golden
# eval set and any trained model all depend on this staying stable between runs.
AS_OF_DATE = date(2026, 6, 30)

SEGMENTS = ["SMB", "Commercial", "Enterprise"]
SEGMENT_WEIGHTS = [0.45, 0.35, 0.20]

# ARR band and seat count per segment: (arr_low, arr_high, seats_low, seats_high)
SEGMENT_PROFILE = {
    "SMB": (10_000, 60_000, 5, 30),
    "Commercial": (50_000, 150_000, 20, 100),
    "Enterprise": (100_000, 500_000, 50, 500),
}

INDUSTRIES = [
    "Financial Services", "Healthcare", "Retail", "Manufacturing", "Media",
    "Logistics", "Education", "Energy", "Public Sector", "Technology",
]
REGIONS = ["NA-East", "NA-West", "EMEA", "APAC", "LATAM"]

NAME_STEMS = [
    "TechFlow", "DataSync", "CloudVision", "InnovateLabs", "StreamPro",
    "AnalyticsCo", "DevOpsHub", "SecureNet", "AIForge", "CodeCraft",
    "ScaleSystems", "MetricsDash", "WorkflowPro", "IntegrationHQ", "DataBridge",
    "CloudScale", "DevTools", "APIGateway", "MicroServe", "ContainerOps",
    "MonitorPlus", "LogStream", "SecurityFirst", "ComplianceHub", "AuditPro",
    "BackupCloud", "DisasterRecovery", "LoadBalance", "CacheMaster", "QueueManager",
]
NAME_SUFFIXES = ["Systems", "Solutions", "Technologies", "Corp", "Inc", "LLC", "Group", "Labs"]

COMPETITORS = [
    "Vantage Analytics", "NorthStar Cloud", "Kestrel Data", "Apex Platform",
    "Beacon Systems", "Meridian Software",
]

CHURN_REASONS = {
    "Pricing Concerns": ["Too expensive", "Better pricing elsewhere", "ROI not clear", "Budget cuts"],
    "Product Fit": ["Missing features", "Too complex", "Doesn't scale", "Poor integration"],
    "Support Issues": ["Slow response", "Unresolved bugs", "Poor documentation", "No training"],
    "Competition": ["Competitor has better features", "Competitor cheaper", "Better UX elsewhere"],
    "Adoption Challenges": ["Team not using it", "Too hard to onboard", "Lack of champions"],
}

RETENTION_STRATEGIES = {
    "Pricing Concerns": [
        "Offered discount for annual commitment",
        "Created custom pricing tier",
        "Demonstrated ROI with detailed analytics",
        "Provided credits for referrals",
    ],
    "Product Fit": [
        "Fast-tracked feature request",
        "Built custom integration",
        "Simplified workflow with automation",
        "Provided API access for customization",
    ],
    "Support Issues": [
        "Assigned dedicated success manager",
        "Created custom training program",
        "Set up weekly check-ins",
        "Improved response SLA",
    ],
    "Competition": [
        "Highlighted unique differentiators",
        "Matched competitor pricing",
        "Added requested features quickly",
        "Improved product based on feedback",
    ],
    "Adoption Challenges": [
        "Conducted onboarding workshops",
        "Created internal champions program",
        "Simplified user interface",
        "Provided implementation support",
    ],
}

TICKET_CATEGORIES = {
    "Technical": ["API error", "Integration issue", "Performance slow", "Data sync problem", "Authentication failure"],
    "Feature": ["Missing functionality", "Feature request", "Workflow improvement", "UI/UX feedback"],
    "Billing": ["Invoice question", "Pricing clarification", "Payment issue", "Plan change request"],
    "Training": ["How-to question", "Best practices", "Setup help", "Documentation request"],
}

MAX_SNAPSHOT_WEEKS = 104  # two years of weekly history, at most


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------------------
# The customer dimension
# --------------------------------------------------------------------------------------

@dataclass
class Customer:
    """One row of data/customers.csv, plus latent fields used only during generation."""

    customer_id: str
    company_name: str
    segment: str
    industry: str
    region: str
    arr: int
    seats: int
    contract_start_date: str
    contract_end_date: str
    tenure_months: int
    is_churned: int
    churn_date: str
    churn_category: str
    specific_reason: str
    competitor: str

    # Latent -- drives the child tables, not exported to customers.csv.
    start: date = field(default=AS_OF_DATE, repr=False)
    end: date = field(default=AS_OF_DATE, repr=False)
    base_health: float = field(default=0.5, repr=False)
    weekly_slope: float = field(default=0.0, repr=False)

    EXPORT_FIELDS = (
        "customer_id", "company_name", "segment", "industry", "region", "arr", "seats",
        "contract_start_date", "contract_end_date", "tenure_months", "is_churned",
        "churn_date", "churn_category", "specific_reason", "competitor",
    )

    def health_at(self, day: date) -> float:
        """Latent health in [0, 1] at a point in time, following the customer's trend."""
        weeks = max(0, (day - self.start).days) / 7.0
        return clamp(self.base_health + self.weekly_slope * weeks)

    def export_row(self) -> dict:
        row = asdict(self)
        return {k: row[k] for k in self.EXPORT_FIELDS}


def build_customers(rng: random.Random, count: int) -> list[Customer]:
    """Create `count` customers with unique names and a latent health trajectory."""
    names = [f"{stem} {suffix}" for stem in NAME_STEMS for suffix in NAME_SUFFIXES]
    rng.shuffle(names)
    if count > len(names):
        raise ValueError(f"Cannot make {count} unique names from {len(names)} combinations")

    customers: list[Customer] = []
    for i in range(count):
        segment = rng.choices(SEGMENTS, weights=SEGMENT_WEIGHTS, k=1)[0]
        arr_low, arr_high, seat_low, seat_high = SEGMENT_PROFILE[segment]

        tenure_months = rng.randint(3, 48)
        start = AS_OF_DATE - timedelta(days=tenure_months * 30)

        # Latent trajectory. Enterprise accounts start healthier; short-tenure accounts
        # are more volatile. The slope is what ultimately separates churn from retention.
        base_health = clamp(rng.gauss({"SMB": 0.58, "Commercial": 0.64, "Enterprise": 0.70}[segment], 0.16))
        weekly_slope = rng.gauss(-0.0015, 0.0045)

        weeks_elapsed = max(1, (AS_OF_DATE - start).days // 7)
        final_health = clamp(base_health + weekly_slope * weeks_elapsed)

        # Churn probability is a function of the latent state, not an independent coin
        # flip -- this is what makes the published features predictive.
        tenure_penalty = 0.18 if tenure_months < 12 else 0.0
        churn_prob = clamp(0.88 * (1.0 - final_health) + tenure_penalty - 0.15)
        is_churned = rng.random() < churn_prob

        churn_category = rng.choice(list(CHURN_REASONS))
        specific_reason = rng.choice(CHURN_REASONS[churn_category])

        if is_churned:
            # Churn lands somewhere in the back half of the relationship.
            churn_day = start + timedelta(days=int((AS_OF_DATE - start).days * rng.uniform(0.55, 1.0)))
            end = churn_day
            churn_date = churn_day.isoformat()
            contract_end = churn_day
        else:
            churn_date = ""
            end = AS_OF_DATE
            contract_end = AS_OF_DATE + timedelta(days=rng.randint(30, 365))

        customers.append(
            Customer(
                customer_id=f"CUST-{i + 1:04d}",
                company_name=names[i],
                segment=segment,
                industry=rng.choice(INDUSTRIES),
                region=rng.choice(REGIONS),
                arr=int(round(rng.uniform(arr_low, arr_high), -2)),
                seats=rng.randint(seat_low, seat_high),
                contract_start_date=start.isoformat(),
                contract_end_date=contract_end.isoformat(),
                tenure_months=tenure_months,
                is_churned=int(is_churned),
                churn_date=churn_date,
                churn_category=churn_category if is_churned else "",
                specific_reason=specific_reason if is_churned else "",
                competitor=rng.choice(COMPETITORS) if (is_churned and rng.random() > 0.45) else "",
                start=start,
                end=end,
                base_health=base_health,
                weekly_slope=weekly_slope,
            )
        )
    return customers


# --------------------------------------------------------------------------------------
# Fact tables -- every row references a customer_id from the dimension
# --------------------------------------------------------------------------------------

def build_engagement_snapshots(rng: random.Random, customers: list[Customer]) -> list[dict]:
    """Weekly engagement time series per customer, derived from the health trajectory.

    This replaces the per-request random walk the API used to fabricate for charts.
    """
    rows = []
    for c in customers:
        total_weeks = max(1, (c.end - c.start).days // 7)
        weeks = min(total_weeks, MAX_SNAPSHOT_WEEKS)
        first_week = c.end - timedelta(weeks=weeks)

        for w in range(weeks):
            day = first_week + timedelta(weeks=w)
            health = c.health_at(day)

            engagement = clamp(rng.gauss(health, 0.06))
            adoption = clamp(rng.gauss(health * 0.92, 0.05))
            active_users = max(1, int(c.seats * clamp(rng.gauss(health, 0.08))))

            rows.append({
                "customer_id": c.customer_id,
                "week_start": day.isoformat(),
                "active_users": active_users,
                "sessions": max(0, int(active_users * rng.uniform(1.5, 6.0) * health)),
                "feature_adoption_rate": round(adoption, 3),
                "engagement_score": round(engagement, 3),
            })
    return rows


def build_interactions(rng: random.Random, customers: list[Customer]) -> list[dict]:
    """Customer touchpoints. Healthy accounts get more meetings, unhealthy get escalations."""
    types = ["Email", "Call", "Meeting", "Support Ticket", "Product Feedback"]
    rows = []

    for c in customers:
        span_days = max(7, (c.end - c.start).days)
        for _ in range(rng.randint(5, 25)):
            day = c.start + timedelta(days=rng.randint(0, span_days))
            health = c.health_at(day)
            itype = rng.choice(types)

            if itype == "Support Ticket":
                topic = rng.choice(["Bug report", "Feature request", "Integration help", "Performance issue"])
                sentiment = "Frustrated" if health < 0.4 else "Neutral" if health < 0.7 else "Satisfied"
                content = f"{c.company_name} reported {topic.lower()}. Sentiment: {sentiment}."
            elif itype == "Meeting":
                kind = rng.choice(["Quarterly business review", "Training session", "Feature demo", "Renewal discussion"])
                content = f"{kind} with {c.company_name}. {rng.randint(2, 8)} attendees. Discussed roadmap and usage."
            elif itype == "Call":
                reason = "Issue escalation" if health < 0.45 else rng.choice(["Check-in", "Feature question", "Expansion discussion"])
                outcome = "Needs follow-up" if health < 0.5 else "Positive"
                content = f"{reason} call with {c.company_name}. {rng.randint(15, 60)} minutes. {outcome} outcome."
            else:
                content = f"Communication with {c.company_name} regarding {rng.choice(['product updates', 'usage tips', 'billing', 'feedback request'])}."

            rows.append({
                "customer_id": c.customer_id,
                "company_name": c.company_name,
                "segment": c.segment,
                "interaction_date": day.isoformat(),
                "interaction_type": itype,
                "content": content,
                "customer_tenure_months": c.tenure_months,
            })

    rows.sort(key=lambda r: r["interaction_date"], reverse=True)
    return rows


def build_support_tickets(rng: random.Random, customers: list[Customer]) -> list[dict]:
    """Ticket volume scales inversely with health; CSAT scales with it."""
    rows = []
    ticket_no = 0

    for c in customers:
        span_days = max(7, (c.end - c.start).days)
        health_now = c.health_at(c.end)
        # 1 ticket for a healthy account, up to ~12 for one in trouble.
        volume = max(1, int(round(1 + 11 * (1 - health_now) * rng.uniform(0.6, 1.4))))

        for _ in range(volume):
            ticket_no += 1
            day = c.start + timedelta(days=rng.randint(0, span_days))
            health = c.health_at(day)

            category = rng.choice(list(TICKET_CATEGORIES))
            issue = rng.choice(TICKET_CATEGORIES[category])
            severity = rng.choices(
                ["Low", "Medium", "High", "Critical"],
                weights=[0.35, 0.35, 0.2, 0.1] if health > 0.5 else [0.15, 0.3, 0.35, 0.2],
                k=1,
            )[0]
            resolution_hours = {
                "Critical": rng.randint(1, 12), "High": rng.randint(4, 48),
                "Medium": rng.randint(12, 96), "Low": rng.randint(24, 168),
            }[severity]
            csat = max(1, min(5, int(round(rng.gauss(1 + 4 * health, 0.7)))))

            description = f"{c.company_name} ({c.segment}) reported: {issue}. Severity: {severity}."
            if severity in ("High", "Critical"):
                description += " Impacting business operations."

            rows.append({
                "ticket_id": f"TICKET-{ticket_no:05d}",
                "customer_id": c.customer_id,
                "company_name": c.company_name,
                "segment": c.segment,
                "category": category,
                "issue_type": issue,
                "severity": severity,
                "created_date": day.isoformat(),
                "resolved_date": (day + timedelta(hours=resolution_hours)).isoformat(),
                "resolution_hours": resolution_hours,
                "description": description,
                "resolution_notes": f"Resolved by providing {rng.choice(['workaround', 'fix', 'documentation', 'configuration change', 'feature update'])}. Customer satisfaction: {csat}/5.",
                "csat_score": csat,
            })
    return rows


def build_success_stories(rng: random.Random, customers: list[Customer]) -> list[dict]:
    """Retained accounts that recovered. A churned customer cannot have a success story."""
    eligible = [c for c in customers if not c.is_churned and c.weekly_slope > -0.001]
    chosen = rng.sample(eligible, k=min(60, len(eligible)))
    rows = []

    for n, c in enumerate(sorted(chosen, key=lambda x: x.customer_id), start=1):
        challenge_category = rng.choice(list(CHURN_REASONS))
        specific_challenge = rng.choice(CHURN_REASONS[challenge_category])
        solution = rng.choice(RETENTION_STRATEGIES[challenge_category])

        adoption_after = int(clamp(c.health_at(c.end)) * 100)
        adoption_before = max(15, adoption_after - rng.randint(25, 45))

        story = f"""**Company:** {c.company_name}
**Segment:** {c.segment}   |   **Industry:** {c.industry}   |   **Seats:** {c.seats}
**ARR:** ${c.arr:,}

**Challenge:**
{c.company_name} was facing {specific_challenge.lower()}. Feature adoption had stalled at {adoption_before}% and the account was evaluating alternatives.

**Solution Implemented:**
{solution}. Customer Success worked with their leadership on a tailored plan.

**Results:**
- Feature adoption rose from {adoption_before}% to {adoption_after}%
- Support tickets reduced by {rng.randint(40, 80)}%
- Renewed and expanded contract

**Key Learnings:**
Early intervention matters most for {c.segment} accounts facing {challenge_category.lower()}."""

        rows.append({
            "story_id": f"SUCCESS-{n:03d}",
            "customer_id": c.customer_id,
            "company_name": c.company_name,
            "segment": c.segment,
            "title": f"How {c.company_name} Overcame {challenge_category} and Lifted Adoption by {adoption_after - adoption_before}%",
            "challenge_category": challenge_category,
            "specific_challenge": specific_challenge,
            "solution": solution,
            "arr": c.arr,
            "team_size": c.seats,
            "adoption_before": adoption_before,
            "adoption_after": adoption_after,
            "engagement_increase": rng.randint(30, 150),
            "support_reduction": rng.randint(40, 80),
            "full_story": story,
        })
    return rows


def build_churn_analyses(rng: random.Random, customers: list[Customer],
                         tickets: list[dict]) -> list[dict]:
    """Analysis docs for churned accounts and the worst-off retained ones (the RAG corpus)."""
    ticket_counts: dict[str, int] = {}
    for t in tickets:
        ticket_counts[t["customer_id"]] = ticket_counts.get(t["customer_id"], 0) + 1

    churned = [c for c in customers if c.is_churned]
    at_risk = sorted(
        (c for c in customers if not c.is_churned),
        key=lambda c: c.health_at(c.end),
    )[:40]

    rows = []
    for n, c in enumerate(sorted(churned + at_risk, key=lambda x: x.customer_id), start=1):
        health = c.health_at(c.end)
        risk_score = int(round((1 - health) * 100))
        feature_adoption = int(clamp(health * 0.92) * 100)
        tickets_30d = max(1, int(ticket_counts.get(c.customer_id, 1) / max(1, c.tenure_months) * 30))
        last_engagement_days = rng.randint(3, 20) if health > 0.6 else rng.randint(20, 75)

        category = c.churn_category or rng.choice(list(CHURN_REASONS))
        reason = c.specific_reason or rng.choice(CHURN_REASONS[category])

        document = f"""# Churn Risk Analysis: {c.company_name}

## Executive Summary
{c.company_name} ({c.customer_id}) is a {c.segment} customer in {c.industry} with ${c.arr:,} ARR and {c.tenure_months} months of tenure. Current risk score: {risk_score}%. Status: {"CHURNED on " + c.churn_date if c.is_churned else "ACTIVE - at risk"}.

## Risk Factors
1. **Primary Concern:** {reason}
2. **Feature Adoption:** {feature_adoption}% (below the {c.segment} benchmark of 70%)
3. **Support Activity:** {tickets_30d} tickets per 30 days
4. **Engagement:** last interaction {last_engagement_days} days ago

## Detailed Analysis

### Churn Category: {category}
This account shows the classic pattern of {category.lower()}, specifically: {reason.lower()}.

### Behavioural Patterns
- Feature adoption plateaued at {feature_adoption}%
- Support volume {"rising" if tickets_30d > 6 else "steady"}
- Engagement {"declining" if last_engagement_days > 30 else "stable"}

### Segment-Specific Insight
For {c.segment} accounts, {category.lower()} typically calls for {"executive intervention" if c.segment == "Enterprise" else "focused customer success effort" if c.segment == "Commercial" else "product education and training"}.

## Recommended Actions

### Immediate (7 days)
1. Schedule {"an executive business review" if c.segment == "Enterprise" else "a customer success call"}
2. Address {reason.lower()} directly
3. {"Assign a dedicated technical account manager" if c.segment == "Enterprise" else "Prioritise open support tickets"}

### Short-term (30 days)
1. Lift feature adoption from {feature_adoption}% toward {min(feature_adoption + 25, 85)}%
2. Reduce support load by {"proactive monitoring" if tickets_30d > 6 else "better documentation"}
3. Establish a {"weekly" if risk_score > 70 else "bi-weekly"} check-in cadence

### Long-term (90 days)
1. Rebuild executive relationships
2. Explore expansion once health recovers

## Historical Context
Comparable {c.segment} accounts that received {rng.choice(RETENTION_STRATEGIES[category]).lower()} showed {"significant" if health > 0.4 else "moderate"} improvement in retention metrics.

---
*Customer ID: {c.customer_id}*
*Risk Score: {risk_score}%*
*Segment: {c.segment}*"""

        rows.append({
            "doc_id": f"ANALYSIS-{n:04d}",
            "customer_id": c.customer_id,
            "company_name": c.company_name,
            "segment": c.segment,
            "churn_category": category,
            "specific_reason": reason,
            "arr": c.arr,
            "tenure_months": c.tenure_months,
            "risk_score": risk_score,
            "feature_adoption": feature_adoption,
            "support_tickets_30d": tickets_30d,
            "last_engagement_days": last_engagement_days,
            "is_churned": c.is_churned,
            "document": document,
        })
    return rows


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    if not rows:
        raise ValueError(f"Refusing to write empty file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames or list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {path.relative_to(DATA_DIR.parent)}: {len(rows):,} rows")


def write_analysis_docs(analyses: list[dict]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for stale in DOCS_DIR.glob("*.txt"):
        stale.unlink()
    for row in analyses:
        (DOCS_DIR / f"{row['doc_id']}.txt").write_text(row["document"], encoding="utf-8")
    print(f"  data/churn_analysis_docs/: {len(analyses):,} files")


def write_metadata(customers: list[Customer], counts: dict[str, int]) -> None:
    churned = sum(c.is_churned for c in customers)
    metadata = {
        "generated_by": "scripts/generate_synthetic_rag_data.py",
        "as_of_date": AS_OF_DATE.isoformat(),
        "seed": SEED,
        "grain": "customers.csv is the dimension; all other files reference customer_id",
        "data_files": {
            "customers": "customers.csv",
            "engagement_snapshots": "engagement_snapshots.csv",
            "customer_interactions": "customer_interactions.csv",
            "support_tickets": "support_tickets.csv",
            "success_stories": "success_stories.csv",
            "churn_analyses": "churn_analyses.csv",
        },
        "document_collections": {"churn_analysis_docs": "One text file per churn analysis, for RAG ingestion"},
        "statistics": {
            "total_customers": len(customers),
            "churned_customers": churned,
            "churn_rate": round(churned / len(customers), 3),
            "segments": SEGMENTS,
            "churn_categories": list(CHURN_REASONS),
            "row_counts": counts,
        },
        "usage": {
            "description": "Synthetic B2B SaaS churn dataset for ChurnGuard AI",
            "recommended_chunk_size": 500,
            "recommended_overlap": 50,
        },
    }
    (DATA_DIR / "rag_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("  data/rag_metadata.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the ChurnGuard synthetic dataset")
    parser.add_argument("--customers", type=int, default=NUM_CUSTOMERS)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    print(f"Generating dataset ({args.customers} customers, seed={args.seed}, as_of={AS_OF_DATE})")

    customers = build_customers(rng, args.customers)
    snapshots = build_engagement_snapshots(rng, customers)
    interactions = build_interactions(rng, customers)
    tickets = build_support_tickets(rng, customers)
    stories = build_success_stories(rng, customers)
    analyses = build_churn_analyses(rng, customers, tickets)

    write_csv(DATA_DIR / "customers.csv", [c.export_row() for c in customers], list(Customer.EXPORT_FIELDS))
    write_csv(DATA_DIR / "engagement_snapshots.csv", snapshots)
    write_csv(DATA_DIR / "customer_interactions.csv", interactions)
    write_csv(DATA_DIR / "support_tickets.csv", tickets)
    write_csv(DATA_DIR / "success_stories.csv", stories)
    write_csv(DATA_DIR / "churn_analyses.csv", analyses)
    write_analysis_docs(analyses)
    write_metadata(customers, {
        "engagement_snapshots": len(snapshots),
        "customer_interactions": len(interactions),
        "support_tickets": len(tickets),
        "success_stories": len(stories),
        "churn_analyses": len(analyses),
    })

    churned = sum(c.is_churned for c in customers)
    print(f"\nDone. {len(customers)} customers, {churned} churned ({churned / len(customers):.1%}).")
    print("Verify with: python3 scripts/validate_dataset.py")


if __name__ == "__main__":
    main()
