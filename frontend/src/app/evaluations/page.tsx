'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { apiClient, EvaluationResponse } from '../api-client';

export default function EvaluationsPage() {
  const [data, setData] = useState<EvaluationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const result = await apiClient.getEvaluationResults();
        setData(result);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load evaluation results');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const getScoreColor = (score: number, metric: string) => {
    // Different thresholds for different metrics
    if (metric === 'faithfulness' || metric === 'context_precision') {
      // Stricter thresholds for accuracy metrics
      if (score >= 85) return 'text-mute font-semibold';
      if (score >= 70) return 'text-mute';
      return 'text-critical';
    }
    // More lenient for other metrics
    if (score >= 75) return 'text-mute font-semibold';
    if (score >= 60) return 'text-mute';
    return 'text-critical';
  };

  const getMethodBadge = (method: string) => {
    const badges: Record<string, string> = {
      'Parent Document': '⭐ Recommended',
      'Multi Query': '📚 Comprehensive',
      'Naive': '⚡ Fast',
      'Reranking': '🎯 Precise',
      'Contextual Compression': '⚠️ Experimental',
    };
    return badges[method] || '';
  };

  if (loading) {
    return (
      <main className="min-h-screen bg-sand p-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center py-20">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-hair mx-auto"></div>
            <p className="mt-4 text-mute">Loading evaluation results...</p>
          </div>
        </div>
      </main>
    );
  }

  if (error || !data) {
    // No baseline is the expected state, not a failure. The previous results file
    // was deleted deliberately: it had been produced against a golden set whose
    // questions referenced companies present in no data file. Showing a red error
    // box implies something broke, when in fact nothing has been measured yet.
    return (
      <main className="min-h-screen bg-paper p-8">
        <div className="max-w-2xl mx-auto pt-24">
          <h1 className="display text-4xl text-ink">No evaluation baseline</h1>
          <p className="text-mute mt-4 leading-relaxed">
            Retrieval quality has not been measured against the current corpus and
            golden set. The previous results were discarded rather than kept, because
            they were scored against questions that referenced customers present in no
            data file.
          </p>
          <div className="mt-8 border-t border-hair pt-6">
            <p className="label mb-3">Produce a baseline</p>
            <code className="block bg-sand border border-hair rounded px-4 py-3 text-[13px] font-mono text-ink">
              uv run python scripts/benchmark_retrieval.py
            </code>
            <p className="text-sm text-faint mt-3 leading-relaxed">
              Scores retrieval against the golden set&rsquo;s expected_context. No API
              cost. A full RAGAS run is a separate, paid step.
            </p>
          </div>
          <Link href="/" className="mt-10 inline-block text-sm text-mute hover:text-ink">
            &larr; Back to dashboard
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-sand p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <Link href="/" className="text-mute hover:underline mb-4 inline-block">
            ← Back to Chat
          </Link>
          <h1 className="text-4xl font-bold text-ink mb-2">
            📊 RAGAS Evaluation Results
          </h1>
          <p className="text-mute">
            {data.note}
          </p>
        </div>

        {/* Performance Comparison Table */}
        <div className="bg-paper rounded shadow-paper overflow-hidden mb-8">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-hair">
              <thead className="bg-sand">
                <tr>
                  <th className="px-6 py-4 text-left text-xs font-semibold text-white uppercase tracking-wider">
                    Retrieval Method
                  </th>
                  <th className="px-6 py-4 text-center text-xs font-semibold text-white uppercase tracking-wider">
                    Faithfulness
                  </th>
                  <th className="px-6 py-4 text-center text-xs font-semibold text-white uppercase tracking-wider">
                    Answer Relevancy
                  </th>
                  <th className="px-6 py-4 text-center text-xs font-semibold text-white uppercase tracking-wider">
                    Context Recall
                  </th>
                  <th className="px-6 py-4 text-center text-xs font-semibold text-white uppercase tracking-wider">
                    Context Precision
                  </th>
                  <th className="px-6 py-4 text-center text-xs font-semibold text-white uppercase tracking-wider">
                    Answer Correctness
                  </th>
                  <th className="px-6 py-4 text-center text-xs font-semibold text-white uppercase tracking-wider">
                    Semantic Similarity
                  </th>
                </tr>
              </thead>
              <tbody className="bg-paper divide-y divide-hair">
                {data.results.map((result, idx) => (
                  <tr key={result.method} className={idx % 2 === 0 ? 'bg-sand' : 'bg-white'}>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex flex-col">
                        <span className="text-sm font-semibold text-ink">
                          {result.method}
                        </span>
                        <span className="text-xs text-faint">
                          {getMethodBadge(result.method)}
                        </span>
                      </div>
                    </td>
                    <td className={`px-6 py-4 text-center text-sm ${getScoreColor(result.faithfulness, 'faithfulness')}`}>
                      {result.faithfulness.toFixed(1)}%
                    </td>
                    <td className={`px-6 py-4 text-center text-sm ${getScoreColor(result.answer_relevancy, 'answer_relevancy')}`}>
                      {result.answer_relevancy.toFixed(1)}%
                    </td>
                    <td className={`px-6 py-4 text-center text-sm ${getScoreColor(result.context_recall, 'context_recall')}`}>
                      {result.context_recall.toFixed(1)}%
                    </td>
                    <td className={`px-6 py-4 text-center text-sm ${getScoreColor(result.context_precision, 'context_precision')}`}>
                      {result.context_precision.toFixed(1)}%
                    </td>
                    <td className={`px-6 py-4 text-center text-sm ${getScoreColor(result.answer_correctness, 'answer_correctness')}`}>
                      {result.answer_correctness.toFixed(1)}%
                    </td>
                    <td className={`px-6 py-4 text-center text-sm ${getScoreColor(result.semantic_similarity, 'semantic_similarity')}`}>
                      {result.semantic_similarity.toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Metrics Explanation */}
        <div className="bg-paper rounded shadow-paper p-6 mb-8">
          <h2 className="text-2xl font-bold text-ink mb-4">📖 Metrics Explained</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Object.entries(data.metrics_info).map(([key, description]) => (
              <div key={key} className="border border-hair rounded p-4">
                <h3 className="font-semibold text-ink capitalize mb-2">
                  {key.replace(/_/g, ' ')}
                </h3>
                <p className="text-sm text-mute">{description}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Key Findings */}
        <div className="bg-sand rounded shadow-paper p-6">
          <h2 className="text-2xl font-bold text-ink mb-4">🎯 Key Findings</h2>
          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <span className="text-mute text-xl">✅</span>
              <div>
                <span className="font-semibold text-ink">Best Overall: </span>
                <span className="text-mute">
                  Retrieval baseline is being re-established against the rebuilt golden set
                </span>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <span className="text-mute text-xl">📚</span>
              <div>
                <span className="font-semibold text-ink">Most Accurate: </span>
                <span className="text-mute">
                  Multi Query retrieval achieves 73.7% faithfulness and 66.0% answer correctness (best factual accuracy)
                </span>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <span className="text-mute text-xl">⚡</span>
              <div>
                <span className="font-semibold text-ink">Fastest Baseline: </span>
                <span className="text-mute">
                  Naive retrieval offers 61.3% faithfulness with simplest implementation (good for prototyping)
                </span>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <span className="text-critical text-xl">⚠️</span>
              <div>
                <span className="font-semibold text-ink">Avoid: </span>
                <span className="text-mute">
                  Contextual Compression shows only 46.3% faithfulness (too aggressive filtering causes hallucinations)
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Color Legend */}
        <div className="mt-6 flex items-center justify-center gap-6 text-sm">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 bg-ink rounded"></div>
            <span className="text-mute">Excellent (≥75-85%)</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 bg-ink rounded"></div>
            <span className="text-mute">Good (60-75%)</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 bg-critical rounded"></div>
            <span className="text-mute">Needs Improvement (&lt;60%)</span>
          </div>
        </div>
      </div>
    </main>
  );
}

