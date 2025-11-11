'use client';

import React, { useState, useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  TrendingDown,
  TrendingUp,
  AlertTriangle,
  Calendar,
  Users,
  MessageSquare,
  Activity,
  Target,
  Clock,
  CheckCircle2,
  XCircle,
  Minus,
  BarChart3,
  PieChart,
} from 'lucide-react';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
} from 'recharts';

interface CustomerAnalysis {
  customer: any;
  analysis: {
    engagement_history: any[];
    support_tickets: any[];
    feature_usage: any[];
    interactions: any[];
    predictions: any;
    recommended_actions: any[];
  };
  health_indicators: {
    engagement: string;
    product_usage: string;
    support_health: string;
    relationship_strength: string;
  };
}

export default function CustomerDetailPage() {
  const params = useParams();
  const router = useRouter();
  const customerId = params?.id;

  const [analysis, setAnalysis] = useState<CustomerAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (customerId) {
      fetchCustomerAnalysis(Number(customerId));
    }
  }, [customerId]);

  const fetchCustomerAnalysis = async (id: number) => {
    try {
      setLoading(true);
      const response = await fetch(`http://localhost:8000/customer/${id}/detailed-analysis`);

      if (!response.ok) {
        throw new Error('Failed to fetch customer analysis');
      }

      const data = await response.json();
      setAnalysis(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-blue-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-16 w-16 border-b-4 border-indigo-600 mx-auto mb-4"></div>
          <p className="text-gray-600 font-medium">Loading customer analysis...</p>
        </div>
      </div>
    );
  }

  if (error || !analysis) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-blue-50 flex items-center justify-center">
        <div className="text-center">
          <AlertTriangle className="w-16 h-16 text-red-500 mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-gray-900 mb-2">Error Loading Customer</h2>
          <p className="text-gray-600 mb-4">{error || 'Customer not found'}</p>
          <button
            onClick={() => router.push('/')}
            className="px-6 py-3 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
          >
            Back to Dashboard
          </button>
        </div>
      </div>
    );
  }

  const { customer, analysis: customerAnalysis, health_indicators } = analysis;

  const getRiskColor = (score: number) => {
    if (score >= 80) return 'text-red-600 bg-red-50 border-red-200';
    if (score >= 60) return 'text-orange-600 bg-orange-50 border-orange-200';
    if (score >= 40) return 'text-yellow-600 bg-yellow-50 border-yellow-200';
    return 'text-green-600 bg-green-50 border-green-200';
  };

  const getHealthColor = (health: string) => {
    if (health === 'Poor' || health === 'At Risk' || health === 'Weak' || health === 'Low') {
      return 'text-red-600 bg-red-50';
    }
    if (health === 'Fair' || health === 'Moderate' || health === 'Normal' || health === 'Medium') {
      return 'text-yellow-600 bg-yellow-50';
    }
    return 'text-green-600 bg-green-50';
  };

  const getPriorityColor = (priority: string) => {
    if (priority === 'Critical') return 'bg-red-600 text-white';
    if (priority === 'High') return 'bg-orange-600 text-white';
    return 'bg-yellow-600 text-white';
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-blue-50 pb-12">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 sticky top-0 z-10 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <button
            onClick={() => router.push('/')}
            className="flex items-center gap-2 text-gray-600 hover:text-gray-900 mb-4 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
            <span className="font-medium">Back to Dashboard</span>
          </button>

          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold text-gray-900">{customer.name}</h1>
              <div className="flex items-center gap-4 mt-2">
                <span className="px-3 py-1 bg-indigo-100 text-indigo-700 rounded-full text-sm font-medium">
                  {customer.segment}
                </span>
                <span className="text-gray-600">ARR: ${customer.arr.toLocaleString()}</span>
                <span className="text-gray-600">Tenure: {customer.tenure_years} years</span>
              </div>
            </div>

            <div className={`px-6 py-4 rounded-xl border-2 ${getRiskColor(customer.risk_score)}`}>
              <div className="text-center">
                <p className="text-sm font-medium opacity-75 mb-1">Risk Score</p>
                <p className="text-4xl font-bold">{customer.risk_score}%</p>
                <p className="text-sm mt-1">{customer.risk_reason}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Health Indicators */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
          {Object.entries(health_indicators).map(([key, value]) => (
            <motion.div
              key={key}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className={`p-4 rounded-xl ${getHealthColor(value)} border-2`}
            >
              <p className="text-sm font-medium opacity-75 mb-1 capitalize">
                {key.replace('_', ' ')}
              </p>
              <p className="text-2xl font-bold">{value}</p>
            </motion.div>
          ))}
        </div>

        {/* Engagement Timeline */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mb-6"
        >
          <div className="flex items-center gap-3 mb-6">
            <Activity className="w-6 h-6 text-indigo-600" />
            <h2 className="text-xl font-bold text-gray-900">Engagement Timeline</h2>
          </div>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={customerAnalysis.engagement_history}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 12 }} />
              <YAxis domain={[0, 1]} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Legend />
              <Line
                type="monotone"
                dataKey="engagement_score"
                stroke="#6366f1"
                strokeWidth={3}
                dot={{ fill: '#6366f1', r: 4 }}
                name="Engagement Score"
              />
            </LineChart>
          </ResponsiveContainer>
        </motion.div>

        {/* Two Column Layout */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          {/* Feature Usage */}
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            className="bg-white rounded-xl shadow-sm border border-gray-200 p-6"
          >
            <div className="flex items-center gap-3 mb-6">
              <BarChart3 className="w-6 h-6 text-indigo-600" />
              <h2 className="text-xl font-bold text-gray-900">Feature Usage</h2>
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={customerAnalysis.feature_usage} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" domain={[0, 1]} tick={{ fontSize: 12 }} />
                <YAxis type="category" dataKey="feature" tick={{ fontSize: 12 }} width={100} />
                <Tooltip />
                <Bar dataKey="usage_rate" fill="#6366f1" name="Usage Rate" />
              </BarChart>
            </ResponsiveContainer>
          </motion.div>

          {/* Contributing Factors */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            className="bg-white rounded-xl shadow-sm border border-gray-200 p-6"
          >
            <div className="flex items-center gap-3 mb-6">
              <PieChart className="w-6 h-6 text-indigo-600" />
              <h2 className="text-xl font-bold text-gray-900">Risk Contributing Factors</h2>
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <RadarChart data={customerAnalysis.predictions.contributing_factors}>
                <PolarGrid />
                <PolarAngleAxis dataKey="factor" tick={{ fontSize: 12 }} />
                <PolarRadiusAxis domain={[0, 1]} tick={{ fontSize: 12 }} />
                <Radar
                  name="Weight"
                  dataKey="weight"
                  stroke="#6366f1"
                  fill="#6366f1"
                  fillOpacity={0.6}
                />
              </RadarChart>
            </ResponsiveContainer>
            <div className="mt-4 space-y-2">
              {customerAnalysis.predictions.contributing_factors.map((factor: any, idx: number) => (
                <div key={idx} className="flex items-center justify-between text-sm">
                  <span className="text-gray-700">{factor.factor}</span>
                  <span className={`px-2 py-1 rounded ${
                    factor.impact === 'High' ? 'bg-red-100 text-red-700' :
                    factor.impact === 'Medium' ? 'bg-yellow-100 text-yellow-700' :
                    'bg-green-100 text-green-700'
                  } font-medium`}>
                    {factor.impact}
                  </span>
                </div>
              ))}
            </div>
          </motion.div>
        </div>

        {/* Predictions */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-gradient-to-r from-orange-50 to-red-50 rounded-xl border border-orange-200 p-6 mb-6"
        >
          <div className="flex items-center gap-3 mb-4">
            <Target className="w-6 h-6 text-orange-600" />
            <h2 className="text-xl font-bold text-gray-900">Churn Prediction</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div>
              <p className="text-sm text-gray-600 mb-2">Churn Probability</p>
              <p className="text-4xl font-bold text-orange-600">
                {(customerAnalysis.predictions.churn_probability * 100).toFixed(0)}%
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-600 mb-2">Predicted Churn Date</p>
              <p className="text-2xl font-bold text-gray-900">
                {customerAnalysis.predictions.days_until_churn} days
              </p>
              <p className="text-sm text-gray-600 mt-1">
                ({customerAnalysis.predictions.confidence_interval.lower}-{customerAnalysis.predictions.confidence_interval.upper} days range)
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-600 mb-2">Urgency Level</p>
              <p className={`text-2xl font-bold ${
                customer.risk_score >= 80 ? 'text-red-600' :
                customer.risk_score >= 60 ? 'text-orange-600' :
                'text-yellow-600'
              }`}>
                {customer.risk_score >= 80 ? 'Critical' : customer.risk_score >= 60 ? 'High' : 'Medium'}
              </p>
            </div>
          </div>
        </motion.div>

        {/* Recommended Actions */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mb-6"
        >
          <div className="flex items-center gap-3 mb-6">
            <CheckCircle2 className="w-6 h-6 text-indigo-600" />
            <h2 className="text-xl font-bold text-gray-900">Recommended Actions</h2>
          </div>
          <div className="space-y-4">
            {customerAnalysis.recommended_actions.map((action: any, idx: number) => (
              <div
                key={idx}
                className="flex items-start gap-4 p-4 bg-gray-50 rounded-lg border border-gray-200 hover:border-indigo-300 transition-colors"
              >
                <span className={`px-3 py-1 rounded-full text-xs font-bold ${getPriorityColor(action.priority)}`}>
                  {action.priority}
                </span>
                <div className="flex-1">
                  <p className="font-semibold text-gray-900 mb-1">{action.action}</p>
                  <div className="flex items-center gap-4 text-sm text-gray-600">
                    <span className="flex items-center gap-1">
                      <Clock className="w-4 h-4" />
                      Deadline: {action.deadline}
                    </span>
                    <span className="flex items-center gap-1">
                      <Users className="w-4 h-4" />
                      {action.owner}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </motion.div>

        {/* Two Column: Support Tickets & Interactions */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Support Tickets */}
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            className="bg-white rounded-xl shadow-sm border border-gray-200 p-6"
          >
            <div className="flex items-center gap-3 mb-6">
              <AlertTriangle className="w-6 h-6 text-indigo-600" />
              <h2 className="text-xl font-bold text-gray-900">Recent Support Tickets</h2>
            </div>
            <div className="space-y-3 max-h-96 overflow-y-auto">
              {customerAnalysis.support_tickets.map((ticket: any, idx: number) => (
                <div key={idx} className="p-3 bg-gray-50 rounded-lg border border-gray-200">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-semibold text-gray-900">{ticket.type}</span>
                    <span className={`px-2 py-1 rounded text-xs font-medium ${
                      ticket.status === 'Resolved' ? 'bg-green-100 text-green-700' :
                      ticket.status === 'Open' ? 'bg-red-100 text-red-700' :
                      'bg-yellow-100 text-yellow-700'
                    }`}>
                      {ticket.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-4 text-xs text-gray-600">
                    <span>{ticket.date}</span>
                    <span className={`px-2 py-0.5 rounded ${
                      ticket.priority === 'High' ? 'bg-red-100 text-red-700' : 'bg-gray-200 text-gray-700'
                    }`}>
                      {ticket.priority}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </motion.div>

          {/* Recent Interactions */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            className="bg-white rounded-xl shadow-sm border border-gray-200 p-6"
          >
            <div className="flex items-center gap-3 mb-6">
              <MessageSquare className="w-6 h-6 text-indigo-600" />
              <h2 className="text-xl font-bold text-gray-900">Recent Interactions</h2>
            </div>
            <div className="space-y-3 max-h-96 overflow-y-auto">
              {customerAnalysis.interactions.map((interaction: any, idx: number) => (
                <div key={idx} className="p-3 bg-gray-50 rounded-lg border border-gray-200">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-semibold text-gray-900">{interaction.type}</span>
                    <span className={`px-2 py-1 rounded text-xs font-medium ${
                      interaction.sentiment === 'Positive' ? 'bg-green-100 text-green-700' :
                      interaction.sentiment === 'Negative' ? 'bg-red-100 text-red-700' :
                      'bg-gray-200 text-gray-700'
                    }`}>
                      {interaction.sentiment}
                    </span>
                  </div>
                  <p className="text-xs text-gray-600 mb-1">{interaction.notes}</p>
                  <span className="text-xs text-gray-500">{interaction.date}</span>
                </div>
              ))}
            </div>
          </motion.div>
        </div>
      </div>
    </div>
  );
}
