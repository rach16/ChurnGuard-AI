"""
Metrics Visualization
Create heatmaps and charts for RAGAS evaluation results
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class RAGMetricsVisualizer:
    """
    Visualize RAG evaluation metrics
    
    Creates:
    - Heatmaps for retriever comparison
    - Performance charts
    - Metric trends
    """
    
    def __init__(self, output_dir: str = "metrics/visualizations/"):
        """Initialize visualizer with output directory"""
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)
        
        # Set visualization style
        sns.set_theme(style="whitegrid")
        plt.rcParams["figure.figsize"] = (12, 8)
        plt.rcParams["font.size"] = 10
    
    def create_comparison_heatmap(
        self,
        metrics_df: pd.DataFrame,
        output_filename: str = "retriever_comparison_heatmap.png"
    ):
        """
        Create heatmap comparing retriever performance
        
        Args:
            metrics_df: DataFrame with retriever metrics (methods as rows, metrics as columns)
            output_filename: Output filename for chart
        """
        logger.info(f"Creating comparison heatmap...")
        
        # Prepare data for heatmap
        heatmap_df = metrics_df.set_index('method')
        
        # Create figure
        plt.figure(figsize=(14, 6))
        
        # Create heatmap
        sns.heatmap(
            heatmap_df,
            annot=True,
            fmt='.4f',
            cmap='RdYlGn',
            center=0.5,
            vmin=0,
            vmax=1,
            cbar_kws={'label': 'Score'},
            linewidths=0.5
        )
        
        plt.title('Retrieval Methods Performance Comparison (RAGAS Metrics)', fontsize=16, fontweight='bold', pad=20)
        plt.xlabel('Metrics', fontsize=12, fontweight='bold')
        plt.ylabel('Retrieval Methods', fontsize=12, fontweight='bold')
        plt.xticks(rotation=45, ha='right')
        plt.yticks(rotation=0)
        plt.tight_layout()
        
        # Save
        output_path = self.output_dir / output_filename
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✓ Saved heatmap to: {output_path}")
    
    def create_performance_bars(
        self,
        metrics_df: pd.DataFrame,
        output_filename: str = "performance_bars.png"
    ):
        """
        Create bar charts comparing retrieval methods
        
        Args:
            metrics_df: DataFrame with metrics
            output_filename: Output filename
        """
        logger.info(f"Creating performance bar charts...")
        
        # Prepare data
        plot_df = metrics_df.set_index('method')
        
        # Create figure with subplots
        fig, axes = plt.subplots(2, 3, figsize=(18, 10))
        axes = axes.flatten()
        
        # Plot each metric
        for idx, metric in enumerate(plot_df.columns):
            ax = axes[idx]
            
            # Sort by metric value
            sorted_data = plot_df[metric].sort_values(ascending=False)
            
            # Create bar chart
            bars = ax.barh(range(len(sorted_data)), sorted_data.values)
            
            # Color bars by performance
            colors = ['#2ecc71' if v >= 0.8 else '#f39c12' if v >= 0.6 else '#e74c3c' 
                     for v in sorted_data.values]
            for bar, color in zip(bars, colors):
                bar.set_color(color)
            
            # Formatting
            ax.set_yticks(range(len(sorted_data)))
            ax.set_yticklabels(sorted_data.index)
            ax.set_xlabel('Score', fontweight='bold')
            ax.set_title(metric.replace('_', ' ').title(), fontweight='bold')
            ax.set_xlim(0, 1)
            ax.grid(axis='x', alpha=0.3)
            
            # Add value labels
            for i, v in enumerate(sorted_data.values):
                ax.text(v + 0.01, i, f'{v:.3f}', va='center', fontsize=9)
        
        # Remove extra subplot
        if len(plot_df.columns) < len(axes):
            for idx in range(len(plot_df.columns), len(axes)):
                fig.delaxes(axes[idx])
        
        plt.suptitle('Retrieval Methods Performance by Metric', fontsize=16, fontweight='bold', y=0.995)
        plt.tight_layout()
        
        # Save
        output_path = self.output_dir / output_filename
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✓ Saved bar charts to: {output_path}")
    
    def create_radar_chart(
        self,
        metrics_df: pd.DataFrame,
        output_filename: str = "radar_chart.png"
    ):
        """
        Create radar chart for multi-dimensional comparison
        
        Args:
            metrics_df: DataFrame with metrics
            output_filename: Output filename
        """
        logger.info(f"Creating radar chart...")
        
        # Prepare data
        methods = metrics_df['method'].tolist()
        metrics = [col for col in metrics_df.columns if col != 'method']
        
        # Create figure
        fig = plt.figure(figsize=(12, 10))
        ax = fig.add_subplot(111, projection='polar')
        
        # Set up angles
        angles = np.linspace(0, 2 * np.pi, len(metrics), endpoint=False).tolist()
        angles += angles[:1]  # Complete the circle
        
        # Plot each method
        colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6']
        
        for idx, (_, row) in enumerate(metrics_df.iterrows()):
            values = row[metrics].tolist()
            values += values[:1]  # Complete the circle
            
            ax.plot(angles, values, 'o-', linewidth=2, label=row['method'], color=colors[idx % len(colors)])
            ax.fill(angles, values, alpha=0.15, color=colors[idx % len(colors)])
        
        # Fix labels
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels([m.replace('_', ' ').title() for m in metrics], fontsize=10)
        ax.set_ylim(0, 1)
        ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
        ax.set_yticklabels(['0.2', '0.4', '0.6', '0.8', '1.0'], fontsize=9)
        ax.grid(True, linestyle='--', alpha=0.7)
        
        plt.title('Multi-Dimensional Performance Comparison\n(Radar Chart)', 
                 fontsize=16, fontweight='bold', pad=20)
        plt.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1), fontsize=10)
        plt.tight_layout()
        
        # Save
        output_path = self.output_dir / output_filename
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✓ Saved radar chart to: {output_path}")
    
    def generate_evaluation_report(
        self,
        metrics_df: pd.DataFrame,
        output_filename: str = "evaluation_report.html"
    ):
        """
        Generate HTML report with all visualizations
        
        Args:
            metrics_df: DataFrame with all metrics
            output_filename: Output filename for HTML report
        """
        logger.info(f"Generating evaluation report...")
        
        # Create all visualizations
        self.create_comparison_heatmap(metrics_df)
        self.create_performance_bars(metrics_df)
        self.create_radar_chart(metrics_df)
        
        # Generate HTML report
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>RAGAS Evaluation Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 30px; }}
        .metric-table {{ width: 100%; border-collapse: collapse; margin: 20px 0; background: white; }}
        .metric-table th, .metric-table td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
        .metric-table th {{ background-color: #3498db; color: white; font-weight: bold; }}
        .metric-table tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .visualization {{ margin: 30px 0; text-align: center; background: white; padding: 20px; border-radius: 8px; }}
        .visualization img {{ max-width: 100%; height: auto; border: 1px solid #ddd; }}
        .best-method {{ background-color: #2ecc71; color: white; padding: 15px; border-radius: 5px; margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>📊 RAGAS Evaluation Report</h1>
    <p><strong>Date:</strong> {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p><strong>Methods Evaluated:</strong> {len(metrics_df)}</p>
    
    <h2>🏆 Best Performing Method</h2>
    <div class="best-method">
        <strong>Method:</strong> {metrics_df.set_index('method').mean(axis=1).idxmax()}<br>
        <strong>Average Score:</strong> {metrics_df.set_index('method').mean(axis=1).max():.4f}
    </div>
    
    <h2>📈 Performance Metrics Table</h2>
    {metrics_df.to_html(classes='metric-table', index=False, float_format='%.4f')}
    
    <h2>🔥 Heatmap Comparison</h2>
    <div class="visualization">
        <img src="retriever_comparison_heatmap.png" alt="Heatmap">
    </div>
    
    <h2>📊 Performance Bars</h2>
    <div class="visualization">
        <img src="performance_bars.png" alt="Bar Charts">
    </div>
    
    <h2>🎯 Radar Chart</h2>
    <div class="visualization">
        <img src="radar_chart.png" alt="Radar Chart">
    </div>
</body>
</html>
"""
        
        # Save HTML
        output_path = self.output_dir / output_filename
        with open(output_path, 'w') as f:
            f.write(html_content)
        
        logger.info(f"✓ Saved HTML report to: {output_path}")


def visualize_evaluation_results(metrics_file: str = "metrics/evaluation_comparison.csv"):
    """
    Load and visualize evaluation results
    
    Args:
        metrics_file: Path to metrics CSV file
    """
    logger.info(f"\n{'='*80}")
    logger.info("GENERATING VISUALIZATIONS")
    logger.info(f"{'='*80}\n")
    
    # Load metrics
    logger.info(f"Loading metrics from {metrics_file}...")
    df = pd.read_csv(metrics_file)
    logger.info(f"✓ Loaded metrics for {len(df)} methods")
    
    # Create visualizer
    visualizer = RAGMetricsVisualizer()
    
    # Generate all visualizations
    visualizer.generate_evaluation_report(df)
    
    logger.info(f"\n✅ All visualizations generated!")
    logger.info(f"📁 Saved to: metrics/visualizations/")


if __name__ == "__main__":
    # Run visualization if called directly
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    metrics_file = sys.argv[1] if len(sys.argv) > 1 else "metrics/evaluation_comparison.csv"
    visualize_evaluation_results(metrics_file)

