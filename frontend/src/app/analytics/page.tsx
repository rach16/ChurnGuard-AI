'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  Users,
  AlertTriangle,
  Target,
  Calendar,
  ArrowLeft,
  Download,
  Filter,
  RefreshCw,
} from 'lucide-react';

// Mock data for charts
const churnTrendData = [
  { month: 'Jan', churnRate: 4.2, atRisk: 12, churned: 8 },
  { month: 'Feb', churnRate: 3.8, atRisk: 15, churned: 7 },
  { month: 'Mar', churnRate: 5.1, atRisk: 18, churned: 10 },
  { month: 'Apr', churnRate: 4.5, atRisk: 14, churned: 9 },
  { month: 'May', churnRate: 3.9, atRisk: 16, churned: 8 },
  { month: 'Jun', churnRate: 4.8, atRisk: 20, churned: 11 },
  { month: 'Jul', churnRate: 5.5, atRisk: 25, churned: 13 },
  { month: 'Aug', churnRate: 4.2, atRisk: 22, churned: 10 },
];

const riskDistributionData = [
  { name: 'Critical (80-100%)', value: 6, color: '#ef4444' },
  { name: 'High (60-79%)', value: 14, color: '#f97316' },
  { name: 'Medium (40-59%)', value: 23, color: '#eab308' },
  { name: 'Low (0-39%)', value: 107, color: '#22c55e' },
];

const arrBySegmentData = [
  { segment: 'Enterprise', arr: 2450000, count: 12 },
  { segment: 'Mid-Market', arr: 1680000, count: 28 },
  { segment: 'Commercial', arr: 890000, count: 45 },
  { segment: 'SMB', arr: 320000, count: 65 },
];

const churnReasonsData = [
  { reason: 'Pricing & Budget', count: 12, percentage: 28 },
  { reason: 'Product Fit', count: 9, percentage: 21 },
  { reason: 'Poor Onboarding', count: 7, percentage: 16 },
  { reason: 'Low Usage', count: 6, percentage: 14 },
  { reason: 'Competitive', count: 5, percentage: 12 },
  { reason: 'Other', count: 4, percentage: 9 },
];

const competitorData = [
  { name: 'Salesforce', losses: 8 },
  { name: 'HubSpot', losses: 6 },
  { name: 'Mixpanel', losses: 5 },
  { name: 'Gainsight', losses: 4 },
  { name: 'Others', losses: 9 },
];

const monthlyArrData = [
  { month: 'Jan', totalArr: 5200000, atRiskArr: 520000 },
  { month: 'Feb', totalArr: 5350000, atRiskArr: 535000 },
  { month: 'Mar', totalArr: 5480000, atRiskArr: 658000 },
  { month: 'Apr', totalArr: 5620000, atRiskArr: 562000 },
  { month: 'May', totalArr: 5740000, atRiskArr: 631000 },
  { month: 'Jun', totalArr: 5890000, atRiskArr: 706000 },
  { month: 'Jul', totalArr: 6020000, atRiskArr: 843000 },
  { month: 'Aug', totalArr: 6180000, atRiskArr: 772000 },
];

export default function AnalyticsPage() {
  const [timeRange, setTimeRange] = useState('6M');
  const [loading, setLoading] = useState(false);

  const handleRefresh = () => {
    setLoading(true);
    setTimeout(() => setLoading(false), 1000);
  };

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
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50">
      {/* Header */}
      <header className="bg-white/90 backdrop-blur-xl border-b border-gray-200/80 sticky top-0 z-50 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <Link href="/">
                <motion.button
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.95 }}
                  className="p-2 bg-white border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors"
                >
                  <ArrowLeft className="w-5 h-5 text-gray-700" />
                </motion.button>
              </Link>
              <div>
                <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 bg-clip-text text-transparent">
                  Analytics Dashboard
                </h1>
                <p className="text-xs text-gray-500 font-medium">Comprehensive churn insights and trends</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {/* Time Range Selector */}
              <div className="flex bg-white border border-gray-200 rounded-xl p-1 gap-1">
                {['1M', '3M', '6M', '1Y'].map((range) => (
                  <button
                    key={range}
                    onClick={() => setTimeRange(range)}
                    className={`px-3 py-1.5 rounded-lg text-sm font-semibold transition-all ${
                      timeRange === range
                        ? 'bg-blue-600 text-white shadow'
                        : 'text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {range}
                  </button>
                ))}
              </div>

              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                onClick={handleRefresh}
                disabled={loading}
                className="p-2.5 bg-white border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-5 h-5 text-gray-700 ${loading ? 'animate-spin' : ''}`} />
              </motion.button>

              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                className="px-4 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl hover:shadow-lg transition-all font-semibold flex items-center gap-2"
              >
                <Download className="w-4 h-4" />
                Export Report
              </motion.button>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Summary Stats */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            whileHover={{ y: -5 }}
            className="bg-white rounded-2xl shadow-lg p-6 border border-gray-100 relative overflow-hidden"
          >
            <div className="absolute top-0 right-0 w-24 h-24 bg-red-50 rounded-full -mr-12 -mt-12 opacity-50"></div>
            <div className="relative">
              <div className="flex items-center justify-between mb-3">
                <div className="bg-red-100 p-3 rounded-xl">
                  <TrendingDown className="w-6 h-6 text-red-600" />
                </div>
              </div>
              <p className="text-3xl font-bold text-gray-900 mb-1">4.2%</p>
              <p className="text-sm font-medium text-gray-600">Avg Churn Rate</p>
              <p className="text-xs text-red-600 font-semibold mt-2 flex items-center gap-1">
                <TrendingDown className="w-3 h-3" />
                -0.6% from last month
              </p>
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            whileHover={{ y: -5 }}
            className="bg-white rounded-2xl shadow-lg p-6 border border-gray-100 relative overflow-hidden"
          >
            <div className="absolute top-0 right-0 w-24 h-24 bg-orange-50 rounded-full -mr-12 -mt-12 opacity-50"></div>
            <div className="relative">
              <div className="flex items-center justify-between mb-3">
                <div className="bg-orange-100 p-3 rounded-xl">
                  <DollarSign className="w-6 h-6 text-orange-600" />
                </div>
              </div>
              <p className="text-3xl font-bold text-gray-900 mb-1">$772K</p>
              <p className="text-sm font-medium text-gray-600">Current ARR at Risk</p>
              <p className="text-xs text-green-600 font-semibold mt-2 flex items-center gap-1">
                <TrendingUp className="w-3 h-3 rotate-180" />
                -8.4% from peak
              </p>
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            whileHover={{ y: -5 }}
            className="bg-white rounded-2xl shadow-lg p-6 border border-gray-100 relative overflow-hidden"
          >
            <div className="absolute top-0 right-0 w-24 h-24 bg-blue-50 rounded-full -mr-12 -mt-12 opacity-50"></div>
            <div className="relative">
              <div className="flex items-center justify-between mb-3">
                <div className="bg-blue-100 p-3 rounded-xl">
                  <Users className="w-6 h-6 text-blue-600" />
                </div>
              </div>
              <p className="text-3xl font-bold text-gray-900 mb-1">150</p>
              <p className="text-sm font-medium text-gray-600">Total Active Customers</p>
              <p className="text-xs text-green-600 font-semibold mt-2 flex items-center gap-1">
                <TrendingUp className="w-3 h-3" />
                +12 this month
              </p>
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
            whileHover={{ y: -5 }}
            className="bg-white rounded-2xl shadow-lg p-6 border border-gray-100 relative overflow-hidden"
          >
            <div className="absolute top-0 right-0 w-24 h-24 bg-green-50 rounded-full -mr-12 -mt-12 opacity-50"></div>
            <div className="relative">
              <div className="flex items-center justify-between mb-3">
                <div className="bg-green-100 p-3 rounded-xl">
                  <Target className="w-6 h-6 text-green-600" />
                </div>
              </div>
              <p className="text-3xl font-bold text-gray-900 mb-1">87%</p>
              <p className="text-sm font-medium text-gray-600">Retention Rate</p>
              <p className="text-xs text-green-600 font-semibold mt-2 flex items-center gap-1">
                <TrendingUp className="w-3 h-3" />
                +2.3% YoY
              </p>
            </div>
          </motion.div>
        </div>

        {/* Charts Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          {/* Churn Rate Trend */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 }}
            className="bg-white rounded-2xl shadow-xl p-6 border border-gray-100"
          >
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-lg font-bold text-gray-900">Churn Rate Trend</h3>
                <p className="text-sm text-gray-600">Monthly churn percentage over time</p>
              </div>
              <div className="px-3 py-1.5 bg-red-100 text-red-700 rounded-lg text-xs font-bold">
                Critical Metric
              </div>
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={churnTrendData}>
                <defs>
                  <linearGradient id="colorChurn" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="month" stroke="#6b7280" style={{ fontSize: '12px', fontWeight: 600 }} />
                <YAxis stroke="#6b7280" style={{ fontSize: '12px', fontWeight: 600 }} />
                <Tooltip content={<CustomTooltip />} />
                <Area
                  type="monotone"
                  dataKey="churnRate"
                  stroke="#ef4444"
                  strokeWidth={3}
                  fillOpacity={1}
                  fill="url(#colorChurn)"
                  name="Churn Rate (%)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </motion.div>

          {/* Risk Distribution */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5 }}
            className="bg-white rounded-2xl shadow-xl p-6 border border-gray-100"
          >
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-lg font-bold text-gray-900">Risk Distribution</h3>
                <p className="text-sm text-gray-600">Customer count by risk level</p>
              </div>
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={riskDistributionData}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ name, percent }: { name?: string; percent?: number }) => `${name?.split(' ')[0] || ''} ${((percent || 0) * 100).toFixed(0)}%`}
                  outerRadius={100}
                  fill="#8884d8"
                  dataKey="value"
                  style={{ fontSize: '12px', fontWeight: 600 }}
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
                  <span className="text-xs font-medium text-gray-700">{item.name}: {item.value}</span>
                </div>
              ))}
            </div>
          </motion.div>

          {/* ARR by Segment */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.6 }}
            className="bg-white rounded-2xl shadow-xl p-6 border border-gray-100"
          >
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-lg font-bold text-gray-900">ARR by Segment</h3>
                <p className="text-sm text-gray-600">Annual recurring revenue breakdown</p>
              </div>
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={arrBySegmentData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="segment" stroke="#6b7280" style={{ fontSize: '12px', fontWeight: 600 }} />
                <YAxis stroke="#6b7280" style={{ fontSize: '12px', fontWeight: 600 }} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="arr" fill="#3b82f6" radius={[8, 8, 0, 0]} name="ARR ($)" />
              </BarChart>
            </ResponsiveContainer>
          </motion.div>

          {/* Top Churn Reasons */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.7 }}
            className="bg-white rounded-2xl shadow-xl p-6 border border-gray-100"
          >
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-lg font-bold text-gray-900">Top Churn Reasons</h3>
                <p className="text-sm text-gray-600">Why customers are leaving</p>
              </div>
            </div>
            <div className="space-y-4">
              {churnReasonsData.map((reason, index) => (
                <motion.div
                  key={reason.reason}
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.8 + index * 0.05 }}
                  className="relative"
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-semibold text-gray-700">{reason.reason}</span>
                    <span className="text-sm font-bold text-gray-900">{reason.count} ({reason.percentage}%)</span>
                  </div>
                  <div className="w-full bg-gray-100 rounded-full h-3 overflow-hidden">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${reason.percentage}%` }}
                      transition={{ delay: 0.8 + index * 0.05, duration: 0.6 }}
                      className="h-full bg-gradient-to-r from-red-500 to-orange-500 rounded-full"
                    ></motion.div>
                  </div>
                </motion.div>
              ))}
            </div>
          </motion.div>
        </div>

        {/* Full Width Charts */}
        <div className="grid grid-cols-1 gap-6">
          {/* Monthly ARR Trends */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.9 }}
            className="bg-white rounded-2xl shadow-xl p-6 border border-gray-100"
          >
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-lg font-bold text-gray-900">Monthly ARR Trends</h3>
                <p className="text-sm text-gray-600">Total ARR vs ARR at risk over time</p>
              </div>
            </div>
            <ResponsiveContainer width="100%" height={350}>
              <LineChart data={monthlyArrData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="month" stroke="#6b7280" style={{ fontSize: '12px', fontWeight: 600 }} />
                <YAxis stroke="#6b7280" style={{ fontSize: '12px', fontWeight: 600 }} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: '14px', fontWeight: 600 }} />
                <Line
                  type="monotone"
                  dataKey="totalArr"
                  stroke="#10b981"
                  strokeWidth={3}
                  dot={{ fill: '#10b981', r: 5 }}
                  name="Total ARR ($)"
                />
                <Line
                  type="monotone"
                  dataKey="atRiskArr"
                  stroke="#ef4444"
                  strokeWidth={3}
                  strokeDasharray="5 5"
                  dot={{ fill: '#ef4444', r: 5 }}
                  name="ARR at Risk ($)"
                />
              </LineChart>
            </ResponsiveContainer>
          </motion.div>

          {/* Competitor Analysis */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 1.0 }}
            className="bg-white rounded-2xl shadow-xl p-6 border border-gray-100"
          >
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3 className="text-lg font-bold text-gray-900">Competitive Losses</h3>
                <p className="text-sm text-gray-600">Customers lost to competitors</p>
              </div>
              <div className="px-3 py-1.5 bg-purple-100 text-purple-700 rounded-lg text-xs font-bold">
                Strategic Intel
              </div>
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={competitorData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis type="number" stroke="#6b7280" style={{ fontSize: '12px', fontWeight: 600 }} />
                <YAxis type="category" dataKey="name" stroke="#6b7280" style={{ fontSize: '12px', fontWeight: 600 }} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="losses" fill="#8b5cf6" radius={[0, 8, 8, 0]} name="Customers Lost" />
              </BarChart>
            </ResponsiveContainer>
          </motion.div>
        </div>

        {/* Insights Section */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 1.1 }}
          className="mt-6 bg-gradient-to-r from-blue-50 to-indigo-50 rounded-2xl border-2 border-blue-200 p-6"
        >
          <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
            <Target className="w-6 h-6 text-blue-600" />
            Key Insights & Recommendations
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-white p-4 rounded-xl border border-blue-200">
              <p className="text-sm font-bold text-blue-900 mb-2">🎯 Pricing Concerns Lead Churn</p>
              <p className="text-sm text-gray-700">28% of churned customers cited pricing. Consider value-based pricing review and competitive analysis.</p>
            </div>
            <div className="bg-white p-4 rounded-xl border border-blue-200">
              <p className="text-sm font-bold text-blue-900 mb-2">📊 Enterprise Segment Healthy</p>
              <p className="text-sm text-gray-700">Enterprise accounts show strong retention (92%). Focus resources on Commercial and SMB segments.</p>
            </div>
            <div className="bg-white p-4 rounded-xl border border-blue-200">
              <p className="text-sm font-bold text-blue-900 mb-2">⚠️ Onboarding Gap Identified</p>
              <p className="text-sm text-gray-700">16% churn from poor onboarding. Implement automated workflows and improve CSM handoff process.</p>
            </div>
            <div className="bg-white p-4 rounded-xl border border-blue-200">
              <p className="text-sm font-bold text-blue-900 mb-2">🏆 Competitive Threat: Salesforce</p>
              <p className="text-sm text-gray-700">Most losses to Salesforce. Create competitive battlecards and highlight unique differentiators.</p>
            </div>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
