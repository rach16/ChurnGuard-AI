"""
Quick script to generate golden master dataset
Run from Docker Jupyter environment
"""

import os
import sys

# Set up paths
sys.path.append('/app/src')

from evaluation.synthetic_data_generation import GoldenDatasetGenerator

# Ensure API key is set
if not os.getenv("OPENAI_API_KEY"):
    print("❌ ERROR: OPENAI_API_KEY not set!")
    print("Set it in your Jupyter notebook first:")
    print("  os.environ['OPENAI_API_KEY'] = 'sk-...'")
    sys.exit(1)

print("🚀 Starting Golden Dataset Generation")
print("=" * 80)

# Initialize generator
generator = GoldenDatasetGenerator(data_folder="/app/data/")

# Generate dataset (smaller counts for testing, increase for production)
questions_per_category = {
    "customer_specific": 12,        # Questions about specific customers
    "pattern_analysis": 12,         # Questions about patterns/trends
    "competitive_intelligence": 10, # Questions about competitors
    "financial_analysis": 10,       # Questions about ARR/financial impact
    "segment_analysis": 10          # Questions about segments
}

print(f"\nGenerating {sum(questions_per_category.values())} questions across 5 categories...")
print("This will take 2-3 minutes...\n")

# Generate the dataset
golden_df = generator.generate_golden_dataset(
    questions_per_category=questions_per_category,
    output_file="/app/golden-masters/churn_golden_master.csv"
)

print("\n" + "=" * 80)
print("✅ GENERATION COMPLETE!")
print("=" * 80)
print(f"\n📊 Dataset Statistics:")
print(f"   Total questions: {len(golden_df)}")
print(f"   Categories: {golden_df['query_type'].nunique()}")
print(f"   Output: /app/golden-masters/churn_golden_master.csv")
print(f"\n📝 Sample questions:")
for i, row in golden_df.head(3).iterrows():
    print(f"\n   Q{i+1}: {row['question']}")
    print(f"   Type: {row['query_type']}")
    print(f"   Answer: {row['ground_truth'][:100]}...")

print("\n🎉 Ready for Phase 5: RAGAS Evaluation!")

