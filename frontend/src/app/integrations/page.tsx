'use client';

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import {
  CheckCircle,
  XCircle,
  Settings,
  RefreshCw,
  Link2,
  AlertCircle,
  ExternalLink,
  Zap,
  Database,
  BarChart3,
  MessageSquare,
  DollarSign,
  Users,
  Calendar,
  TrendingUp,
} from 'lucide-react';

interface Integration {
  id: string;
  name: string;
  description: string;
  icon: React.ReactNode;
  status: 'connected' | 'disconnected' | 'error';
  lastSync?: string;
  recordsSynced?: number;
  category: 'crm' | 'analytics' | 'billing' | 'support';
  features: string[];
  color: string;
}

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([
    {
      id: 'salesforce',
      name: 'Salesforce',
      description: 'Sync customer data, opportunities, and account information',
      icon: <Database className="w-8 h-8" />,
      status: 'connected',
      lastSync: '2 minutes ago',
      recordsSynced: 1247,
      category: 'crm',
      features: ['Account sync', 'Opportunity tracking', 'Contact management', 'Custom fields'],
      color: 'bg-blue-500',
    },
    {
      id: 'stripe',
      name: 'Stripe',
      description: 'Monitor payment data, subscriptions, and revenue metrics',
      icon: <DollarSign className="w-8 h-8" />,
      status: 'connected',
      lastSync: '5 minutes ago',
      recordsSynced: 892,
      category: 'billing',
      features: ['Payment tracking', 'Subscription events', 'MRR calculation', 'Churn analysis'],
      color: 'bg-purple-500',
    },
    {
      id: 'mixpanel',
      name: 'Mixpanel',
      description: 'Track product usage, engagement, and behavioral analytics',
      icon: <BarChart3 className="w-8 h-8" />,
      status: 'connected',
      lastSync: '10 minutes ago',
      recordsSynced: 45231,
      category: 'analytics',
      features: ['Event tracking', 'User engagement', 'Feature adoption', 'Cohort analysis'],
      color: 'bg-indigo-500',
    },
    {
      id: 'intercom',
      name: 'Intercom',
      description: 'Sync support conversations, satisfaction scores, and ticket data',
      icon: <MessageSquare className="w-8 h-8" />,
      status: 'disconnected',
      category: 'support',
      features: ['Conversation sync', 'CSAT scores', 'Support tickets', 'Response times'],
      color: 'bg-green-500',
    },
    {
      id: 'hubspot',
      name: 'HubSpot',
      description: 'Connect marketing and sales data for comprehensive insights',
      icon: <TrendingUp className="w-8 h-8" />,
      status: 'error',
      lastSync: '2 hours ago',
      category: 'crm',
      features: ['Contact sync', 'Deal pipeline', 'Marketing campaigns', 'Lead scoring'],
      color: 'bg-orange-500',
    },
    {
      id: 'zendesk',
      name: 'Zendesk',
      description: 'Import customer support tickets and satisfaction metrics',
      icon: <Users className="w-8 h-8" />,
      status: 'disconnected',
      category: 'support',
      features: ['Ticket sync', 'CSAT tracking', 'Agent performance', 'Resolution time'],
      color: 'bg-red-500',
    },
  ]);

  const [selectedCategory, setSelectedCategory] = useState<string>('all');

  const categories = [
    { id: 'all', name: 'All Integrations', count: integrations.length },
    { id: 'crm', name: 'CRM', count: integrations.filter(i => i.category === 'crm').length },
    { id: 'analytics', name: 'Analytics', count: integrations.filter(i => i.category === 'analytics').length },
    { id: 'billing', name: 'Billing', count: integrations.filter(i => i.category === 'billing').length },
    { id: 'support', name: 'Support', count: integrations.filter(i => i.category === 'support').length },
  ];

  const filteredIntegrations = selectedCategory === 'all'
    ? integrations
    : integrations.filter(i => i.category === selectedCategory);

  const connectedCount = integrations.filter(i => i.status === 'connected').length;
  const totalRecordsSynced = integrations.reduce((sum, i) => sum + (i.recordsSynced || 0), 0);

  const handleConnect = (id: string) => {
    setIntegrations(prev => prev.map(int =>
      int.id === id ? { ...int, status: 'connected', lastSync: 'Just now', recordsSynced: 0 } : int
    ));
  };

  const handleDisconnect = (id: string) => {
    setIntegrations(prev => prev.map(int =>
      int.id === id ? { ...int, status: 'disconnected', lastSync: undefined, recordsSynced: undefined } : int
    ));
  };

  const handleSync = (id: string) => {
    setIntegrations(prev => prev.map(int =>
      int.id === id ? { ...int, lastSync: 'Just now' } : int
    ));
  };

  const getStatusIcon = (status: Integration['status']) => {
    switch (status) {
      case 'connected':
        return <CheckCircle className="w-5 h-5 text-mute" />;
      case 'error':
        return <AlertCircle className="w-5 h-5 text-mute" />;
      case 'disconnected':
        return <XCircle className="w-5 h-5 text-faint" />;
    }
  };

  const getStatusText = (status: Integration['status']) => {
    switch (status) {
      case 'connected':
        return 'Connected';
      case 'error':
        return 'Connection Error';
      case 'disconnected':
        return 'Not Connected';
    }
  };

  return (
    <div className="min-h-screen bg-sand">
      {/* Header */}
      <div className="bg-paper border-b border-hair sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold text-ink">Integrations</h1>
              <p className="text-mute mt-1">Connect your tools to power churn prediction</p>
            </div>
            <a
              href="/"
              className="px-4 py-2 text-sm font-medium text-mute bg-paper border border-hair rounded hover:bg-sand transition-colors"
            >
              Back to Dashboard
            </a>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="bg-paper rounded shadow-paper border border-hair p-6"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-mute">Active Connections</p>
                <p className="text-3xl font-bold text-ink mt-2">{connectedCount}</p>
              </div>
              <div className="p-3 bg-sand rounded">
                <Link2 className="w-6 h-6 text-mute" />
              </div>
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="bg-paper rounded shadow-paper border border-hair p-6"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-mute">Records Synced</p>
                <p className="text-3xl font-bold text-ink mt-2">{totalRecordsSynced.toLocaleString()}</p>
              </div>
              <div className="p-3 bg-sand rounded">
                <Database className="w-6 h-6 text-mute" />
              </div>
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="bg-paper rounded shadow-paper border border-hair p-6"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-mute">Available Integrations</p>
                <p className="text-3xl font-bold text-ink mt-2">{integrations.length}</p>
              </div>
              <div className="p-3 bg-sand rounded">
                <Zap className="w-6 h-6 text-mute" />
              </div>
            </div>
          </motion.div>
        </div>

        {/* Category Filter */}
        <div className="bg-paper rounded shadow-paper border border-hair p-4 mb-6">
          <div className="flex flex-wrap gap-2">
            {categories.map((category) => (
              <button
                key={category.id}
                onClick={() => setSelectedCategory(category.id)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  selectedCategory === category.id
                    ? 'bg-indigo-600 text-white'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                {category.name} ({category.count})
              </button>
            ))}
          </div>
        </div>

        {/* Integrations Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {filteredIntegrations.map((integration, index) => (
            <motion.div
              key={integration.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.1 }}
              className="bg-paper rounded shadow-paper border border-hair p-6 hover:shadow-paper transition-shadow"
            >
              {/* Integration Header */}
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center space-x-4">
                  <div className={`p-3 ${integration.color} bg-opacity-10 rounded-lg`}>
                    <div className={`${integration.color.replace('bg-', 'text-')}`}>
                      {integration.icon}
                    </div>
                  </div>
                  <div>
                    <h3 className="text-xl font-bold text-ink">{integration.name}</h3>
                    <div className="flex items-center space-x-2 mt-1">
                      {getStatusIcon(integration.status)}
                      <span className={`text-sm font-medium ${
                        integration.status === 'connected' ? 'text-mute' :
                        integration.status === 'error' ? 'text-mute' :
                        'text-gray-500'
                      }`}>
                        {getStatusText(integration.status)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Description */}
              <p className="text-mute mb-4">{integration.description}</p>

              {/* Features */}
              <div className="mb-4">
                <p className="text-sm font-medium text-mute mb-2">Features:</p>
                <div className="flex flex-wrap gap-2">
                  {integration.features.map((feature) => (
                    <span
                      key={feature}
                      className="px-2 py-1 bg-sand text-mute text-xs rounded-md"
                    >
                      {feature}
                    </span>
                  ))}
                </div>
              </div>

              {/* Sync Info */}
              {integration.status === 'connected' && (
                <div className="bg-sand rounded p-3 mb-4">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-mute">Last synced:</span>
                    <span className="font-medium text-ink">{integration.lastSync}</span>
                  </div>
                  <div className="flex items-center justify-between text-sm mt-2">
                    <span className="text-mute">Records synced:</span>
                    <span className="font-medium text-ink">{integration.recordsSynced?.toLocaleString()}</span>
                  </div>
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2">
                {integration.status === 'connected' ? (
                  <>
                    <button
                      onClick={() => handleSync(integration.id)}
                      className="flex-1 px-4 py-2 bg-ink text-white rounded hover:bg-ink transition-colors flex items-center justify-center space-x-2"
                    >
                      <RefreshCw className="w-4 h-4" />
                      <span>Sync Now</span>
                    </button>
                    <button
                      onClick={() => handleDisconnect(integration.id)}
                      className="px-4 py-2 bg-sand text-mute rounded hover:bg-hair transition-colors"
                    >
                      Disconnect
                    </button>
                    <button className="px-4 py-2 bg-sand text-mute rounded hover:bg-hair transition-colors">
                      <Settings className="w-4 h-4" />
                    </button>
                  </>
                ) : integration.status === 'error' ? (
                  <>
                    <button
                      onClick={() => handleConnect(integration.id)}
                      className="flex-1 px-4 py-2 bg-ink text-white rounded hover:bg-ink transition-colors flex items-center justify-center space-x-2"
                    >
                      <RefreshCw className="w-4 h-4" />
                      <span>Reconnect</span>
                    </button>
                    <button className="px-4 py-2 bg-sand text-mute rounded hover:bg-hair transition-colors">
                      <Settings className="w-4 h-4" />
                    </button>
                  </>
                ) : (
                  <button
                    onClick={() => handleConnect(integration.id)}
                    className="flex-1 px-4 py-2 bg-ink text-white rounded hover:bg-ink transition-colors flex items-center justify-center space-x-2"
                  >
                    <Link2 className="w-4 h-4" />
                    <span>Connect</span>
                  </button>
                )}
              </div>
            </motion.div>
          ))}
        </div>

        {/* Help Section */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          className="mt-8 bg-sand rounded border border-hair p-6"
        >
          <div className="flex items-start space-x-4">
            <div className="p-3 bg-sand rounded">
              <ExternalLink className="w-6 h-6 text-mute" />
            </div>
            <div className="flex-1">
              <h3 className="text-lg font-bold text-ink mb-2">Need Help Connecting?</h3>
              <p className="text-mute mb-4">
                Our integrations automatically sync customer data, usage metrics, and engagement signals to power accurate churn predictions. Check our documentation for setup guides.
              </p>
              <a
                href="#"
                className="inline-flex items-center space-x-2 text-mute font-medium hover:text-mute"
              >
                <span>View Documentation</span>
                <ExternalLink className="w-4 h-4" />
              </a>
            </div>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
