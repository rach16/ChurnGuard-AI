"""
Generate comprehensive synthetic data for RAG system
Creates multiple datasets: customer interactions, support tickets, success stories, and churn analyses
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import random
from pathlib import Path

# Set seed for reproducibility
np.random.seed(42)
random.seed(42)

# Define data directory
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# Company names
COMPANY_NAMES = [
    "TechFlow", "DataSync", "CloudVision", "InnovateLabs", "StreamPro",
    "AnalyticsCo", "DevOpsHub", "SecureNet", "AIForge", "CodeCraft",
    "ScaleSystems", "MetricsDash", "WorkflowPro", "IntegrationHQ", "DataBridge",
    "CloudScale", "DevTools", "APIGateway", "MicroServe", "ContainerOps",
    "MonitorPlus", "LogStream", "SecurityFirst", "ComplianceHub", "AuditPro",
    "BackupCloud", "DisasterRecovery", "LoadBalance", "CacheMaster", "QueueManager"
]

SEGMENTS = ["SMB", "Commercial", "Enterprise"]

# Churn reasons and patterns
CHURN_REASONS = {
    "Pricing Concerns": ["Too expensive", "Better pricing elsewhere", "ROI not clear", "Budget cuts"],
    "Product Fit": ["Missing features", "Too complex", "Doesn't scale", "Poor integration"],
    "Support Issues": ["Slow response", "Unresolved bugs", "Poor documentation", "No training"],
    "Competition": ["Competitor has better features", "Competitor cheaper", "Better UX elsewhere"],
    "Adoption Challenges": ["Team not using it", "Too hard to onboard", "Lack of champions"]
}

# Successful retention strategies
RETENTION_STRATEGIES = {
    "Pricing Concerns": [
        "Offered discount for annual commitment",
        "Created custom pricing tier",
        "Demonstrated ROI with detailed analytics",
        "Provided credits for referrals"
    ],
    "Product Fit": [
        "Fast-tracked feature request",
        "Built custom integration",
        "Simplified workflow with automation",
        "Provided API access for customization"
    ],
    "Support Issues": [
        "Assigned dedicated success manager",
        "Created custom training program",
        "Set up weekly check-ins",
        "Improved response SLA"
    ],
    "Competition": [
        "Highlighted unique differentiators",
        "Matched competitor pricing",
        "Added requested features quickly",
        "Improved product based on feedback"
    ],
    "Adoption Challenges": [
        "Conducted onboarding workshops",
        "Created internal champions program",
        "Simplified user interface",
        "Provided implementation support"
    ]
}

def generate_customer_interactions(num_customers=100):
    """Generate detailed customer interaction history"""
    interactions = []

    for _ in range(num_customers):
        company = random.choice(COMPANY_NAMES) + f" {random.choice(['Systems', 'Solutions', 'Technologies', 'Corp'])}"
        segment = random.choice(SEGMENTS)
        tenure_months = random.randint(1, 48)

        # Generate 5-20 interactions per customer
        num_interactions = random.randint(5, 20)

        for i in range(num_interactions):
            days_ago = random.randint(1, tenure_months * 30)
            interaction_date = datetime.now() - timedelta(days=days_ago)

            interaction_types = ["Email", "Call", "Meeting", "Support Ticket", "Product Feedback"]
            interaction_type = random.choice(interaction_types)

            # Generate realistic interaction content
            if interaction_type == "Support Ticket":
                topics = ["Bug report", "Feature request", "Integration help", "Performance issue", "Training request"]
                topic = random.choice(topics)
                sentiment = random.choice(["Frustrated", "Neutral", "Satisfied"])
                resolution_time = random.randint(1, 72)  # hours

                content = f"{company} reported {topic.lower()}. Sentiment: {sentiment}. Resolved in {resolution_time}h."

            elif interaction_type == "Meeting":
                meeting_types = ["Quarterly business review", "Training session", "Feature demo", "Renewal discussion"]
                meeting_type = random.choice(meeting_types)
                attendees = random.randint(2, 8)

                content = f"{meeting_type} with {company}. {attendees} attendees. Discussed roadmap and usage patterns."

            elif interaction_type == "Call":
                call_reasons = ["Check-in", "Issue escalation", "Feature question", "Expansion discussion"]
                call_reason = random.choice(call_reasons)
                duration = random.randint(15, 60)

                content = f"{call_reason} call with {company}. Duration: {duration} minutes. {random.choice(['Positive', 'Neutral', 'Needs follow-up'])} outcome."

            else:  # Email or Feedback
                content = f"Communication with {company} regarding {random.choice(['product updates', 'usage tips', 'billing', 'feedback request'])}."

            interactions.append({
                "company_name": company,
                "segment": segment,
                "interaction_date": interaction_date.strftime("%Y-%m-%d"),
                "interaction_type": interaction_type,
                "content": content,
                "customer_tenure_months": tenure_months
            })

    df = pd.DataFrame(interactions)
    df = df.sort_values("interaction_date", ascending=False)
    df.to_csv(DATA_DIR / "customer_interactions.csv", index=False)
    print(f"✅ Generated {len(df)} customer interactions")
    return df

def generate_support_tickets(num_tickets=200):
    """Generate detailed support ticket data"""
    tickets = []

    ticket_categories = {
        "Technical": ["API error", "Integration issue", "Performance slow", "Data sync problem", "Authentication failure"],
        "Feature": ["Missing functionality", "Feature request", "Workflow improvement", "UI/UX feedback"],
        "Billing": ["Invoice question", "Pricing clarification", "Payment issue", "Plan change request"],
        "Training": ["How-to question", "Best practices", "Setup help", "Documentation request"]
    }

    for ticket_id in range(1, num_tickets + 1):
        company = random.choice(COMPANY_NAMES) + f" {random.choice(['Inc', 'LLC', 'Corp'])}"
        segment = random.choice(SEGMENTS)
        category = random.choice(list(ticket_categories.keys()))
        issue = random.choice(ticket_categories[category])

        # Create date
        days_ago = random.randint(1, 365)
        created_date = datetime.now() - timedelta(days=days_ago)

        # Resolution time varies by severity
        severity = random.choice(["Low", "Medium", "High", "Critical"])
        if severity == "Critical":
            resolution_hours = random.randint(1, 12)
        elif severity == "High":
            resolution_hours = random.randint(4, 48)
        elif severity == "Medium":
            resolution_hours = random.randint(12, 96)
        else:
            resolution_hours = random.randint(24, 168)

        resolved_date = created_date + timedelta(hours=resolution_hours)

        # Customer satisfaction
        csat_score = random.randint(1, 5)

        # Generate ticket description
        description = f"{company} ({segment}) reported: {issue}. Severity: {severity}. "

        if severity in ["High", "Critical"]:
            description += "Impacting business operations. "

        # Resolution notes
        resolution_notes = f"Resolved by providing {random.choice(['workaround', 'fix', 'documentation', 'configuration change', 'feature update'])}. "
        resolution_notes += f"Customer satisfaction: {csat_score}/5."

        tickets.append({
            "ticket_id": f"TICKET-{ticket_id:05d}",
            "company_name": company,
            "segment": segment,
            "category": category,
            "issue_type": issue,
            "severity": severity,
            "created_date": created_date.strftime("%Y-%m-%d %H:%M"),
            "resolved_date": resolved_date.strftime("%Y-%m-%d %H:%M"),
            "resolution_hours": resolution_hours,
            "description": description,
            "resolution_notes": resolution_notes,
            "csat_score": csat_score
        })

    df = pd.DataFrame(tickets)
    df.to_csv(DATA_DIR / "support_tickets.csv", index=False)
    print(f"✅ Generated {len(df)} support tickets")
    return df

def generate_success_stories(num_stories=50):
    """Generate customer success stories and case studies"""
    stories = []

    for story_id in range(1, num_stories + 1):
        company = random.choice(COMPANY_NAMES) + " " + random.choice(["Systems", "Solutions", "Technologies"])
        segment = random.choice(SEGMENTS)

        # Pick a challenge and solution
        challenge_category = random.choice(list(CHURN_REASONS.keys()))
        specific_challenge = random.choice(CHURN_REASONS[challenge_category])
        solution = random.choice(RETENTION_STRATEGIES[challenge_category])

        # Generate metrics
        if segment == "Enterprise":
            arr = random.randint(100000, 500000)
            team_size = random.randint(50, 500)
        elif segment == "Commercial":
            arr = random.randint(50000, 150000)
            team_size = random.randint(20, 100)
        else:  # SMB
            arr = random.randint(10000, 60000)
            team_size = random.randint(5, 30)

        # Success metrics
        adoption_before = random.randint(20, 50)
        adoption_after = random.randint(70, 95)
        engagement_increase = random.randint(30, 150)
        support_tickets_reduction = random.randint(40, 80)

        # Create story
        story_title = f"How {company} Overcame {challenge_category} and Increased Adoption by {adoption_after - adoption_before}%"

        story_content = f"""
**Company:** {company}
**Segment:** {segment}
**Team Size:** {team_size} employees
**ARR:** ${arr:,}

**Challenge:**
{company} was facing {specific_challenge.lower()}. Their feature adoption was only {adoption_before}% and they were considering alternatives.

**Solution Implemented:**
{solution}. Our Customer Success team worked closely with their leadership to create a customized plan.

**Results:**
- Feature adoption increased from {adoption_before}% to {adoption_after}%
- User engagement improved by {engagement_increase}%
- Support tickets reduced by {support_tickets_reduction}%
- Successfully renewed and expanded contract
- Became a reference customer and advocate

**Key Learnings:**
Early intervention and personalized support plans are critical for {segment} customers facing {challenge_category.lower()}.
        """.strip()

        stories.append({
            "story_id": f"SUCCESS-{story_id:03d}",
            "company_name": company,
            "segment": segment,
            "title": story_title,
            "challenge_category": challenge_category,
            "specific_challenge": specific_challenge,
            "solution": solution,
            "arr": arr,
            "team_size": team_size,
            "adoption_before": adoption_before,
            "adoption_after": adoption_after,
            "engagement_increase": engagement_increase,
            "support_reduction": support_tickets_reduction,
            "full_story": story_content
        })

    df = pd.DataFrame(stories)
    df.to_csv(DATA_DIR / "success_stories.csv", index=False)
    print(f"✅ Generated {len(df)} success stories")
    return df

def generate_churn_analysis_documents(num_docs=75):
    """Generate detailed churn analysis documents for RAG"""
    analyses = []

    for doc_id in range(1, num_docs + 1):
        company = random.choice(COMPANY_NAMES) + " " + random.choice(["Corp", "Inc", "LLC"])
        segment = random.choice(SEGMENTS)

        # Pick churn reason
        churn_category = random.choice(list(CHURN_REASONS.keys()))
        specific_reason = random.choice(CHURN_REASONS[churn_category])

        # Generate customer profile
        tenure_months = random.randint(3, 36)
        if segment == "Enterprise":
            arr = random.randint(100000, 500000)
            risk_score = random.randint(60, 95)
        elif segment == "Commercial":
            arr = random.randint(50000, 150000)
            risk_score = random.randint(55, 90)
        else:
            arr = random.randint(10000, 60000)
            risk_score = random.randint(50, 85)

        feature_adoption = random.randint(20, 60)
        support_tickets_30d = random.randint(3, 15)
        last_engagement_days = random.randint(7, 60)

        # Generate analysis document
        document = f"""
# Churn Risk Analysis: {company}

## Executive Summary
{company} is a {segment} customer with ${arr:,} ARR and {tenure_months} months of tenure. Current risk score: {risk_score}%.

## Risk Factors
1. **Primary Concern:** {specific_reason}
2. **Feature Adoption:** {feature_adoption}% (Below {segment} average of 70%)
3. **Support Activity:** {support_tickets_30d} tickets in last 30 days
4. **Engagement:** Last interaction {last_engagement_days} days ago

## Detailed Analysis

### Churn Category: {churn_category}
This customer is exhibiting classic signs of {churn_category.lower()}. Specifically, they have expressed concerns about: {specific_reason.lower()}.

### Behavioral Patterns
- Feature adoption has plateaued at {feature_adoption}%
- Support ticket volume {"increasing" if support_tickets_30d > 8 else "stable" if support_tickets_30d > 5 else "decreasing"}
- Engagement frequency {"declining" if last_engagement_days > 30 else "stable"}

### Segment-Specific Insights
For {segment} customers, {churn_category.lower()} typically requires {"immediate executive intervention" if segment == "Enterprise" else "focused customer success efforts" if segment == "Commercial" else "product education and training"}.

## Recommended Actions

### Immediate (Next 7 Days)
1. Schedule {"executive business review" if segment == "Enterprise" else "customer success call"}
2. {"Assign dedicated technical account manager" if segment == "Enterprise" else "Prioritize support tickets"}
3. Review and address {specific_reason.lower()}

### Short-term (30 Days)
1. Increase feature adoption from {feature_adoption}% to {min(feature_adoption + 25, 85)}%
2. Reduce support tickets by {"implementing proactive monitoring" if support_tickets_30d > 8 else "improving documentation"}
3. Establish regular {"weekly" if risk_score > 75 else "bi-weekly"} check-in cadence

### Long-term (90 Days)
1. Position {company} as reference customer
2. Explore expansion opportunities
3. Build executive relationships

## Success Probability
Based on similar {segment} customers with {churn_category.lower()}, intervention at this stage has a {random.randint(65, 85)}% success rate.

## Historical Context
Previous {segment} customers with similar risk profiles who received {random.choice(RETENTION_STRATEGIES[churn_category]).lower()} showed {"significant improvement" if random.random() > 0.5 else "moderate improvement"} in retention metrics.

---
*Analysis Date: {datetime.now().strftime("%Y-%m-%d")}*
*Risk Score: {risk_score}%*
*Segment: {segment}*
        """.strip()

        analyses.append({
            "doc_id": f"ANALYSIS-{doc_id:04d}",
            "company_name": company,
            "segment": segment,
            "churn_category": churn_category,
            "specific_reason": specific_reason,
            "arr": arr,
            "tenure_months": tenure_months,
            "risk_score": risk_score,
            "feature_adoption": feature_adoption,
            "support_tickets_30d": support_tickets_30d,
            "last_engagement_days": last_engagement_days,
            "document": document
        })

    df = pd.DataFrame(analyses)
    df.to_csv(DATA_DIR / "churn_analyses.csv", index=False)

    # Also save as individual text files for easier RAG ingestion
    docs_dir = DATA_DIR / "churn_analysis_docs"
    docs_dir.mkdir(exist_ok=True)

    for _, row in df.iterrows():
        doc_path = docs_dir / f"{row['doc_id']}.txt"
        with open(doc_path, 'w') as f:
            f.write(row['document'])

    print(f"✅ Generated {len(df)} churn analysis documents")
    print(f"✅ Saved individual documents to {docs_dir}")
    return df

def generate_rag_metadata():
    """Generate metadata file for RAG system"""
    metadata = {
        "generated_date": datetime.now().isoformat(),
        "data_files": {
            "customer_interactions": "customer_interactions.csv",
            "support_tickets": "support_tickets.csv",
            "success_stories": "success_stories.csv",
            "churn_analyses": "churn_analyses.csv",
            "churned_customers": "churned_customers_cleaned.csv"
        },
        "document_collections": {
            "churn_analysis_docs": "Individual text documents for RAG ingestion"
        },
        "statistics": {
            "total_companies": len(COMPANY_NAMES),
            "segments": SEGMENTS,
            "churn_categories": list(CHURN_REASONS.keys())
        },
        "usage": {
            "description": "Synthetic data for ChurnGuard AI RAG system",
            "recommended_chunk_size": 500,
            "recommended_overlap": 50
        }
    }

    with open(DATA_DIR / "rag_metadata.json", 'w') as f:
        json.dump(metadata, f, indent=2)

    print(f"✅ Generated RAG metadata file")

def main():
    """Generate all synthetic data"""
    print("🚀 Starting synthetic data generation for RAG system...")
    print("=" * 60)

    # Generate all datasets
    interactions_df = generate_customer_interactions(num_customers=100)
    support_df = generate_support_tickets(num_tickets=200)
    success_df = generate_success_stories(num_stories=50)
    analyses_df = generate_churn_analysis_documents(num_docs=75)

    # Generate metadata
    generate_rag_metadata()

    print("=" * 60)
    print("✨ Data generation complete!")
    print(f"\n📊 Summary:")
    print(f"  - Customer Interactions: {len(interactions_df)} records")
    print(f"  - Support Tickets: {len(support_df)} records")
    print(f"  - Success Stories: {len(success_df)} records")
    print(f"  - Churn Analyses: {len(analyses_df)} records")
    print(f"\n📁 All files saved to: {DATA_DIR.absolute()}")
    print(f"\n🎯 Next steps:")
    print(f"  1. Review generated data in {DATA_DIR}")
    print(f"  2. Run RAG ingestion script to load into vector database")
    print(f"  3. Fine-tune model with generated training data")

if __name__ == "__main__":
    main()
