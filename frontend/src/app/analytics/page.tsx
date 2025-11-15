'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import {
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import {
  DollarSign,
  Users,
  Target,
  ArrowLeft,
  RefreshCw,
  AlertTriangle,
} from 'lucide-react';
import { apiClient, AtRiskCustomer, DashboardStats } from '../api-client';

export default function AnalyticsPage() {
  const [loading, setLoading] = useState(true);
  const [customers, setCustomers] = useState<AtRiskCustomer[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);

  useEffect(() => {
    loadAnalyticsData();
  }, []);

  const loadAnalyticsData = async () => {
    setLoading(true);
    try {
      const [customersData, statsData] = await Promise.all([
        apiClient.getAtRiskCustomers(0, 200), // Get all customers for analytics
        apiClient.getDashboardStats()
      ]);
      setCustomers(customersData.at_risk_customers);
      setStats(statsData);
    } catch (err) {
      console.error('Failed to load analytics data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = () => {
    loadAnalyticsData();
  };

  // Calculate analytics from real data
  const riskDistribution = customers.reduce((acc, customer) => {
    if (customer.risk_score >= 80) acc.critical++;
    else if (customer.risk_score >= 60) acc.high++;
    else if (customer.risk_score >= 40) acc.medium++;
    else acc.low++;
    return acc;
  }, { critical: 0, high: 0, medium: 0, low: 0 });

  const riskDistributionData = [
    { name: 'Critical (80-100%)', value: riskDistribution.critical, color: '#ef4444' },
    { name: 'High (60-79%)', value: riskDistribution.high, color: '#f97316' },
    { name: 'Medium (40-59%)', value: riskDistribution.medium, color: '#eab308' },
    { name: 'Low (0-39%)', value: riskDistribution.low, color: '#22c55e' },
  ];

  const segmentData = customers.reduce((acc, customer) => {
    if (!acc[customer.segment]) {
      acc[customer.segment] = { segment: customer.segment, arr: 0, count: 0 };
    }
    acc[customer.segment].arr += customer.arr;
    acc[customer.segment].count++;
    return acc;
  }, {} as Record<string, { segment: string; arr: number; count: number }>);

  const arrBySegmentData = Object.values(segmentData);

  const riskReasons = customers.reduce((acc, customer) => {
    if (!acc[customer.risk_reason]) {
      acc[customer.risk_reason] = 0;
    }
    acc[customer.risk_reason]++;
    return acc;
  }, {} as Record<string, number>);

  const totalReasons = Object.values(riskReasons).reduce((sum, count) => sum + count, 0);
  const churnReasonsData = Object.entries(riskReasons)
    .map(([reason, count]) => ({
      reason,
      count,
      percentage: totalReasons > 0 ? Math.round((count / totalReasons) * 100) : 0
    }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-white p-4 rounded-xl shadow-lg border-2 border-gray-200">
          <p className="font-bold text-gray-900 mb-2">{label}</p>
          {payload.map((entry: any, index: number) => (
            <p key={index} className="text-sm" style={{ color: entry.color }}>
              {entry.name}: <span className="font-bold">{entry.value}</span>
            </p>
          ))}
        </div>
      );
    }
    return null;
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white/80 backdrop-blur-sm border-b border-gray-200/60 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <Link href="/">
                <button className="p-2.5 hover:bg-gray-100 rounded-lg transition-all duration-200 border border-gray-200 hover:border-gray-300 hover:shadow-sm">
                  <ArrowLeft className="w-5 h-5 text-gray-700" />
                </button>
              </Link>
              <div>
                <h1 className="text-xl font-bold text-gray-900 tracking-tight">Analytics Dashboard</h1>
                <p className="text-sm text-gray-600 font-medium">Real-time churn insights</p>
              </div>
            </div>

            <button
              onClick={handleRefresh}
              disabled={loading}
              className="p-2.5 hover:bg-gray-100 rounded-lg transition-all duration-200 disabled:opacity-50 border border-gray-200 hover:border-gray-300 hover:shadow-sm"
            >
              <RefreshCw className={`w-5 h-5 text-gray-700 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Summary Stats */}
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
            {[1,2,3,4].map(i => (
              <div key={i} className="bg-white rounded-xl p-6 border border-gray-200 shadow-sm animate-pulse">
                <div className="h-12 w-12 bg-gray-200 rounded-xl mb-4"></div>
                <div className="h-8 w-20 bg-gray-200 rounded mb-2"></div>
                <div className="h-4 w-32 bg-gray-200 rounded"></div>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
            <div className="bg-white/60 backdrop-blur-sm rounded-xl p-6 border border-gray-200/50 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <div className="bg-red-50 p-3 rounded-xl">
                  <AlertTriangle className="w-6 h-6 text-red-500" />
                </div>
              </div>
              <p className="text-3xl font-bold text-gray-800 mb-1.5 tracking-tight">{stats?.total_at_risk || 0}</p>
              <p className="text-sm text-gray-600 font-medium">At-Risk Customers</p>
            </div>

            <div className="bg-white/60 backdrop-blur-sm rounded-xl p-6 border border-gray-200/50 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <div className="bg-orange-50 p-3 rounded-xl">
                  <DollarSign className="w-6 h-6 text-orange-500" />
                </div>
              </div>
              <p className="text-3xl font-bold text-gray-800 mb-1.5 tracking-tight">
                ${stats ? Math.round(stats.total_arr_at_risk / 1000) : 0}K
              </p>
              <p className="text-sm text-gray-600 font-medium">ARR at Risk</p>
            </div>

            <div className="bg-white/60 backdrop-blur-sm rounded-xl p-6 border border-gray-200/50 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <div className="bg-blue-50 p-3 rounded-xl">
                  <Users className="w-6 h-6 text-blue-500" />
                </div>
              </div>
              <p className="text-3xl font-bold text-gray-800 mb-1.5 tracking-tight">{stats?.total_active_customers || 0}</p>
              <p className="text-sm text-gray-600 font-medium">Total Customers</p>
            </div>

            <div className="bg-white/60 backdrop-blur-sm rounded-xl p-6 border border-gray-200/50 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <div className="bg-green-50 p-3 rounded-xl">
                  <Target className="w-6 h-6 text-green-600" />
                </div>
              </div>
              <p className="text-3xl font-bold text-gray-800 mb-1.5 tracking-tight">
                {stats ? (stats.prediction_accuracy * 100).toFixed(1) : 0}%
              </p>
              <p className="text-sm text-gray-600 font-medium">Prediction Accuracy</p>
            </div>
          </div>
        )}

        {/* Charts Grid */}
        {!loading && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            {/* Risk Distribution */}
            <div className="bg-white/60 backdrop-blur-sm rounded-xl p-6 border border-gray-200/50 shadow-sm">
              <h3 className="text-lg font-bold text-gray-800 mb-2 tracking-tight">Risk Distribution</h3>
              <p className="text-sm text-gray-600 mb-6 font-medium">Customer count by risk level</p>
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  <Pie
                    data={riskDistributionData}
                    cx="50%"
                    cy="50%"
                    labelLine={false}
                    label={({ name, percent }: { name?: string; percent?: number }) =>
                      `${name?.split(' ')[0] || ''} ${((percent || 0) * 100).toFixed(0)}%`
                    }
                    outerRadius={100}
                    dataKey="value"
                  >
                    {riskDistributionData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip content={<CustomTooltip />} />
                </PieChart>
              </ResponsiveContainer>
              <div className="grid grid-cols-2 gap-3 mt-4">
                {riskDistributionData.map((item) => (
                  <div key={item.name} className="flex items-center gap-2">
                    <div className="w-3 h-3 rounded-full" style={{ backgroundColor: item.color }}></div>
                    <span className="text-xs text-gray-700">{item.name}: {item.value}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* ARR by Segment */}
            <div className="bg-white/60 backdrop-blur-sm rounded-xl p-6 border border-gray-200/50 shadow-sm">
              <h3 className="text-lg font-bold text-gray-800 mb-2 tracking-tight">ARR by Segment</h3>
              <p className="text-sm text-gray-600 mb-6 font-medium">Revenue breakdown by customer segment</p>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={arrBySegmentData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="segment" stroke="#6b7280" />
                  <YAxis stroke="#6b7280" />
                  <Tooltip content={<CustomTooltip />} />
                  <Bar dataKey="arr" fill="#3b82f6" radius={[4, 4, 0, 0]} name="ARR ($)" />
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Top Churn Reasons */}
            <div className="bg-white/60 backdrop-blur-sm rounded-xl p-6 border border-gray-200/50 shadow-sm lg:col-span-2">
              <h3 className="text-lg font-bold text-gray-800 mb-2 tracking-tight">Top Risk Reasons</h3>
              <p className="text-sm text-gray-600 mb-6 font-medium">Most common reasons for customer churn risk</p>
              <div className="space-y-4">
                {churnReasonsData.map((reason) => (
                  <div key={reason.reason}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-gray-700">{reason.reason}</span>
                      <span className="text-sm font-bold text-gray-900">{reason.count} ({reason.percentage}%)</span>
                    </div>
                    <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                      <div
                        className="h-full bg-blue-600 rounded-full transition-all duration-500"
                        style={{ width: `${reason.percentage}%` }}
                      ></div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
