/**
 * API Client for Customer Churn RAG Backend
 * TypeScript interfaces and API call functions
 */

// API Configuration
// Same-origin. next.config.js rewrites /api/* to the backend server-side, so
// this is correct both on a laptop and when deployed, with no build-time
// address baked in and nothing for the browser to block as cross-origin.
const API_BASE_URL = '/api';

// Without a timeout a request can hang indefinitely and the caller's `finally`
// never runs, so the UI spins on skeletons forever with no way to tell that
// anything is wrong. A rewrite that points at a backend which is down or
// unreachable stalls the same way: the request neither resolves nor rejects
// promptly, and the caller never learns anything went wrong.
//
// Ninety seconds, which is far longer than any endpoint needs and deliberately
// so. The slowest measured path is /book/exposure, well inside a second once the
// service is warm -- but the backend runs on a free tier that sleeps after ~15
// minutes idle, and a cold start takes 50 seconds or more before the first byte.
// At the previous 10s every first visit aborted and showed an empty queue, which
// is the worst possible impression for a link someone opened once.
const REQUEST_TIMEOUT_MS = 90_000;

// A request slower than this is almost certainly a sleeping backend waking up,
// not a fast one being slow. Used only to change what the UI says while waiting.
export const COLD_START_HINT_MS = 4_000;

/** fetch that fails fast instead of hanging, preserving the caller's error handling. */
async function fetchWithTimeout(input: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (e) {
    // An abort and a refused connection are the same thing to a caller: the
    // backend could not be reached. Naming it that way keeps the UI honest --
    // "unreachable" rather than a stack trace or a silent empty list.
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new APIError(0, 'Timeout', `No response from ${API_BASE_URL} within ${REQUEST_TIMEOUT_MS / 1000}s`);
    }
    throw new APIError(0, 'Unreachable', `Could not reach ${API_BASE_URL}`);
  } finally {
    clearTimeout(timer);
  }
}

// Type Definitions
export interface Source {
  content: string;
  metadata: Record<string, any>;
  relevance_score: number;
}

export interface Metrics {
  response_time_ms: number;
  tokens_used: number;
  retrieval_method?: string;
  documents_found?: number;
  agent_steps?: number;
}

export interface AskResponse {
  answer: string;
  sources: Source[];
  metrics: Metrics;
}

export interface ChurnAnalysisResponse {
  answer: string;
  customer_id?: string;
  churn_risk_score?: number;
  recommendations?: string[];
  sources: Source[];
  metrics: Metrics;
}

export interface MultiAgentResponse {
  query: string;
  query_type?: string;
  response: string;
  background_context?: string;
  key_insights: string[];
  citations: Array<{
    citation_id?: string;
    type?: string;
    customer?: string;
    segment?: string;
    churn_reason?: string;
    arr_lost?: string;
    title?: string;
    url?: string;
    relevance?: string;
  }>;
  style_notes: string[];
  confidence_score: number;
  processing_stages: string[];
  total_sources: number;
  errors: string[];
}

export interface HealthResponse {
  status: string;
  timestamp: string;
  service: string;
}

// API Error Class
export class APIError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    message: string
  ) {
    super(message);
    this.name = 'APIError';
  }
}

// API Client Functions
export const apiClient = {
  /**
   * Check if the backend API is healthy
   */
  async health(): Promise<HealthResponse> {
    const response = await fetchWithTimeout(`${API_BASE_URL}/health`);
    
    if (!response.ok) {
      throw new APIError(
        response.status,
        response.statusText,
        'Health check failed'
      );
    }
    
    return response.json();
  },

  /**
   * Ask a general question about churn patterns
   */
  async ask(
    question: string, 
    retrieverType: string = 'parent_document',
    maxResponseLength: number = 2000
  ): Promise<AskResponse> {
    const response = await fetchWithTimeout(`${API_BASE_URL}/ask`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        question,
        retriever_type: retrieverType,
        max_response_length: maxResponseLength,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new APIError(
        response.status,
        response.statusText,
        errorData.detail || 'Failed to get answer'
      );
    }

    return response.json();
  },

  /**
   * Analyze customer churn with agent-based reasoning
   */
  async analyzeChurn(
    query: string,
    customerId?: string,
    includeRecommendations: boolean = true
  ): Promise<ChurnAnalysisResponse> {
    const response = await fetchWithTimeout(`${API_BASE_URL}/analyze-churn`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query,
        customer_id: customerId,
        include_recommendations: includeRecommendations,
        max_response_length: 2000,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new APIError(
        response.status,
        response.statusText,
        errorData.detail || 'Churn analysis failed'
      );
    }

    return response.json();
  },

  /**
   * Multi-Agent Analysis with Research Team + Writing Team
   */
  async multiAgentAnalyze(
    query: string,
    includeBackground: boolean = true,
    includeCitations: boolean = true
  ): Promise<MultiAgentResponse> {
    const response = await fetchWithTimeout(`${API_BASE_URL}/multi-agent-analyze`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query,
        include_background: includeBackground,
        include_citations: includeCitations,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new APIError(
        response.status,
        response.statusText,
        errorData.detail || 'Multi-agent analysis failed'
      );
    }

    return response.json();
  },

  getEvaluationResults: async (): Promise<EvaluationResponse> => {
    const response = await fetchWithTimeout(`${API_BASE_URL}/evaluation-results`);

    if (!response.ok) {
      throw new APIError(
        response.status,
        response.statusText,
        'Failed to fetch evaluation results'
      );
    }

    return response.json();
  },

  /**
   * Get list of at-risk customers
   */
  async getAtRiskCustomers(riskThreshold: number = 60, limit: number = 10): Promise<{ at_risk_customers: AtRiskCustomer[], total_count: number, risk_threshold: number }> {
    const response = await fetchWithTimeout(`${API_BASE_URL}/at-risk-customers?risk_threshold=${riskThreshold}&limit=${limit}`);

    if (!response.ok) {
      throw new APIError(
        response.status,
        response.statusText,
        'Failed to fetch at-risk customers'
      );
    }

    return response.json();
  },

  /**
   * Get dashboard statistics
   */
  async getDashboardStats(): Promise<DashboardStats> {
    const response = await fetchWithTimeout(`${API_BASE_URL}/dashboard-stats`);

    if (!response.ok) {
      throw new APIError(
        response.status,
        response.statusText,
        'Failed to fetch dashboard stats'
      );
    }

    return response.json();
  },
};

// Helper function to format error messages for display
export interface EvaluationResult {
  method: string;
  faithfulness: number;
  answer_relevancy: number;
  context_recall: number;
  context_precision: number;
  answer_correctness: number;
  semantic_similarity: number;
}

export interface EvaluationResponse {
  results: EvaluationResult[];
  metrics_info: Record<string, string>;
  note: string;
}

export interface AtRiskCustomer {
  id: number;
  name: string;
  segment: string;
  arr: number;
  tenure_years: number;
  risk_score: number;
  days_until_churn: number;
  risk_reason: string;
  trend: string;
  last_engagement_days: number;
  support_tickets_30d: number;
  feature_adoption_rate: number;
  competitor?: string;
}

export interface DashboardStats {
  total_at_risk: number;
  critical_risk_count: number;
  total_arr_at_risk: number;
  avg_days_to_churn: number;
  historical_churn_rate: number;
  as_of: string;
  total_active_customers: number;
}

export function formatAPIError(error: unknown): string {
  if (error instanceof APIError) {
    if (error.status === 503) {
      return '⚠️  Backend service is not ready. Please ensure Qdrant is running and the API keys are configured.';
    }
    if (error.status === 500) {
      return `❌ Server error: ${error.message}`;
    }
    return `❌ API Error (${error.status}): ${error.message}`;
  }
  
  if (error instanceof Error) {
    return `❌ ${error.message}`;
  }
  
  return '❌ An unexpected error occurred';
}

