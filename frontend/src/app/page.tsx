'use client';

import { useState, useEffect, useRef, FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import Chatbot from './components/Chatbot';
import {
  AlertTriangle,
  TrendingDown,
  TrendingUp,
  Users,
  Shield,
  Sparkles,
  BarChart3,
  Zap,
  Target,
  Heart,
  CheckCircle2,
  Clock,
  DollarSign,
  Download,
  RefreshCw,
  ArrowRight,
  Activity,
  ChevronRight,
  Bell
} from 'lucide-react';
import { apiClient, formatAPIError, AskResponse, MultiAgentResponse, AtRiskCustomer, DashboardStats } from './api-client';

type ResponseType = AskResponse | MultiAgentResponse | null;

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  response?: ResponseType;
}

export default function Home() {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [retrieverType, setRetrieverType] = useState('parent_document');
  const [useAgent, setUseAgent] = useState(false);
  const [useMultiAgent, setUseMultiAgent] = useState(true);
  const [response, setResponse] = useState<ResponseType>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backendStatus, setBackendStatus] = useState<'checking' | 'online' | 'offline'>('checking');
  const [activeTab, setActiveTab] = useState<'dashboard' | 'analyze'>('dashboard');

  // Chatbot state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isTyping, setIsTyping] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Dashboard data
  const [atRiskCustomers, setAtRiskCustomers] = useState<AtRiskCustomer[]>([]);
  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Toast notification state
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Scroll to bottom of messages
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  // Show toast notification
  const showToast = (message: string) => {
    setToastMessage(message);
    setTimeout(() => setToastMessage(null), 4000); // Show for 4 seconds
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  // Check backend health and load dashboard data on mount
  useEffect(() => {
    const checkBackend = async () => {
      try {
        await apiClient.health();
        setBackendStatus('online');
      } catch {
        setBackendStatus('offline');
      }
    };
    checkBackend();
  }, []);

  // Load dashboard data when dashboard tab is active
  useEffect(() => {
    if (activeTab === 'dashboard' && backendStatus === 'online') {
      loadDashboardData();
    }
  }, [activeTab, backendStatus]);

  const loadDashboardData = async () => {
    setDashboardLoading(true);
    try {
      const [customersData, statsData] = await Promise.all([
        apiClient.getAtRiskCustomers(60, 10),
        apiClient.getDashboardStats()
      ]);

      setAtRiskCustomers(customersData.at_risk_customers);
      setDashboardStats(statsData);
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
    } finally {
      setDashboardLoading(false);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    await loadDashboardData();
    setTimeout(() => setRefreshing(false), 500);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim() || loading) return;

    // Add user message to chat
    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: query,
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMessage]);
    setQuery('');
    setLoading(true);
    setIsTyping(true);
    setError(null);

    try {
      let data: ResponseType;
      if (useMultiAgent) {
        data = await apiClient.multiAgentAnalyze(query, true, true);
      } else if (useAgent) {
        data = await apiClient.analyzeChurn(query, undefined, true);
      } else {
        data = await apiClient.ask(query, retrieverType);
      }

      // Add assistant response to chat
      const assistantMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: isMultiAgentResponse(data) ? data.response : ('answer' in data ? data.answer : ''),
        timestamp: new Date(),
        response: data,
      };
      setMessages(prev => [...prev, assistantMessage]);
      setBackendStatus('online');
    } catch (err) {
      console.error('Error:', err);
      const errorMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `Sorry, I encountered an error: ${formatAPIError(err)}`,
        timestamp: new Date(),
      };
      setMessages(prev => [...prev, errorMessage]);
      setBackendStatus('offline');
    } finally {
      setLoading(false);
      setIsTyping(false);
    }
  };

  const handleSuggestedPrompt = (prompt: string) => {
    setQuery(prompt);
  };

  const clearChat = () => {
    setMessages([]);
    setError(null);
  };

  const isMultiAgentResponse = (resp: ResponseType): resp is MultiAgentResponse => {
    return resp !== null && 'confidence_score' in resp && 'processing_stages' in resp;
  };

  const getRiskColor = (score: number) => {
    if (score >= 80) return 'text-red-600 bg-red-50 border-red-200';
    if (score >= 60) return 'text-orange-600 bg-orange-50 border-orange-200';
    return 'text-yellow-600 bg-yellow-50 border-yellow-200';
  };

  const getRiskBadgeColor = (score: number) => {
    if (score >= 80) return 'bg-red-500';
    if (score >= 60) return 'bg-orange-500';
    return 'bg-yellow-500';
  };

  // Handle AI recommendations for a customer
  const handleViewRecommendations = async (customer: AtRiskCustomer) => {
    const analysisQuery = `Analyze customer churn risk for ${customer.name} (${customer.segment} segment, $${customer.arr.toLocaleString()} ARR). They have a ${customer.risk_score}% risk score with primary concern: ${customer.risk_reason}. Provide specific retention strategies and recommendations.`;

    setActiveTab('analyze');
    setUseMultiAgent(true);
    setQuery(analysisQuery);

    // Show toast to indicate we're preparing the analysis
    showToast(`🤖 Preparing AI analysis for ${customer.name}...`);

    // Auto-submit the query after a brief delay
    setTimeout(() => {
      const event = new Event('submit', { bubbles: true, cancelable: true });
      handleSubmit(event as any);
    }, 500);
  };

  // Handle export
  const handleExport = () => {
    const csv = [
      ['Name', 'Segment', 'ARR', 'Risk Score', 'Days Until Churn', 'Risk Reason', 'Trend'].join(','),
      ...atRiskCustomers.map(c =>
        [c.name, c.segment, c.arr, c.risk_score, c.days_until_churn, c.risk_reason, c.trend].join(',')
      )
    ].join('\n');

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `at-risk-customers-${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
  };

  // Skeleton loader component
  const SkeletonCard = () => (
    <div className="animate-pulse p-6 bg-white rounded-xl border border-gray-200">
      <div className="flex items-center justify-between mb-4">
        <div className="w-12 h-12 bg-gray-200 rounded-lg"></div>
        <div className="w-16 h-6 bg-gray-200 rounded-full"></div>
      </div>
      <div className="space-y-3">
        <div className="h-8 bg-gray-200 rounded w-24"></div>
        <div className="h-4 bg-gray-200 rounded w-32"></div>
        <div className="h-3 bg-gray-200 rounded w-20"></div>
      </div>
    </div>
  );

  const SkeletonCustomer = () => (
    <div className="animate-pulse p-6 border-b border-gray-100">
      <div className="flex items-start justify-between">
        <div className="flex-1 space-y-4">
          <div className="flex items-center gap-3">
            <div className="h-6 w-40 bg-gray-200 rounded"></div>
            <div className="h-6 w-20 bg-gray-200 rounded-full"></div>
            <div className="h-6 w-24 bg-gray-200 rounded"></div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div className="h-12 bg-gray-200 rounded"></div>
            <div className="h-12 bg-gray-200 rounded"></div>
            <div className="h-12 bg-gray-200 rounded"></div>
          </div>
          <div className="flex gap-2">
            <div className="h-10 w-48 bg-gray-200 rounded-lg"></div>
            <div className="h-10 w-32 bg-gray-200 rounded-lg"></div>
            <div className="h-10 w-32 bg-gray-200 rounded-lg"></div>
          </div>
        </div>
        <div className="w-20 h-20 bg-gray-200 rounded-full"></div>
      </div>
    </div>
  );

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50">
      {/* Header */}
      <header className="bg-white/90 backdrop-blur-xl border-b border-gray-200/80 sticky top-0 z-50 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <motion.div
                whileHover={{ scale: 1.05, rotate: 5 }}
                className="bg-gradient-to-br from-blue-600 to-indigo-600 p-2.5 rounded-xl shadow-lg"
              >
                <Shield className="w-7 h-7 text-white" />
              </motion.div>
              <div>
                <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 bg-clip-text text-transparent">
                  ChurnGuard AI
                </h1>
                <p className="text-xs text-gray-500 font-medium">Proactive Customer Success Intelligence</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Link
                  href="/analytics"
                  className="text-sm px-4 py-2.5 bg-gradient-to-r from-blue-600 to-cyan-600 text-white rounded-xl hover:from-blue-700 hover:to-cyan-700 transition-all shadow-md hover:shadow-lg font-medium flex items-center gap-2"
                >
                  <BarChart3 className="w-4 h-4" />
                  Analytics
                </Link>
              </motion.div>

              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Link
                  href="/integrations"
                  className="text-sm px-4 py-2.5 bg-gradient-to-r from-indigo-600 to-purple-600 text-white rounded-xl hover:from-indigo-700 hover:to-purple-700 transition-all shadow-md hover:shadow-lg font-medium flex items-center gap-2"
                >
                  <Zap className="w-4 h-4" />
                  Integrations
                </Link>
              </motion.div>

              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                <Link
                  href="/evaluations"
                  className="text-sm px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl hover:bg-gray-50 transition-all shadow-sm hover:shadow font-medium flex items-center gap-2"
                >
                  <Target className="w-4 h-4" />
                  Metrics
                </Link>
              </motion.div>

              <div className="flex items-center gap-2 px-3 py-2 bg-white border border-gray-200 rounded-xl shadow-sm">
                <motion.div
                  animate={{ scale: backendStatus === 'online' ? [1, 1.2, 1] : 1 }}
                  transition={{ repeat: backendStatus === 'online' ? Infinity : 0, duration: 2 }}
                  className={`w-2.5 h-2.5 rounded-full ${
                    backendStatus === 'online' ? 'bg-green-500' :
                    backendStatus === 'offline' ? 'bg-red-500' :
                    'bg-yellow-500'
                  }`}
                />
                <span className="text-xs text-gray-700 font-semibold">
                  {backendStatus === 'online' ? 'Live' :
                   backendStatus === 'offline' ? 'Offline' :
                   'Checking...'}
                </span>
              </div>
            </div>
          </div>

          {/* Navigation Tabs */}
          <div className="flex gap-2 mt-4">
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
              onClick={() => setActiveTab('dashboard')}
              className={`px-5 py-2.5 rounded-xl font-semibold text-sm transition-all ${
                activeTab === 'dashboard'
                  ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-lg shadow-blue-200'
                  : 'bg-white text-gray-600 hover:bg-gray-50 border border-gray-200'
              }`}
            >
              <div className="flex items-center gap-2">
                <BarChart3 className="w-4 h-4" />
                Dashboard
              </div>
            </motion.button>
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
              onClick={() => setActiveTab('analyze')}
              className={`px-5 py-2.5 rounded-xl font-semibold text-sm transition-all ${
                activeTab === 'analyze'
                  ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-lg shadow-blue-200'
                  : 'bg-white text-gray-600 hover:bg-gray-50 border border-gray-200'
              }`}
            >
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4" />
                AI Analysis
              </div>
            </motion.button>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <AnimatePresence mode="wait">
          {activeTab === 'dashboard' ? (
            <motion.div
              key="dashboard"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
            >
              {/* Alert Banner */}
              {dashboardStats && dashboardStats.critical_risk_count > 0 && (
                <motion.div
                  initial={{ opacity: 0, y: -20 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="mb-6 bg-gradient-to-r from-red-500 to-orange-500 text-white rounded-2xl p-4 shadow-lg flex items-center justify-between"
                >
                  <div className="flex items-center gap-3">
                    <Bell className="w-6 h-6 animate-bounce" />
                    <div>
                      <p className="font-bold">Critical Alert: {dashboardStats.critical_risk_count} customers need immediate attention</p>
                      <p className="text-sm opacity-90">High churn risk detected - Review and take action now</p>
                    </div>
                  </div>
                  <button className="px-4 py-2 bg-white text-red-600 rounded-lg font-semibold hover:bg-red-50 transition-colors">
                    View All
                  </button>
                </motion.div>
              )}

              {/* Hero Stats */}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                {dashboardLoading ? (
                  <>
                    <SkeletonCard />
                    <SkeletonCard />
                    <SkeletonCard />
                    <SkeletonCard />
                  </>
                ) : (
                  <>
                    <motion.div
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      whileHover={{ y: -5, boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)" }}
                      transition={{ delay: 0.1 }}
                      className="group bg-white rounded-2xl shadow-lg hover:shadow-2xl p-6 border border-gray-100 transition-all cursor-pointer relative overflow-hidden"
                    >
                      <div className="absolute top-0 right-0 w-32 h-32 bg-red-50 rounded-full -mr-16 -mt-16 opacity-50"></div>
                      <div className="relative">
                        <div className="flex items-center justify-between mb-3">
                          <div className="bg-red-100 p-3 rounded-xl group-hover:scale-110 transition-transform">
                            <AlertTriangle className="w-6 h-6 text-red-600" />
                          </div>
                          <span className="text-xs font-bold text-red-600 bg-red-100 px-3 py-1.5 rounded-full">
                            URGENT
                          </span>
                        </div>
                        <p className="text-3xl font-bold text-gray-900 mb-1">{dashboardStats?.total_at_risk || 0}</p>
                        <p className="text-sm font-medium text-gray-600">At-Risk Customers</p>
                        <div className="mt-3 pt-3 border-t border-gray-100">
                          <p className="text-xs text-red-600 font-semibold flex items-center gap-1">
                            <Activity className="w-3 h-3" />
                            {dashboardStats?.critical_risk_count || 0} critical cases
                          </p>
                        </div>
                      </div>
                    </motion.div>

                    <motion.div
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      whileHover={{ y: -5, boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)" }}
                      transition={{ delay: 0.2 }}
                      className="group bg-white rounded-2xl shadow-lg hover:shadow-2xl p-6 border border-gray-100 transition-all cursor-pointer relative overflow-hidden"
                    >
                      <div className="absolute top-0 right-0 w-32 h-32 bg-orange-50 rounded-full -mr-16 -mt-16 opacity-50"></div>
                      <div className="relative">
                        <div className="flex items-center justify-between mb-3">
                          <div className="bg-orange-100 p-3 rounded-xl group-hover:scale-110 transition-transform">
                            <DollarSign className="w-6 h-6 text-orange-600" />
                          </div>
                          <span className="text-xs font-bold text-orange-600 bg-orange-100 px-3 py-1.5 rounded-full">
                            AT RISK
                          </span>
                        </div>
                        <p className="text-3xl font-bold text-gray-900 mb-1">
                          ${dashboardStats ? (dashboardStats.total_arr_at_risk / 1000).toFixed(0) : 0}K
                        </p>
                        <p className="text-sm font-medium text-gray-600">ARR at Risk</p>
                        <div className="mt-3 pt-3 border-t border-gray-100">
                          <p className="text-xs text-gray-500 font-medium flex items-center gap-1">
                            <Users className="w-3 h-3" />
                            Across {dashboardStats?.total_at_risk || 0} accounts
                          </p>
                        </div>
                      </div>
                    </motion.div>

                    <motion.div
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      whileHover={{ y: -5, boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)" }}
                      transition={{ delay: 0.3 }}
                      className="group bg-white rounded-2xl shadow-lg hover:shadow-2xl p-6 border border-gray-100 transition-all cursor-pointer relative overflow-hidden"
                    >
                      <div className="absolute top-0 right-0 w-32 h-32 bg-green-50 rounded-full -mr-16 -mt-16 opacity-50"></div>
                      <div className="relative">
                        <div className="flex items-center justify-between mb-3">
                          <div className="bg-green-100 p-3 rounded-xl group-hover:scale-110 transition-transform">
                            <Target className="w-6 h-6 text-green-600" />
                          </div>
                          <span className="text-xs font-bold text-green-600 bg-green-100 px-3 py-1.5 rounded-full">
                            ACCURATE
                          </span>
                        </div>
                        <p className="text-3xl font-bold text-gray-900 mb-1">
                          {dashboardStats ? (dashboardStats.prediction_accuracy * 100).toFixed(1) : 0}%
                        </p>
                        <p className="text-sm font-medium text-gray-600">Prediction Accuracy</p>
                        <div className="mt-3 pt-3 border-t border-gray-100">
                          <p className="text-xs text-green-600 font-semibold flex items-center gap-1">
                            <CheckCircle2 className="w-3 h-3" />
                            Industry leading
                          </p>
                        </div>
                      </div>
                    </motion.div>

                    <motion.div
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      whileHover={{ y: -5, boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)" }}
                      transition={{ delay: 0.4 }}
                      className="group bg-white rounded-2xl shadow-lg hover:shadow-2xl p-6 border border-gray-100 transition-all cursor-pointer relative overflow-hidden"
                    >
                      <div className="absolute top-0 right-0 w-32 h-32 bg-blue-50 rounded-full -mr-16 -mt-16 opacity-50"></div>
                      <div className="relative">
                        <div className="flex items-center justify-between mb-3">
                          <div className="bg-blue-100 p-3 rounded-xl group-hover:scale-110 transition-transform">
                            <Clock className="w-6 h-6 text-blue-600" />
                          </div>
                          <span className="text-xs font-bold text-blue-600 bg-blue-100 px-3 py-1.5 rounded-full">
                            AVG
                          </span>
                        </div>
                        <p className="text-3xl font-bold text-gray-900 mb-1">
                          {dashboardStats ? Math.round(dashboardStats.avg_days_to_churn) : 0}d
                        </p>
                        <p className="text-sm font-medium text-gray-600">Early Warning Time</p>
                        <div className="mt-3 pt-3 border-t border-gray-100">
                          <p className="text-xs text-blue-600 font-semibold flex items-center gap-1">
                            <TrendingUp className="w-3 h-3" />
                            7-30 day window
                          </p>
                        </div>
                      </div>
                    </motion.div>
                  </>
                )}
              </div>

              {/* At-Risk Customers Queue */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.5 }}
                className="bg-white rounded-2xl shadow-xl border border-gray-100 overflow-hidden"
              >
                <div className="bg-gradient-to-r from-red-50 via-orange-50 to-amber-50 px-6 py-5 border-b border-gray-200">
                  <div className="flex items-center justify-between">
                    <div>
                      <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2 mb-1">
                        <Target className="w-6 h-6 text-red-600" />
                        Priority Queue - Immediate Action Required
                      </h2>
                      <p className="text-sm text-gray-600 font-medium">
                        AI-predicted churn risks ranked by urgency and revenue impact
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <motion.button
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        onClick={handleRefresh}
                        disabled={refreshing}
                        className="p-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl hover:bg-gray-50 transition-colors disabled:opacity-50"
                      >
                        <RefreshCw className={`w-5 h-5 ${refreshing ? 'animate-spin' : ''}`} />
                      </motion.button>
                      <motion.button
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        onClick={handleExport}
                        className="px-5 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl hover:shadow-lg transition-all font-semibold flex items-center gap-2"
                      >
                        <Download className="w-4 h-4" />
                        Export CSV
                      </motion.button>
                    </div>
                  </div>
                </div>

                <div className="divide-y divide-gray-100">
                  {dashboardLoading ? (
                    <>
                      <SkeletonCustomer />
                      <SkeletonCustomer />
                      <SkeletonCustomer />
                    </>
                  ) : atRiskCustomers.length === 0 ? (
                    <div className="p-16 text-center">
                      <div className="bg-green-100 w-20 h-20 rounded-full flex items-center justify-center mx-auto mb-4">
                        <CheckCircle2 className="w-10 h-10 text-green-600" />
                      </div>
                      <p className="text-lg font-bold text-gray-900 mb-2">All Clear!</p>
                      <p className="text-gray-600">No at-risk customers detected at this time</p>
                    </div>
                  ) : (
                    atRiskCustomers.map((customer, idx) => (
                    <motion.div
                      key={customer.id}
                      initial={{ opacity: 0, x: -20 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: 0.6 + idx * 0.05 }}
                      whileHover={{ backgroundColor: '#f9fafb' }}
                      className="p-6 transition-colors cursor-pointer group"
                    >
                      <div className="flex items-start justify-between gap-6">
                        <div className="flex-1">
                          <div className="flex items-center gap-3 mb-3">
                            <h3 className="text-lg font-bold text-gray-900 group-hover:text-blue-600 transition-colors">
                              {customer.name}
                            </h3>
                            <motion.span
                              whileHover={{ scale: 1.05 }}
                              className={`px-3 py-1.5 rounded-full text-xs font-bold ${getRiskColor(customer.risk_score)} border`}
                            >
                              {customer.risk_score}% Risk
                            </motion.span>
                            <span className="px-3 py-1 bg-gradient-to-r from-gray-100 to-gray-200 text-gray-700 rounded-lg text-xs font-semibold">
                              {customer.segment}
                            </span>
                          </div>

                          <div className="grid grid-cols-3 gap-4 mb-4">
                            <div className="bg-gradient-to-br from-blue-50 to-indigo-50 p-3 rounded-xl border border-blue-100">
                              <p className="text-xs text-gray-600 mb-1 font-medium">Annual Revenue</p>
                              <p className="text-lg font-bold text-gray-900">
                                ${customer.arr.toLocaleString()}
                              </p>
                            </div>
                            <div className="bg-gradient-to-br from-orange-50 to-red-50 p-3 rounded-xl border border-orange-100">
                              <p className="text-xs text-gray-600 mb-1 font-medium">Predicted Churn</p>
                              <p className="text-lg font-bold text-orange-600 flex items-center gap-1">
                                <Clock className="w-4 h-4" />
                                {customer.days_until_churn}d
                              </p>
                            </div>
                            <div className="bg-gradient-to-br from-purple-50 to-pink-50 p-3 rounded-xl border border-purple-100">
                              <p className="text-xs text-gray-600 mb-1 font-medium">Primary Risk</p>
                              <p className="text-sm font-bold text-gray-700">{customer.risk_reason}</p>
                            </div>
                          </div>

                          <div className="flex items-center gap-2">
                            <motion.button
                              whileHover={{ scale: 1.02 }}
                              whileTap={{ scale: 0.98 }}
                              onClick={() => handleViewRecommendations(customer)}
                              className="px-5 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-xl hover:shadow-lg transition-all font-semibold flex items-center gap-2 group"
                              title="Get AI-powered analysis and retention strategies for this customer"
                            >
                              <Sparkles className="w-4 h-4" />
                              Analyze with AI
                              <ChevronRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                            </motion.button>
                            <motion.button
                              whileHover={{ scale: 1.02 }}
                              whileTap={{ scale: 0.98 }}
                              onClick={() => router.push(`/customer/${customer.id}`)}
                              className="px-5 py-2.5 bg-white border-2 border-gray-200 text-gray-700 rounded-xl hover:border-gray-300 hover:shadow transition-all font-semibold"
                            >
                              View Details
                            </motion.button>
                            <motion.button
                              whileHover={{ scale: 1.02 }}
                              whileTap={{ scale: 0.98 }}
                              onClick={() => {
                                const task = `Follow up with ${customer.name}\nPriority: ${customer.risk_score >= 80 ? 'Critical' : 'High'}\nDue: ${customer.days_until_churn} days\nAction: Address ${customer.risk_reason}`;
                                navigator.clipboard.writeText(task);
                                showToast('✓ Task copied to clipboard!');
                              }}
                              className="px-5 py-2.5 bg-gradient-to-r from-green-50 to-emerald-50 border-2 border-green-200 text-green-700 rounded-xl hover:border-green-300 transition-all font-semibold flex items-center gap-2"
                              title="Copy task details to clipboard"
                            >
                              <CheckCircle2 className="w-4 h-4" />
                              Create Task
                            </motion.button>
                          </div>
                        </div>

                        <div className="flex-shrink-0">
                          <motion.div
                            whileHover={{ scale: 1.1, rotate: 360 }}
                            transition={{ duration: 0.6 }}
                            className="relative"
                          >
                            <div className={`w-24 h-24 rounded-full ${getRiskBadgeColor(customer.risk_score)} bg-opacity-10 flex items-center justify-center border-4 ${getRiskBadgeColor(customer.risk_score)} border-opacity-30 shadow-lg`}>
                              <span className={`text-3xl font-bold ${customer.risk_score >= 80 ? 'text-red-600' : customer.risk_score >= 60 ? 'text-orange-600' : 'text-yellow-600'}`}>
                                {Math.round(customer.risk_score)}
                              </span>
                            </div>
                          </motion.div>
                          <p className="text-xs text-gray-500 text-center mt-2 font-semibold">Risk Score</p>
                        </div>
                      </div>

                      {/* Risk trend indicator */}
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.8 + idx * 0.05 }}
                        className="mt-4 pt-4 border-t border-gray-100"
                      >
                        <div className="flex items-center gap-2 text-xs">
                          <span className="text-gray-500 font-medium">Trend:</span>
                          {customer.trend === 'increasing' && (
                            <span className="flex items-center gap-1 text-red-600 font-bold bg-red-50 px-3 py-1 rounded-full">
                              <TrendingDown className="w-3 h-3" />
                              Increasing risk - Intervention needed
                            </span>
                          )}
                          {customer.trend === 'stable' && (
                            <span className="flex items-center gap-1 text-orange-600 font-bold bg-orange-50 px-3 py-1 rounded-full">
                              <Activity className="w-3 h-3" />
                              Stable - Monitor closely
                            </span>
                          )}
                          {customer.trend === 'decreasing' && (
                            <span className="flex items-center gap-1 text-green-600 font-bold bg-green-50 px-3 py-1 rounded-full">
                              <TrendingUp className="w-3 h-3" />
                              Decreasing - Strategies working
                            </span>
                          )}
                        </div>
                      </motion.div>
                    </motion.div>
                    ))
                  )}
                </div>
              </motion.div>

              {/* Bottom Feature Cards */}
              <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-6">
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  whileHover={{ y: -5, boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)" }}
                  transition={{ delay: 0.9 }}
                  className="bg-white p-6 rounded-2xl shadow-lg border border-gray-100 hover:shadow-2xl transition-all group cursor-pointer"
                >
                  <div className="bg-gradient-to-br from-blue-100 to-blue-50 w-14 h-14 rounded-xl flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
                    <Target className="w-7 h-7 text-blue-600" />
                  </div>
                  <h3 className="font-bold text-gray-900 mb-2 text-lg">Predictive Intelligence</h3>
                  <p className="text-sm text-gray-600 leading-relaxed">
                    AI-powered risk scoring predicts churn 7-30 days early with 94.7% accuracy
                  </p>
                </motion.div>

                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  whileHover={{ y: -5, boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)" }}
                  transition={{ delay: 1.0 }}
                  className="bg-white p-6 rounded-2xl shadow-lg border border-gray-100 hover:shadow-2xl transition-all group cursor-pointer"
                >
                  <div className="bg-gradient-to-br from-green-100 to-green-50 w-14 h-14 rounded-xl flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
                    <Sparkles className="w-7 h-7 text-green-600" />
                  </div>
                  <h3 className="font-bold text-gray-900 mb-2 text-lg">Smart Playbooks</h3>
                  <p className="text-sm text-gray-600 leading-relaxed">
                    Context-aware retention strategies retrieved from proven successful interventions
                  </p>
                </motion.div>

                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  whileHover={{ y: -5, boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)" }}
                  transition={{ delay: 1.1 }}
                  className="bg-white p-6 rounded-2xl shadow-lg border border-gray-100 hover:shadow-2xl transition-all group cursor-pointer"
                >
                  <div className="bg-gradient-to-br from-purple-100 to-purple-50 w-14 h-14 rounded-xl flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
                    <Zap className="w-7 h-7 text-purple-600" />
                  </div>
                  <h3 className="font-bold text-gray-900 mb-2 text-lg">Proactive Alerts</h3>
                  <p className="text-sm text-gray-600 leading-relaxed">
                    Real-time monitoring with automated CSM task creation and Slack notifications
                  </p>
                </motion.div>
              </div>
            </motion.div>
          ) : (
            /* AI Chatbot Tab */
            <motion.div
              key="analyze"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              className="h-[800px] rounded-2xl shadow-xl border border-gray-100 overflow-hidden"
            >
              <Chatbot
                messages={messages}
                query={query}
                setQuery={setQuery}
                onSubmit={handleSubmit}
                onClear={clearChat}
                onSuggestedPrompt={handleSuggestedPrompt}
                loading={loading}
                isTyping={isTyping}
                messagesEndRef={messagesEndRef}
                useMultiAgent={useMultiAgent}
                setUseMultiAgent={setUseMultiAgent}
                backendStatus={backendStatus}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Toast Notification */}
      <AnimatePresence>
        {toastMessage && (
          <motion.div
            initial={{ opacity: 0, y: 50, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.9 }}
            transition={{ type: 'spring', stiffness: 300, damping: 25 }}
            className="fixed bottom-8 right-8 z-[100]"
          >
            <div className={`${
              toastMessage.includes('✓') || toastMessage.includes('copied')
                ? 'bg-gradient-to-r from-green-600 to-emerald-600'
                : 'bg-gradient-to-r from-blue-600 to-indigo-600'
            } text-white px-6 py-4 rounded-xl shadow-2xl flex items-center gap-3 min-w-[320px] max-w-md border-2 border-white/20`}>
              {toastMessage.includes('✓') || toastMessage.includes('copied') ? (
                <CheckCircle2 className="w-6 h-6 flex-shrink-0 animate-bounce" />
              ) : (
                <Sparkles className="w-6 h-6 flex-shrink-0" />
              )}
              <span className="font-semibold text-sm leading-relaxed">{toastMessage}</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  );
}
