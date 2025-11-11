"""
RAGAS Evaluation for Churn RAG System
Comprehensive evaluation using RAGAS metrics
"""

import os
import sys
from pathlib import Path
from typing import List, Dict, Tuple
import pandas as pd
import logging
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_recall,
    context_precision,
    answer_correctness,
    answer_similarity
)
from datasets import Dataset

logger = logging.getLogger(__name__)


class ChurnRAGEvaluator:
    """
    Evaluate customer churn RAG system using RAGAS
    
    Metrics:
    - Faithfulness: Answer grounded in context
    - Answer Relevancy: Relevance to question
    - Context Recall: Retrieved all relevant context
    - Context Precision: Retrieved contexts are relevant
    - Answer Correctness: Factual accuracy
    - Answer Similarity: Semantic similarity to ground truth
    """
    
    def __init__(self):
        """Initialize evaluator with RAGAS metrics"""
        self.metrics = [
            faithfulness,
            answer_relevancy,
            context_recall,
            context_precision,
            answer_correctness,
            answer_similarity
        ]
    
    def create_evaluation_dataset(
        self,
        questions: List[str],
        answers: List[str],
        contexts: List[List[str]],
        ground_truths: List[str]
    ) -> Dataset:
        """
        Create evaluation dataset for RAGAS
        
        Args:
            questions: List of test questions
            answers: Generated answers from RAG system
            contexts: Retrieved contexts for each question
            ground_truths: Ground truth answers
        
        Returns:
            HuggingFace Dataset for evaluation
        """
        data = {
            "question": questions,
            "answer": answers,
            "contexts": contexts,
            "ground_truth": ground_truths
        }
        return Dataset.from_dict(data)
    
    def evaluate(self, dataset: Dataset) -> Dict:
        """
        Run RAGAS evaluation on dataset
        
        Args:
            dataset: Evaluation dataset
        
        Returns:
            Dictionary of metric scores
        """
        logger.info(f"Running RAGAS evaluation on {len(dataset)} samples...")
        
        try:
            result = evaluate(dataset, metrics=self.metrics)
            logger.info("✓ RAGAS evaluation complete")
            return result
        except Exception as e:
            logger.error(f"RAGAS evaluation failed: {e}")
            raise
    
    def evaluate_retrieval_method(
        self,
        method_name: str,
        questions: List[str],
        answers: List[str],
        contexts: List[List[str]],
        ground_truths: List[str]
    ) -> Tuple[Dict, pd.DataFrame]:
        """
        Evaluate a specific retrieval method
        
        Args:
            method_name: Name of the retrieval method
            questions: List of test questions
            answers: Generated answers
            contexts: Retrieved contexts
            ground_truths: Ground truth answers
        
        Returns:
            Tuple of (metrics dict, results dataframe)
        """
        logger.info(f"\n{'='*80}")
        logger.info(f"Evaluating: {method_name}")
        logger.info(f"{'='*80}")
        
        # Create dataset
        dataset = self.create_evaluation_dataset(
            questions=questions,
            answers=answers,
            contexts=contexts,
            ground_truths=ground_truths
        )
        
        # Run evaluation
        result = self.evaluate(dataset)
        
        # Convert to dataframe for easier analysis
        results_df = pd.DataFrame([{
            'method': method_name,
            **{k: v for k, v in result.items() if k != 'per_sample'}
        }])
        
        logger.info(f"\n{method_name} Results:")
        for metric, score in result.items():
            if metric != 'per_sample':
                logger.info(f"  {metric}: {score:.4f}")
        
        return result, results_df
    
    def compare_all_methods(self, all_results: List[pd.DataFrame]) -> pd.DataFrame:
        """
        Compare results across all retrieval methods
        
        Args:
            all_results: List of result dataframes from each method
        
        Returns:
            Combined comparison dataframe
        """
        logger.info(f"\n{'='*80}")
        logger.info("COMPARISON ACROSS ALL METHODS")
        logger.info(f"{'='*80}\n")
        
        # Combine all results
        comparison_df = pd.concat(all_results, ignore_index=True)
        
        # Display comparison
        logger.info("\nComparison Table:")
        logger.info(comparison_df.to_string(index=False))
        
        # Find best method for each metric
        logger.info("\n\nBest Method per Metric:")
        for col in comparison_df.columns:
            if col != 'method':
                best_idx = comparison_df[col].idxmax()
                best_method = comparison_df.loc[best_idx, 'method']
                best_score = comparison_df.loc[best_idx, col]
                logger.info(f"  {col}: {best_method} ({best_score:.4f})")
        
        return comparison_df
    
    def save_results(self, comparison_df: pd.DataFrame, output_dir: str = "metrics"):
        """
        Save evaluation results to CSV
        
        Args:
            comparison_df: Comparison dataframe
            output_dir: Output directory for results
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Save comparison
        comparison_file = output_path / "evaluation_comparison.csv"
        comparison_df.to_csv(comparison_file, index=False)
        logger.info(f"\n✓ Saved comparison to: {comparison_file}")
        
        # Save individual method results
        for _, row in comparison_df.iterrows():
            method_name = row['method'].replace(' ', '_').replace('-', '_').lower()
            method_file = output_path / f"{method_name}_results.csv"
            row.to_frame().T.to_csv(method_file, index=False)
            logger.info(f"✓ Saved {row['method']} results to: {method_file}")
        
        logger.info(f"\n✅ All results saved to {output_dir}/")
    
    def baseline_evaluation(self) -> Dict:
        """
        Run baseline evaluation on test set
        
        Returns:
            Baseline metric scores
        """
        logger.warning("baseline_evaluation() is deprecated. Use evaluate_retrieval_method() instead.")
        return {}

