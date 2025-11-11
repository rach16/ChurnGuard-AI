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
      if (score >= 85) return 'text-green-600 font-semibold';
      if (score >= 70) return 'text-yellow-600';
      return 'text-red-600';
    }
    // More lenient for other metrics
    if (score >= 75) return 'text-green-600 font-semibold';
    if (score >= 60) return 'text-yellow-600';
    return 'text-red-600';
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
      <main className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 p-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center py-20">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
            <p className="mt-4 text-gray-600">Loading evaluation results...</p>
          </div>
        </div>
      </main>
    );
  }

  if (error || !data) {
    return (
      <main className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 p-8">
        <div className="max-w-7xl mx-auto">
          <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-center">
            <p className="text-red-600 font-semibold">Error loading evaluation results</p>
            <p className="text-red-500 mt-2">{error || 'Unknown error'}</p>
            <Link href="/" className="mt-4 inline-block text-indigo-600 hover:underline">
              ← Back to Home
            </Link>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <Link href="/" className="text-indigo-600 hover:underline mb-4 inline-block">
            ← Back to Chat
          </Link>
          <h1 className="text-4xl font-bold text-gray-800 mb-2">
            📊 RAGAS Evaluation Results
          </h1>
          <p className="text-gray-600">
            {data.note}
          </p>
        </div>

        {/* Performance Comparison Table */}
        <div className="bg-white rounded-lg shadow-xl overflow-hidden mb-8">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gradient-to-r from-indigo-600 to-purple-600">
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
              <tbody className="bg-white divide-y divide-gray-200">
                {data.results.map((result, idx) => (
                  <tr key={result.method} className={idx % 2 === 0 ? 'bg-gray-50' : 'bg-white'}>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex flex-col">
                        <span className="text-sm font-semibold text-gray-900">
                          {result.method}
                        </span>
                        <span className="text-xs text-gray-500">
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
        <div className="bg-white rounded-lg shadow-lg p-6 mb-8">
          <h2 className="text-2xl font-bold text-gray-800 mb-4">📖 Metrics Explained</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Object.entries(data.metrics_info).map(([key, description]) => (
              <div key={key} className="border border-gray-200 rounded-lg p-4">
                <h3 className="font-semibold text-gray-800 capitalize mb-2">
                  {key.replace(/_/g, ' ')}
                </h3>
                <p className="text-sm text-gray-600">{description}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Key Findings */}
        <div className="bg-gradient-to-r from-green-50 to-blue-50 rounded-lg shadow-lg p-6">
          <h2 className="text-2xl font-bold text-gray-800 mb-4">🎯 Key Findings</h2>
          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <span className="text-green-600 text-xl">✅</span>
              <div>
                <span className="font-semibold text-gray-800">Best Overall: </span>
                <span className="text-gray-700">
                  Parent Document retrieval achieves 94.7% context recall and 90.0% precision (most complete context coverage)
                </span>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <span className="text-blue-600 text-xl">📚</span>
              <div>
                <span className="font-semibold text-gray-800">Most Accurate: </span>
                <span className="text-gray-700">
                  Multi Query retrieval achieves 73.7% faithfulness and 66.0% answer correctness (best factual accuracy)
                </span>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <span className="text-yellow-600 text-xl">⚡</span>
              <div>
                <span className="font-semibold text-gray-800">Fastest Baseline: </span>
                <span className="text-gray-700">
                  Naive retrieval offers 61.3% faithfulness with simplest implementation (good for prototyping)
                </span>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <span className="text-red-600 text-xl">⚠️</span>
              <div>
                <span className="font-semibold text-gray-800">Avoid: </span>
                <span className="text-gray-700">
                  Contextual Compression shows only 46.3% faithfulness (too aggressive filtering causes hallucinations)
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Color Legend */}
        <div className="mt-6 flex items-center justify-center gap-6 text-sm">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 bg-green-600 rounded"></div>
            <span className="text-gray-600">Excellent (≥75-85%)</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 bg-yellow-600 rounded"></div>
            <span className="text-gray-600">Good (60-75%)</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 bg-red-600 rounded"></div>
            <span className="text-gray-600">Needs Improvement (&lt;60%)</span>
          </div>
        </div>
      </div>
    </main>
  );
}

