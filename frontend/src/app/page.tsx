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
    <main className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white/80 backdrop-blur-sm border-b border-gray-200/60 sticky top-0 z-50">
        <div className="max-w-[1400px] mx-auto px-8 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
                  <Shield className="w-5 h-5 text-white" />
                </div>
                <div>
                  <h1 className="text-lg font-semibold text-gray-900">ChurnGuard AI</h1>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {/* Navigation */}
              <nav className="flex items-center gap-1">
                <button
                  onClick={() => setActiveTab('dashboard')}
                  className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                    activeTab === 'dashboard'
                      ? 'bg-gray-100 text-gray-900'
                      : 'text-gray-600 hover:text-gray-900 hover:bg-gray-50'
                  }`}
                >
                  Dashboard
                </button>
                <button
                  onClick={() => setActiveTab('analyze')}
                  className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                    activeTab === 'analyze'
                      ? 'bg-gray-100 text-gray-900'
                      : 'text-gray-600 hover:text-gray-900 hover:bg-gray-50'
                  }`}
                >
                  AI Analysis
                </button>
                <Link
                  href="/analytics"
                  className="px-3 py-1.5 text-sm font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-50 rounded-md transition-colors"
                >
                  Analytics
                </Link>
                <Link
                  href="/integrations"
                  className="px-3 py-1.5 text-sm font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-50 rounded-md transition-colors"
                >
                  Integrations
                </Link>
                <Link
                  href="/evaluations"
                  className="px-3 py-1.5 text-sm font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-50 rounded-md transition-colors"
                >
                  Metrics
                </Link>
              </nav>

              <div className="h-6 w-px bg-gray-200"></div>

              <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-gray-50">
                <div className={`w-1.5 h-1.5 rounded-full ${
                  backendStatus === 'online' ? 'bg-green-500' :
                  backendStatus === 'offline' ? 'bg-red-500' :
                  'bg-yellow-500'
                }`} />
                <span className="text-xs text-gray-600 font-medium">
                  {backendStatus === 'online' ? 'Live' :
                   backendStatus === 'offline' ? 'Offline' :
                   'Connecting...'}
                </span>
              </div>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-[1400px] mx-auto px-8 py-6">
        <AnimatePresence mode="wait">
          {activeTab === 'dashboard' ? (
            <motion.div
              key="dashboard"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
            >
              {/* Page Title */}
              <div className="mb-6">
                <h2 className="text-2xl font-semibold text-gray-800">Customer Health Overview</h2>
                <p className="text-sm text-gray-600 mt-1">Monitor at-risk accounts and take proactive action</p>
              </div>

              {/* Alert Banner */}
              {dashboardStats && dashboardStats.critical_risk_count > 0 && (
                <div className="mb-6 bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
                  <div className="flex-shrink-0 w-5 h-5 bg-red-500 rounded-full flex items-center justify-center mt-0.5">
                    <AlertTriangle className="w-3 h-3 text-white" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm font-semibold text-red-900">
                      {dashboardStats.critical_risk_count} {dashboardStats.critical_risk_count === 1 ? 'customer needs' : 'customers need'} immediate attention
                    </p>
                    <p className="text-xs text-red-700 mt-0.5">High churn risk detected - Review and take action</p>
                  </div>
                </div>
              )}

              {/* Stats Cards */}
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
                    <div className="bg-white/60 backdrop-blur-sm rounded-xl border border-gray-200/50 p-5 shadow-sm">
                      <div className="flex items-center gap-3 mb-3">
                        <div className="w-10 h-10 bg-red-50 rounded-lg flex items-center justify-center">
                          <AlertTriangle className="w-5 h-5 text-red-500" />
                        </div>
                        <div className="flex-1">
                          <p className="text-xs text-gray-600 font-medium">At-Risk Customers</p>
                          <p className="text-2xl font-semibold text-gray-800 mt-0.5">{dashboardStats?.total_at_risk || 0}</p>
                        </div>
                      </div>
                      {dashboardStats && dashboardStats.critical_risk_count > 0 && (
                        <div className="mt-3 pt-3 border-t border-gray-100">
                          <p className="text-xs text-red-500 font-medium">
                            {dashboardStats.critical_risk_count} critical {dashboardStats.critical_risk_count === 1 ? 'case' : 'cases'}
                          </p>
                        </div>
                      )}
                    </div>

                    <div className="bg-white/60 backdrop-blur-sm rounded-xl border border-gray-200/50 p-5 shadow-sm">
                      <div className="flex items-center gap-3 mb-3">
                        <div className="w-10 h-10 bg-orange-50 rounded-lg flex items-center justify-center">
                          <DollarSign className="w-5 h-5 text-orange-500" />
                        </div>
                        <div className="flex-1">
                          <p className="text-xs text-gray-600 font-medium">ARR at Risk</p>
                          <p className="text-2xl font-semibold text-gray-800 mt-0.5">
                            ${dashboardStats ? (dashboardStats.total_arr_at_risk / 1000).toFixed(0) : 0}K
                          </p>
                        </div>
                      </div>
                      <div className="mt-3 pt-3 border-t border-gray-100">
                        <p className="text-xs text-gray-600 font-medium">
                          Across {dashboardStats?.total_at_risk || 0} accounts
                        </p>
                      </div>
                    </div>

                    <div className="bg-white/60 backdrop-blur-sm rounded-xl border border-gray-200/50 p-5 shadow-sm">
                      <div className="flex items-center gap-3 mb-3">
                        <div className="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center">
                          <Target className="w-5 h-5 text-green-600" />
                        </div>
                        <div className="flex-1">
                          <p className="text-xs text-gray-600 font-medium">Prediction Accuracy</p>
                          <p className="text-2xl font-semibold text-gray-800 mt-0.5">
                            {dashboardStats ? (dashboardStats.prediction_accuracy * 100).toFixed(1) : 0}%
                          </p>
                        </div>
                      </div>
                      <div className="mt-3 pt-3 border-t border-gray-100">
                        <p className="text-xs text-green-600 font-medium">Industry leading</p>
                      </div>
                    </div>

                    <div className="bg-white/60 backdrop-blur-sm rounded-xl border border-gray-200/50 p-5 shadow-sm">
                      <div className="flex items-center gap-3 mb-3">
                        <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
                          <Clock className="w-5 h-5 text-blue-500" />
                        </div>
                        <div className="flex-1">
                          <p className="text-xs text-gray-600 font-medium">Early Warning Time</p>
                          <p className="text-2xl font-semibold text-gray-800 mt-0.5">
                            {dashboardStats ? Math.round(dashboardStats.avg_days_to_churn) : 0}d
                          </p>
                        </div>
                      </div>
                      <div className="mt-3 pt-3 border-t border-gray-100">
                        <p className="text-xs text-blue-500 font-medium">7-30 day window</p>
                      </div>
                    </div>
                  </>
                )}
              </div>

              {/* At-Risk Customers List */}
              <div className="bg-white/60 backdrop-blur-sm rounded-xl border border-gray-200/50 overflow-hidden shadow-sm">
                <div className="px-6 py-4 border-b border-gray-200/60 flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-semibold text-gray-800">At-Risk Customers</h3>
                    <p className="text-xs text-gray-600 mt-0.5">
                      {atRiskCustomers.length} customers requiring attention
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={handleRefresh}
                      disabled={refreshing}
                      className="p-2 border border-gray-300 text-gray-700 rounded-md hover:bg-gray-50 transition-colors disabled:opacity-50"
                    >
                      <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
                    </button>
                    <button
                      onClick={handleExport}
                      className="px-3 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 transition-colors flex items-center gap-1.5"
                    >
                      <Download className="w-3.5 h-3.5" />
                      Export
                    </button>
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
                    <div className="p-12 text-center">
                      <div className="bg-green-50 w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-3">
                        <CheckCircle2 className="w-8 h-8 text-green-600" />
                      </div>
                      <p className="text-lg font-bold text-gray-900 mb-1">All Clear!</p>
                      <p className="text-gray-600">No at-risk customers detected</p>
                    </div>
                  ) : (
                    atRiskCustomers.map((customer) => (
                    <div
                      key={customer.id}
                      className="p-5 hover:bg-gray-50/50 transition-colors cursor-pointer"
                    >
                      <div className="flex items-center justify-between gap-6">
                        <div className="flex-1 min-w-0">
                          {/* Customer Header */}
                          <div className="flex items-center gap-3 mb-3">
                            <h4 className="text-sm font-semibold text-gray-800 truncate">
                              {customer.name}
                            </h4>
                            <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${getRiskColor(customer.risk_score)}`}>
                              {customer.risk_score}% Risk
                            </span>
                            <span className="inline-flex px-2 py-0.5 bg-gray-100 text-gray-700 rounded text-xs font-medium">
                              {customer.segment}
                            </span>
                          </div>

                          {/* Metrics Grid */}
                          <div className="grid grid-cols-3 gap-4 mb-3">
                            <div>
                              <p className="text-xs text-gray-500 mb-0.5">Annual Revenue</p>
                              <p className="text-sm font-semibold text-gray-900">
                                ${customer.arr.toLocaleString()}
                              </p>
                            </div>
                            <div>
                              <p className="text-xs text-gray-500 mb-0.5">Days to Churn</p>
                              <p className="text-sm font-semibold text-orange-600">
                                {customer.days_until_churn} days
                              </p>
                            </div>
                            <div>
                              <p className="text-xs text-gray-500 mb-0.5">Risk Reason</p>
                              <p className="text-sm font-medium text-gray-900 truncate">{customer.risk_reason}</p>
                            </div>
                          </div>

                          {/* Actions */}
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => handleViewRecommendations(customer)}
                              className="px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-md hover:bg-blue-700 transition-colors flex items-center gap-1.5"
                              title="Get AI-powered analysis"
                            >
                              <Sparkles className="w-3.5 h-3.5" />
                              Analyze
                            </button>
                            <button
                              onClick={() => router.push(`/customer/${customer.id}`)}
                              className="px-3 py-1.5 border border-gray-300 text-gray-700 text-xs font-medium rounded-md hover:bg-gray-50 transition-colors"
                            >
                              View Details
                            </button>
                            <button
                              onClick={() => {
                                const task = `Follow up with ${customer.name}\nPriority: ${customer.risk_score >= 80 ? 'Critical' : 'High'}\nDue: ${customer.days_until_churn} days\nAction: Address ${customer.risk_reason}`;
                                navigator.clipboard.writeText(task);
                                showToast('✓ Task copied!');
                              }}
                              className="px-3 py-1.5 border border-gray-300 text-gray-700 text-xs font-medium rounded-md hover:bg-gray-50 transition-colors flex items-center gap-1.5"
                              title="Create task"
                            >
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              Create Task
                            </button>
                          </div>
                        </div>

                        {/* Risk Score Badge */}
                        <div className="flex-shrink-0">
                          <div className={`w-16 h-16 rounded-lg ${getRiskBadgeColor(customer.risk_score)} bg-opacity-10 flex flex-col items-center justify-center border ${getRiskBadgeColor(customer.risk_score)} border-opacity-20`}>
                            <span className={`text-xl font-bold ${customer.risk_score >= 80 ? 'text-red-600' : customer.risk_score >= 60 ? 'text-orange-600' : 'text-yellow-600'}`}>
                              {Math.round(customer.risk_score)}
                            </span>
                            <span className="text-[10px] text-gray-600 font-medium mt-0.5">RISK</span>
                          </div>
                        </div>
                      </div>
                    </div>
                    ))
                  )}
                </div>
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
