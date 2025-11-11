"""
Run RAGAS Evaluation for All Retrieval Methods
Executes comprehensive evaluation across all 5 retrieval strategies
"""

import os
import sys
from pathlib import Path
import pandas as pd
import logging
from typing import List, Dict
from tqdm import tqdm

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from core.rag_retrievers import ChurnRAGRetriever
from evaluation.ragas_evaluation import ChurnRAGEvaluator
from langchain_openai import ChatOpenAI

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EvaluationRunner:
    """
    Run comprehensive evaluation across all retrieval methods
    """
    
    def __init__(self, data_folder: str = "data/", golden_dataset_path: str = "golden-masters/churn_golden_master.csv"):
        """
        Initialize evaluation runner
        
        Args:
            data_folder: Path to data folder
            golden_dataset_path: Path to golden master dataset
        """
        self.data_folder = data_folder
        self.golden_dataset_path = golden_dataset_path
        
        # Initialize components
        logger.info("Initializing RAG retriever...")
        self.retriever = ChurnRAGRetriever()
        self.retriever.load_and_process_documents(data_folder)
        
        logger.info("Initializing evaluator...")
        self.evaluator = ChurnRAGEvaluator()
        
        logger.info("Initializing LLM for answer generation...")
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        
        # Load golden dataset
        logger.info(f"Loading golden dataset from {golden_dataset_path}...")
        self.golden_df = pd.read_csv(golden_dataset_path)
        logger.info(f"✓ Loaded {len(self.golden_df)} test questions")
    
    def generate_answer(self, question: str, contexts: List[str]) -> str:
        """
        Generate answer using LLM and retrieved contexts
        
        Args:
            question: The question to answer
            contexts: Retrieved context documents
        
        Returns:
            Generated answer
        """
        # Build context string
        context_str = "\n\n".join([f"Context {i+1}:\n{ctx}" for i, ctx in enumerate(contexts)])
        
        # Create prompt
        prompt = f"""You are a customer success analyst. Answer the following question based ONLY on the provided contexts.

Question: {question}

Contexts:
{context_str}

Provide a comprehensive answer based on the contexts above:"""
        
        try:
            response = self.llm.invoke(prompt)
            return response.content
        except Exception as e:
            logger.error(f"Answer generation failed: {e}")
            return f"Error generating answer: {str(e)}"
    
    def evaluate_method(self, method_name: str, retrieval_func) -> pd.DataFrame:
        """
        Evaluate a single retrieval method
        
        Args:
            method_name: Name of the retrieval method
            retrieval_func: Retrieval function to call
        
        Returns:
            Results dataframe
        """
        logger.info(f"\n{'='*80}")
        logger.info(f"EVALUATING: {method_name}")
        logger.info(f"{'='*80}\n")
        
        questions = []
        answers = []
        contexts = []
        ground_truths = []
        
        # Process each question
        for idx, row in tqdm(self.golden_df.iterrows(), total=len(self.golden_df), desc=f"Processing {method_name}"):
            question = row['question']
            ground_truth = row['ground_truth']
            
            try:
                # Retrieve documents
                docs = retrieval_func(question, k=5)
                
                # Extract contexts
                doc_contexts = [doc.page_content for doc in docs]
                
                # Generate answer
                answer = self.generate_answer(question, doc_contexts)
                
                # Store results
                questions.append(question)
                answers.append(answer)
                contexts.append(doc_contexts)
                ground_truths.append(ground_truth)
                
            except Exception as e:
                logger.error(f"Error processing question {idx}: {e}")
                # Add empty/error results to maintain alignment
                questions.append(question)
                answers.append(f"Error: {str(e)}")
                contexts.append(["Error retrieving context"])
                ground_truths.append(ground_truth)
        
        # Run RAGAS evaluation
        logger.info(f"\nRunning RAGAS evaluation for {method_name}...")
        _, results_df = self.evaluator.evaluate_retrieval_method(
            method_name=method_name,
            questions=questions,
            answers=answers,
            contexts=contexts,
            ground_truths=ground_truths
        )
        
        return results_df
    
    def run_all_evaluations(self) -> pd.DataFrame:
        """
        Run evaluation for all retrieval methods
        
        Returns:
            Combined comparison dataframe
        """
        logger.info("\n" + "="*80)
        logger.info("STARTING COMPREHENSIVE EVALUATION")
        logger.info("="*80 + "\n")
        
        all_results = []
        
        # 1. Naive Retrieval
        logger.info("\n🔹 Method 1/5: Naive Retrieval")
        results_naive = self.evaluate_method(
            "Naive Retrieval",
            self.retriever.naive_retrieval
        )
        all_results.append(results_naive)
        
        # 2. Multi-Query Retrieval
        logger.info("\n🔹 Method 2/5: Multi-Query Retrieval")
        results_multi = self.evaluate_method(
            "Multi-Query Retrieval",
            self.retriever.multi_query_retrieval
        )
        all_results.append(results_multi)
        
        # 3. Contextual Compression
        logger.info("\n🔹 Method 3/5: Contextual Compression")
        results_compression = self.evaluate_method(
            "Contextual Compression",
            self.retriever.contextual_compression_retrieval
        )
        all_results.append(results_compression)
        
        # 4. Parent Document Retrieval
        logger.info("\n🔹 Method 4/5: Parent Document Retrieval")
        results_parent = self.evaluate_method(
            "Parent Document Retrieval",
            self.retriever.parent_document_retrieval
        )
        all_results.append(results_parent)
        
        # 5. Reranking
        logger.info("\n🔹 Method 5/5: Reranking (Cohere)")
        results_rerank = self.evaluate_method(
            "Reranking",
            self.retriever.rerank_retrieval
        )
        all_results.append(results_rerank)
        
        # Compare all methods
        logger.info("\n" + "="*80)
        logger.info("GENERATING COMPARISON")
        logger.info("="*80)
        comparison_df = self.evaluator.compare_all_methods(all_results)
        
        # Save results
        self.evaluator.save_results(comparison_df)
        
        logger.info("\n" + "="*80)
        logger.info("✅ EVALUATION COMPLETE!")
        logger.info("="*80 + "\n")
        
        return comparison_df


def main():
    """Main execution function"""
    # Check for required API keys
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("❌ OPENAI_API_KEY environment variable not set!")
        sys.exit(1)
    
    # Check if Cohere key is set
    if not os.getenv("COHERE_API_KEY"):
        logger.warning("⚠️  COHERE_API_KEY not set. Reranking will fall back to contextual compression.")
    
    try:
        # Initialize and run evaluation
        runner = EvaluationRunner()
        comparison_df = runner.run_all_evaluations()
        
        # Display summary
        logger.info("\n📊 EVALUATION SUMMARY:")
        logger.info("\n" + comparison_df.to_string(index=False))
        
        # Identify best overall method
        metric_cols = [col for col in comparison_df.columns if col != 'method']
        comparison_df['average_score'] = comparison_df[metric_cols].mean(axis=1)
        best_method = comparison_df.loc[comparison_df['average_score'].idxmax(), 'method']
        best_score = comparison_df['average_score'].max()
        
        logger.info(f"\n🏆 Best Overall Method: {best_method}")
        logger.info(f"   Average Score: {best_score:.4f}")
        
        logger.info("\n✅ Results saved to metrics/ folder")
        logger.info("\nNext steps:")
        logger.info("  1. Review metrics/evaluation_comparison.csv")
        logger.info("  2. Generate visualizations (metrics_viz.py)")
        logger.info("  3. Write implementation report")
        
    except Exception as e:
        logger.error(f"❌ Evaluation failed: {e}")
        raise


if __name__ == "__main__":
    main()

