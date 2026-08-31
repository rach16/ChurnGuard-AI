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

// $2234K reads as noise. Scale the unit to the magnitude so the number is legible
// at a glance, which is the only job a dashboard tile has.
function formatMoney(value: number): string {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

// One metric. Deliberately plain: a label, a number, and a hint that says what the
// number measures. The previous version wrapped each in a card with a coloured
// icon chip, which cost 230px of vertical space and encoded nothing.
function Metric({
  label,
  value,
  hint,
  severe = false,
}: {
  label: string;
  value: string | number;
  hint: string;
  severe?: boolean;
}) {
  return (
    <div className="px-5 py-3 min-w-[140px]">
      <p className="text-[11px] uppercase tracking-wide text-slate-400 font-medium">{label}</p>
      <p
        className={`text-2xl font-semibold tabular-nums mt-1 ${
          severe ? 'text-red-600' : 'text-slate-900'
        }`}
      >
        {value}
      </p>
      <p className="text-xs text-slate-400 mt-0.5">{hint}</p>
    </div>
  );
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
              {/* Header + metric strip.
                  Previously this region was ~600px: a title block, an alert banner
                  restating "1 critical case", and four oversized cards with pastel
                  icon chips. You scrolled past all of it before seeing a customer,
                  which is the only thing on the page anyone acts on. */}
              <div className="mb-6 flex items-end justify-between gap-6 flex-wrap">
                <div>
                  <h2 className="text-xl font-semibold tracking-tight text-slate-900">
                    Customer Health
                  </h2>
                  <p className="text-sm text-slate-500 mt-0.5">
                    {dashboardStats
                      ? `${dashboardStats.total_active_customers} active accounts \u00b7 as of ${dashboardStats.as_of}`
                      : 'Loading\u2026'}
                  </p>
                </div>

                {/* Colour is reserved for severity. Everything else is neutral, so
                    red on the page always means the same thing. */}
                <div className="flex items-stretch divide-x divide-slate-200 rounded-lg border border-slate-200 bg-white">
                  <Metric
                    label="At risk"
                    value={dashboardStats?.total_at_risk ?? 0}
                    hint={
                      dashboardStats?.critical_risk_count
                        ? `${dashboardStats.critical_risk_count} critical`
                        : 'none critical'
                    }
                    severe={Boolean(dashboardStats?.critical_risk_count)}
                  />
                  <Metric
                    label="ARR at risk"
                    value={dashboardStats ? formatMoney(dashboardStats.total_arr_at_risk) : '$0'}
                    hint={`of ${dashboardStats?.total_active_customers ?? 0} accounts`}
                  />
                  <Metric
                    label="Churn rate"
                    value={dashboardStats ? `${(dashboardStats.historical_churn_rate * 100).toFixed(1)}%` : '0%'}
                    hint="all accounts to date"
                  />
                  <Metric
                    label="Median warning"
                    value={dashboardStats ? `${Math.round(dashboardStats.avg_days_to_churn)}d` : '0d'}
                    hint="across at-risk"
                  />
                </div>
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
                      /* One scannable row per account.
                         Was a ~120px card with the risk figure printed twice, in two
                         formats, so three accounts filled the viewport. A CS lead
                         triages twenty. Risk now appears once, on the left, where a
                         column of numbers can be compared down the page. */
                      <div
                        key={customer.id}
                        onClick={() => router.push(`/customer/${customer.id}`)}
                        className="group grid grid-cols-[3.5rem_minmax(0,1fr)_auto] items-center gap-4 px-5 py-3 border-b border-slate-100 last:border-0 hover:bg-slate-50 cursor-pointer transition-colors"
                      >
                        <div
                          className={`text-2xl font-semibold tabular-nums leading-none ${
                            customer.risk_score >= 80
                              ? 'text-red-600'
                              : customer.risk_score >= 60
                              ? 'text-amber-600'
                              : 'text-slate-500'
                          }`}
                        >
                          {Math.round(customer.risk_score)}
                        </div>

                        <div className="min-w-0">
                          <div className="flex items-baseline gap-2">
                            <span className="font-medium text-slate-900 truncate">{customer.name}</span>
                            <span className="text-xs text-slate-400 shrink-0">{customer.segment}</span>
                          </div>
                          <div className="text-sm text-slate-500 mt-0.5 truncate">
                            {customer.risk_reason}
                            <span className="text-slate-300 mx-1.5">|</span>
                            {formatMoney(customer.arr)}
                            <span className="text-slate-300 mx-1.5">|</span>
                            {customer.support_tickets_30d} tickets / 30d
                          </div>
                        </div>

                        <div className="flex items-center gap-4 shrink-0">
                          <div className="text-right">
                            <div className="text-sm font-medium text-slate-900 tabular-nums">
                              ~{customer.days_until_churn}d
                            </div>
                            <div className="text-xs text-slate-400">est. horizon</div>
                          </div>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              const task = `Follow up with ${customer.name}\nPriority: ${customer.risk_score >= 80 ? 'Critical' : 'High'}\nDue: ${customer.days_until_churn} days\nAction: Address ${customer.risk_reason}`;
                              navigator.clipboard.writeText(task);
                              showToast('Task copied to clipboard');
                            }}
                            className="px-2.5 py-1 text-xs text-slate-500 border border-slate-200 rounded hover:bg-white hover:text-slate-900 opacity-0 group-hover:opacity-100 transition-opacity"
                          >
                            Copy task
                          </button>
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
