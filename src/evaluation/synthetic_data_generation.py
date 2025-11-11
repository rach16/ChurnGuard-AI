"""
Synthetic Data Generation for Golden Master Dataset
Generates diverse test questions with ground truth answers for RAG evaluation
"""

import os
import sys
from pathlib import Path
import pandas as pd
import json
import logging
from typing import List, Dict, Tuple
from openai import OpenAI

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))
from utils.data_loader import ChurnDataLoader

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GoldenDatasetGenerator:
    """
    Generate synthetic questions and ground truth answers for evaluation
    """
    
    def __init__(self, data_folder: str = "data/"):
        """
        Initialize with data folder path
        
        Args:
            data_folder: Path to folder containing churn data
        """
        self.data_folder = Path(data_folder)
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        # Load and analyze data
        logger.info(f"Loading data from {data_folder}")
        self.df = pd.read_csv(self.data_folder / "churned_customers_cleaned.csv")
        
        # Clean Amount column for numeric operations (do this once at load time)
        self.df['Amount_Clean'] = self.df['Amount'].str.replace('$', '').str.replace(',', '').astype(float)
        
        # Data analysis for context
        self.data_summary = self._analyze_data()
        
    def _analyze_data(self) -> Dict:
        """Analyze churn data to understand patterns"""
        logger.info("Analyzing churn data patterns...")
        
        summary = {
            "total_customers": len(self.df),
            "segments": self.df['Account Segment'].value_counts().to_dict(),
            "churn_reasons": self.df['Primary Outcome Reason'].value_counts().to_dict(),
            "top_customers": self.df.nlargest(10, 'Amount_Clean')['Account Name'].tolist(),
            "competitors": [],
            "avg_tenure": self.df['Tenure (years)'].mean(),
            "total_arr_lost": self.df['Amount_Clean'].sum()
        }
        
        # Get unique competitors
        comp1 = self.df['Competitor 1'].dropna().unique().tolist()
        comp2 = self.df['Competitor 2'].dropna().unique().tolist()
        summary['competitors'] = list(set([c for c in comp1 + comp2 if c and c != 'None mentioned']))[:10]
        
        logger.info(f"✓ Analyzed {summary['total_customers']} customers")
        logger.info(f"✓ Found {len(summary['churn_reasons'])} unique churn reasons")
        logger.info(f"✓ Found {len(summary['competitors'])} competitors")
        
        return summary
    
    def _generate_questions_by_category(self, category: str, count: int) -> List[Dict]:
        """
        Generate questions for a specific category using GPT-4
        
        Args:
            category: Question category (customer_specific, pattern_analysis, etc.)
            count: Number of questions to generate
        
        Returns:
            List of question dictionaries
        """
        logger.info(f"Generating {count} {category} questions...")
        
        # Category-specific prompts
        prompts = {
            "customer_specific": f"""Generate {count} specific questions about individual customers who churned.
Use these actual customer names: {', '.join(self.data_summary['top_customers'][:15])}

Questions should ask about:
- Why specific customers churned
- What products they used
- Their segment and tenure
- Competitive switches

Format each question on a new line, numbered.""",
            
            "pattern_analysis": f"""Generate {count} questions about churn patterns and trends.
Data context:
- Segments: {', '.join(self.data_summary['segments'].keys())}
- Main reasons: {', '.join(list(self.data_summary['churn_reasons'].keys())[:5])}

Questions should ask about:
- Common patterns across segments
- Churn reasons by segment
- Trends over time
- Risk factors

Format each question on a new line, numbered.""",
            
            "competitive_intelligence": f"""Generate {count} questions about competitors and competitive dynamics.
Known competitors: {', '.join(self.data_summary['competitors'][:10])}

Questions should ask about:
- Which competitors we're losing to
- Why customers switch to competitors
- Competitive patterns by segment
- Alternative solutions

Format each question on a new line, numbered.""",
            
            "financial_analysis": f"""Generate {count} questions about financial impact of churn.
Context:
- Total ARR lost: ${self.data_summary['total_arr_lost']:,.0f}
- Average customer value varies by segment

Questions should ask about:
- ARR loss by segment
- Average churn value
- High-value customer patterns
- Financial impact analysis

Format each question on a new line, numbered.""",
            
            "segment_analysis": f"""Generate {count} questions about segment-specific churn.
Segments: {', '.join(self.data_summary['segments'].keys())}

Questions should ask about:
- Segment comparison
- Unique patterns per segment
- Segment-specific risks
- Tenure patterns by segment

Format each question on a new line, numbered."""
        }
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a customer success expert generating realistic test questions for a churn analysis system."},
                    {"role": "user", "content": prompts[category]}
                ],
                temperature=0.8,
                max_tokens=1000
            )
            
            # Parse questions
            content = response.choices[0].message.content
            questions = []
            for line in content.strip().split('\n'):
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith('-')):
                    # Remove numbering
                    question = line.split('.', 1)[-1].strip() if '.' in line else line.strip('- ')
                    if question:
                        questions.append({
                            "question": question,
                            "query_type": category,
                            "difficulty": "medium"
                        })
            
            logger.info(f"✓ Generated {len(questions)} {category} questions")
            return questions[:count]  # Ensure we don't exceed requested count
            
        except Exception as e:
            logger.error(f"Failed to generate {category} questions: {e}")
            return []
    
    def _generate_ground_truth(self, question: str, query_type: str) -> Tuple[str, List[str]]:
        """
        Generate ground truth answer using actual data
        
        Args:
            question: The question to answer
            query_type: Type of question
        
        Returns:
            Tuple of (answer, expected_context_sources)
        """
        # Get relevant data context
        context = self._get_relevant_context(question, query_type)
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": """You are a data analyst providing accurate answers based ONLY on the provided data.
Be specific with numbers, customer names, and facts from the data.
If information isn't in the data, say so clearly."""},
                    {"role": "user", "content": f"""Question: {question}

Data Context:
{context}

Provide a comprehensive, factual answer based ONLY on this data."""}
                ],
                temperature=0.3,  # Lower temperature for factual accuracy
                max_tokens=500
            )
            
            answer = response.choices[0].message.content
            
            # Extract expected sources
            sources = self._extract_expected_sources(question, context, query_type)
            
            return answer, sources
            
        except Exception as e:
            logger.error(f"Failed to generate ground truth: {e}")
            return "Error generating answer", []
    
    def _get_relevant_context(self, question: str, query_type: str) -> str:
        """Extract relevant data context for a question"""
        question_lower = question.lower()
        context_parts = []
        
        # Check for specific customer names
        for customer in self.df['Account Name'].unique():
            if customer.lower() in question_lower:
                customer_data = self.df[self.df['Account Name'] == customer].iloc[0]
                
                # Handle potentially missing Lost Opportunity Details
                details = customer_data.get('Lost Opportunity Details', '')
                if pd.isna(details):
                    details = 'No details provided'
                else:
                    details = str(details)[:200] + '...' if len(str(details)) > 200 else str(details)
                
                context_parts.append(f"""
Customer: {customer}
Segment: {customer_data['Account Segment']}
Churn Reason: {customer_data['Primary Outcome Reason']}
Sub Reason: {customer_data['Outcome Sub Reason']}
ARR Lost: {customer_data['Amount']}
Tenure: {customer_data['Tenure (years)']} years
Products: {customer_data['Products (Rollup)']}
Competitor 1: {customer_data['Competitor 1']}
Competitor 2: {customer_data['Competitor 2']}
Details: {details}
""")
                break
        
        # Add segment data if mentioned
        for segment in self.data_summary['segments'].keys():
            if segment.lower() in question_lower:
                segment_data = self.df[self.df['Account Segment'] == segment]
                context_parts.append(f"""
{segment} Segment Summary:
- Total customers: {len(segment_data)}
- Top churn reasons: {segment_data['Primary Outcome Reason'].value_counts().head(3).to_dict()}
- Average tenure: {segment_data['Tenure (years)'].mean():.1f} years
- Total ARR lost: ${segment_data['Amount_Clean'].sum():,.0f}
""")
                break
        
        # Add churn reason data if relevant
        for reason in self.data_summary['churn_reasons'].keys():
            if reason.lower() in question_lower:
                reason_data = self.df[self.df['Primary Outcome Reason'] == reason]
                context_parts.append(f"""
{reason} Churn Reason Summary:
- Total customers: {len(reason_data)}
- Segments affected: {reason_data['Account Segment'].value_counts().to_dict()}
- Average ARR: ${reason_data['Amount_Clean'].mean():,.0f}
""")
                break
        
        # Add general summary if no specific context found
        if not context_parts:
            context_parts.append(f"""
Overall Churn Summary:
- Total customers: {self.data_summary['total_customers']}
- Segments: {self.data_summary['segments']}
- Top 3 churn reasons: {dict(list(self.data_summary['churn_reasons'].items())[:3])}
- Average tenure: {self.data_summary['avg_tenure']:.1f} years
- Total ARR lost: ${self.data_summary['total_arr_lost']:,.0f}
""")
        
        return "\n".join(context_parts)
    
    def _extract_expected_sources(self, question: str, context: str, query_type: str) -> List[str]:
        """Extract expected document sources for the question"""
        sources = []
        question_lower = question.lower()
        
        # Customer names
        for customer in self.df['Account Name'].unique():
            if customer.lower() in question_lower or customer in context:
                sources.append(customer)
        
        # If no specific customers, add category-based sources
        if not sources:
            if query_type == "pattern_analysis":
                sources = ["multiple_customers", "aggregate_data"]
            elif query_type == "competitive_intelligence":
                sources = ["competitor_data", "switching_patterns"]
            elif query_type == "financial_analysis":
                sources = ["financial_data", "arr_analysis"]
            elif query_type == "segment_analysis":
                sources = ["segment_data", "comparative_analysis"]
        
        return sources[:5]  # Limit to top 5 sources
    
    def generate_golden_dataset(
        self,
        questions_per_category: Dict[str, int] = None,
        output_file: str = "golden-masters/churn_golden_master.csv"
    ) -> pd.DataFrame:
        """
        Generate complete golden master dataset
        
        Args:
            questions_per_category: Dict mapping category to question count
            output_file: Path to save the dataset
            
        Returns:
            DataFrame with questions and ground truth
        """
        if questions_per_category is None:
            questions_per_category = {
                "customer_specific": 20,
                "pattern_analysis": 20,
                "competitive_intelligence": 15,
                "financial_analysis": 15,
                "segment_analysis": 15
            }
        
        logger.info("=" * 80)
        logger.info("🚀 GENERATING GOLDEN MASTER DATASET")
        logger.info("=" * 80)
        
        all_questions = []
        
        # Generate questions for each category
        for category, count in questions_per_category.items():
            questions = self._generate_questions_by_category(category, count)
            all_questions.extend(questions)
        
        logger.info(f"\n✓ Generated {len(all_questions)} total questions")
        logger.info(f"Now generating ground truth answers...\n")
        
        # Generate ground truth for each question
        golden_data = []
        for i, q_data in enumerate(all_questions, 1):
            logger.info(f"Processing {i}/{len(all_questions)}: {q_data['question'][:60]}...")
            
            answer, sources = self._generate_ground_truth(
                q_data['question'],
                q_data['query_type']
            )
            
            golden_data.append({
                "question": q_data['question'],
                "ground_truth": answer,
                "query_type": q_data['query_type'],
                "expected_context": ",".join(sources),
                "difficulty": q_data['difficulty']
            })
        
        # Create DataFrame
        golden_df = pd.DataFrame(golden_data)
        
        # Save to CSV
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        golden_df.to_csv(output_path, index=False)
        
        logger.info("\n" + "=" * 80)
        logger.info(f"✅ GOLDEN MASTER DATASET CREATED")
        logger.info("=" * 80)
        logger.info(f"📊 Total questions: {len(golden_df)}")
        logger.info(f"📁 Saved to: {output_path}")
        logger.info(f"\nBreakdown by category:")
        for category, count in golden_df['query_type'].value_counts().items():
            logger.info(f"   - {category}: {count} questions")
        
        return golden_df


def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate golden master dataset for RAG evaluation")
    parser.add_argument(
        "--data-folder",
        type=str,
        default="data/",
        help="Path to data folder (default: data/)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="golden-masters/churn_golden_master.csv",
        help="Output file path (default: golden-masters/churn_golden_master.csv)"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=15,
        help="Questions per category (default: 15)"
    )
    
    args = parser.parse_args()
    
    # Check for API key
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("❌ OPENAI_API_KEY environment variable not set!")
        sys.exit(1)
    
    # Generate dataset
    generator = GoldenDatasetGenerator(args.data_folder)
    
    questions_per_category = {
        "customer_specific": args.count,
        "pattern_analysis": args.count,
        "competitive_intelligence": args.count,
        "financial_analysis": args.count,
        "segment_analysis": args.count
    }
    
    golden_df = generator.generate_golden_dataset(
        questions_per_category=questions_per_category,
        output_file=args.output
    )
    
    print("\n✅ Golden dataset generation complete!")
    print(f"📁 File: {args.output}")
    print(f"📊 Total: {len(golden_df)} questions")


if __name__ == "__main__":
    main()
