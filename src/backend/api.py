"""
Customer Churn RAG - FastAPI Backend
Main API server with RAG endpoints for churn analysis
"""

import os
import sys
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# Add src to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from core.rag_retrievers import ChurnRAGRetriever
from agents.churn_agent import CustomerChurnAgent
from agents.multi_agent_system import MultiAgentChurnSystem
from core.health_scoring import CustomerHealthScorer
from core.exposure import ExposureModel
from core.plays import PlaybookEngine
from core.evidence import CustomerEvidence
from core.llm import active_configuration
from model.survival import ChurnSurvivalModel
from core.llm import chat_model

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = True) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class ServiceState:
    """Tracks which subsystems came up, and why any of them did not.

    The API deliberately still serves in a degraded state: health scoring and the
    dashboard work from CSV alone, so a missing OPENAI_API_KEY should not take the
    whole service down. What it must not do is pretend those subsystems are fine --
    every unavailable component records the reason, /ready reflects it, and the
    endpoints that depend on it return 503 saying exactly what is missing.
    """

    def __init__(self) -> None:
        self.rag_retriever: Optional[ChurnRAGRetriever] = None
        self.churn_agent: Optional[CustomerChurnAgent] = None
        self.multi_agent_system: Optional[MultiAgentChurnSystem] = None
        self.health_scorer: Optional[CustomerHealthScorer] = None
        self.playbook: Optional[PlaybookEngine] = None
        self.survival: Optional[ChurnSurvivalModel] = None
        self.survival_frame = None
        self.evidence: Optional[CustomerEvidence] = None
        self.exposure: Optional[ExposureModel] = None
        self.errors: dict[str, str] = {}

    def unavailable(self, component: str, reason: str) -> None:
        self.errors[component] = reason
        logger.warning(f"⚠️  {component} unavailable: {reason}")

    @property
    def ai_ready(self) -> bool:
        """True when the LLM-backed endpoints can actually serve."""
        return self.multi_agent_system is not None and self.rag_retriever is not None

    @property
    def core_ready(self) -> bool:
        """True when the CSV-backed endpoints (dashboard, scoring) can serve."""
        return self.health_scorer is not None

    def components(self) -> dict:
        return {
            "health_scorer": self.health_scorer is not None,
            "playbook": self.playbook is not None,
            "survival_model": self.survival is not None,
            "exposure": self.exposure is not None,
            "evidence": self.evidence is not None,
            "rag_retriever": self.rag_retriever is not None,
            "churn_agent": self.churn_agent is not None,
            "multi_agent_system": self.multi_agent_system is not None,
        }


state = ServiceState()


def _init_health_scorer() -> None:
    """Health scoring works from CSV alone -- no API key, no vector store."""
    try:
        state.health_scorer = CustomerHealthScorer(
            churn_data_path=os.getenv("CHURN_DATA_PATH", "data/churned_customers_cleaned.csv")
        )
        logger.info("✓ Health scorer initialized")
    except Exception as e:
        state.unavailable("health_scorer", f"{type(e).__name__}: {e}")

    # Recommendations are computed from recorded outcomes, not generated, so they
    # work without an API key and belong in the degraded-mode tier.
    try:
        state.playbook = PlaybookEngine(os.getenv("DATA_FOLDER", "data"))
        logger.info("✓ Playbook initialized")
    except Exception as e:
        state.unavailable("playbook", f"{type(e).__name__}: {e}")

    # Evidence selects a customer's own documents by key and ranks passages with
    # BM25, so it needs neither an API key nor the vector store.
    try:
        from utils.data_loader import ChurnDataLoader
        docs = ChurnDataLoader(os.getenv("DATA_FOLDER", "data")).get_all_documents()
        state.evidence = CustomerEvidence(docs)
        logger.info("✓ Evidence index initialized")
    except Exception as e:
        state.unavailable("evidence", f"{type(e).__name__}: {e}")

    # The likelihood band is read from a model artefact and the warehouse, neither
    # of which needs an API key, so it belongs in the CSV-backed tier too.
    _init_survival()


def _init_survival() -> None:
    """Load the trained survival model and the feature rows it scores."""
    model_path = Path(os.getenv("SURVIVAL_MODEL_PATH", "models/survival.joblib"))
    warehouse = Path(os.getenv("DUCKDB_PATH", "warehouse/churnguard.duckdb"))

    if not model_path.exists():
        state.unavailable("survival_model", f"no model at {model_path}; run scripts/train_survival_model.py")
        return
    if not warehouse.exists():
        state.unavailable("survival_model", f"no warehouse at {warehouse}; run dbt")
        return

    try:
        import duckdb

        state.survival = ChurnSurvivalModel.load(model_path)
        con = duckdb.connect(str(warehouse), read_only=True)
        try:
            # One row per customer: their most recent observation.
            state.survival_frame = con.execute("""
                select * from main_gold.train_survival
                qualify row_number() over (partition by customer_id order by week_start desc) = 1
            """).df()
        finally:
            con.close()
        logger.info(f"✓ Survival model loaded ({len(state.survival_frame)} customers scored)")
    except Exception as e:
        state.survival = None
        state.unavailable("survival_model", f"{type(e).__name__}: {e}")
        return

    # Exposure needs the model for probability and the playbook for the recovery
    # sensitivity. It degrades to exposure-only without the playbook rather than
    # failing, because the money figure is useful on its own.
    state.exposure = ExposureModel(state.survival, state.playbook)
    if state.playbook is None:
        logger.info("Exposure: no playbook, so no recovery estimate")


AI_COMPONENTS = ("rag_retriever", "churn_agent", "multi_agent_system")


def _ai_unavailable(reason: str) -> None:
    """Record one reason against every AI component, so each 503 explains itself."""
    for component in AI_COMPONENTS:
        state.unavailable(component, reason)


def _init_ai_stack() -> None:
    """Bring up the RAG retriever and agents. Any failure leaves the API degraded."""
    if not _env_flag("ENABLE_RAG", True):
        _ai_unavailable("disabled via ENABLE_RAG=false")
        return

    if not os.getenv("OPENAI_API_KEY"):
        _ai_unavailable("OPENAI_API_KEY is not set")
        return

    try:
        logger.info("📊 Loading RAG retriever...")
        state.rag_retriever = ChurnRAGRetriever(
            collection_name=os.getenv("COLLECTION_NAME", "churn_corpus"),
            qdrant_url=os.getenv("QDRANT_URL", "http://qdrant:6333"),
        )
        num_docs = state.rag_retriever.load_and_process_documents(
            data_folder=os.getenv("DATA_FOLDER", "data")
        )
        logger.info(f"✓ Indexed {num_docs} documents")
    except Exception as e:
        state.rag_retriever = None
        _ai_unavailable(f"{type(e).__name__}: {e}")
        return

    use_tavily = bool(os.getenv("TAVILY_API_KEY"))
    for name, factory, attr in (
        ("churn_agent", CustomerChurnAgent, "churn_agent"),
        ("multi_agent_system", MultiAgentChurnSystem, "multi_agent_system"),
    ):
        try:
            setattr(state, attr, factory(
                rag_retriever=state.rag_retriever,
                use_tavily=use_tavily,
            ))
            logger.info(f"✓ {name} initialized")
        except Exception as e:
            state.unavailable(name, f"{type(e).__name__}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start subsystems independently so one failure cannot take down the rest."""
    logger.info("🚀 Starting Customer Churn API...")

    _init_health_scorer()
    _init_ai_stack()

    if state.ai_ready:
        logger.info("✅ Fully initialized -- AI endpoints available")
    elif state.core_ready:
        logger.warning(
            "⚠️  Running DEGRADED: dashboard and health scoring available, "
            f"AI endpoints will return 503. Reasons: {state.errors}"
        )
    else:
        logger.error(f"❌ Nothing initialized. Reasons: {state.errors}")

    yield
    logger.info("Shutting down")


# Initialize FastAPI app
app = FastAPI(
    title="Customer Churn RAG API",
    description="AI-powered customer churn prediction and analysis using RAG",
    version="0.4.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS. A wildcard origin with credentials is rejected by browsers, so credentials
# are only enabled when an explicit origin list is configured.
_origins = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=bool(_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_ai(component: str):
    """Return an initialized AI component, or explain precisely what is missing."""
    obj = getattr(state, component, None)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": f"{component} is not available",
                "reason": state.errors.get(component, "not initialized"),
                "components": state.components(),
            },
        )
    return obj


def require_health_scorer() -> CustomerHealthScorer:
    if state.health_scorer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "health_scorer is not available",
                "reason": state.errors.get("health_scorer", "not initialized"),
            },
        )
    return state.health_scorer


# Request/Response Models
class ChurnAnalysisRequest(BaseModel):
    """Request model for churn analysis"""
    customer_id: Optional[str] = Field(None, description="Customer ID to analyze")
    query: str = Field(..., description="Question about churn analysis")
    include_recommendations: bool = Field(True, description="Include retention recommendations")
    max_response_length: int = Field(2000, ge=100, le=4000)


class AskRequest(BaseModel):
    """Request model for general questions"""
    question: str = Field(..., description="Question about churn patterns")
    retriever_type: str = Field(
        "hybrid", 
        description="Retrieval method: 'hybrid' (default, measured best), 'naive', 'multi_query', 'parent_document', 'contextual_compression'"
    )
    max_response_length: int = Field(2000, ge=100, le=4000)


class MultiAgentRequest(BaseModel):
    """Request model for multi-agent analysis"""
    query: str = Field(..., description="Question for comprehensive multi-agent analysis")
    include_background: bool = Field(True, description="Include background context from research team")
    include_citations: bool = Field(True, description="Include detailed citations")


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    timestamp: str
    service: str
    degraded: bool = False
    components: dict = {}
    errors: dict = {}
    llm: dict = {}


class ChurnAnalysisResponse(BaseModel):
    """Response model for churn analysis"""
    answer: str
    customer_id: Optional[str]
    churn_risk_score: Optional[float]
    recommendations: Optional[list[str]]
    sources: list[dict]
    metrics: dict


class MultiAgentResponse(BaseModel):
    """Response model for multi-agent analysis"""
    query: str
    query_type: Optional[str]
    response: str
    background_context: Optional[str]
    key_insights: list[str]
    citations: list[dict]
    style_notes: list[str]
    confidence_score: float
    processing_stages: list[str]
    total_sources: int
    errors: list[str]


# Health check endpoint
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Liveness check. Always 200 while the process is up.

    Reports which subsystems actually initialized, so a degraded service is visible
    rather than looking identical to a fully healthy one. Use /ready for load
    balancer and orchestrator health checks -- this endpoint deliberately does not
    fail, so on its own it cannot gate traffic.
    """
    return HealthResponse(
        status="healthy" if state.ai_ready else "degraded" if state.core_ready else "unhealthy",
        timestamp=datetime.now().isoformat(),
        service="customer-churn-rag-api",
        degraded=not state.ai_ready,
        components=state.components(),
        errors=state.errors,
        # Which provider is actually configured, so a deployment can be audited
        # without reading the environment. Never includes a credential.
        llm=active_configuration(),
    )


@app.get("/ready")
async def readiness_check(require_ai_stack: bool = False):
    """Readiness check for load balancers and orchestrators.

    Returns 503 when the service cannot serve, so an ALB or ECS health check marks
    the target unhealthy instead of routing traffic to a broken instance. By default
    readiness means the CSV-backed endpoints work; pass require_ai_stack=true to also
    demand the LLM stack.
    """
    ready = state.ai_ready if require_ai_stack else state.core_ready
    payload = {
        "ready": ready,
        "timestamp": datetime.now().isoformat(),
        "components": state.components(),
        "errors": state.errors,
    }
    if not ready:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=payload)
    return payload


# Main analysis endpoint
@app.post("/analyze-churn", response_model=ChurnAnalysisResponse)
async def analyze_churn(request: ChurnAnalysisRequest):
    """
    Analyze customer churn risk and provide recommendations using LangGraph agent
    
    Uses the CustomerChurnAgent for intelligent multi-step reasoning,
    tool selection, and comprehensive churn analysis.
    """
    logger.info(f"Churn analysis request: {request.customer_id or 'general'}")
    
    # Check if agent is initialized
    churn_agent = require_ai("churn_agent")
    
    start_time = time.time()
    
    try:
        # Run agent analysis
        logger.info("🤖 Running agent analysis...")
        # LangGraph's run() is synchronous and takes seconds. Awaiting it directly
        # from an async handler blocks the event loop, so a single worker serialises
        # every request behind it. Hand it to a thread instead.
        result = await run_in_threadpool(
            churn_agent.run,
            query=request.query,
            customer_id=request.customer_id,
        )
        
        # Extract recommendations if requested
        recommendations = None
        if request.include_recommendations and "recommendations" in result:
            recommendations = result["recommendations"]
            if isinstance(recommendations, str):
                # Parse string recommendations into list
                recommendations = [r.strip() for r in recommendations.split("\n") if r.strip()]
            elif isinstance(recommendations, list):
                # Convert dict recommendations to strings
                formatted_recommendations = []
                for rec in recommendations:
                    if isinstance(rec, dict):
                        # Format dict as string
                        rec_str = rec.get("Recommendation", str(rec))
                        if "Priority" in rec:
                            rec_str = f"[{rec['Priority']}] {rec_str}"
                        formatted_recommendations.append(rec_str)
                    else:
                        formatted_recommendations.append(str(rec))
                recommendations = formatted_recommendations
        
        # Calculate metrics
        response_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ Analysis completed in {response_time}ms")
        
        return ChurnAnalysisResponse(
            answer=result.get("analysis", "Analysis completed but no detailed response generated."),
            customer_id=request.customer_id,
            churn_risk_score=result.get("confidence_score", None),
            recommendations=recommendations,
            sources=[
                {
                    "document": doc.metadata.get("source", "unknown"),
                    "relevance_score": doc.metadata.get("score", 0.0),
                    "content": doc.page_content[:200] + ("..." if len(doc.page_content) > 200 else "")
                }
                for doc in result.get("documents", [])[:5]  # Limit to top 5 sources
            ],
            metrics={
                "response_time_ms": response_time,
                "tokens_used": result.get("metrics", {}).get("tokens_used", 0),
                "retrieval_method": result.get("retrieval_method", "agent-selected"),
                "agent_steps": len(result.get("errors", [])) + 1  # Rough estimate
            }
        )
        
    except Exception as e:
        logger.error(f"Error in churn analysis: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Churn analysis failed: {str(e)}"
        )


# General question endpoint
@app.post("/ask")
async def ask_question(request: AskRequest):
    """
    Ask general questions about churn patterns and insights using RAG
    
    Uses parent document retrieval (best performing method) to find relevant
    information and generates contextual answers.
    """
    logger.info(f"Question received: {request.question[:50]}...")
    
    # Check if RAG system is initialized
    rag_retriever = require_ai("rag_retriever")
    
    start_time = time.time()
    
    try:
        # Select retrieval method (defaults to parent_document - best performer from RAGAS evaluation)
        logger.info(f"🔍 Retrieving relevant documents using: {request.retriever_type}")
        
        # Map retriever type to method
        retriever_methods = {
            "naive": rag_retriever.naive_retrieval,
            "hybrid": rag_retriever.hybrid_retrieval,
            "multi_query": rag_retriever.multi_query_retrieval,
            "parent_document": rag_retriever.parent_document_retrieval,
            "contextual_compression": rag_retriever.contextual_compression_retrieval
        }
        
        # Validate retriever type
        if request.retriever_type not in retriever_methods:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid retriever_type. Must be one of: {list(retriever_methods.keys())}"
            )
        
        # Get the retrieval method
        retrieval_method = retriever_methods[request.retriever_type]
        # Retrieval embeds the query and hits Qdrant; multi_query also calls the LLM.
        # All of it is synchronous, so it belongs off the event loop too.
        docs = await run_in_threadpool(retrieval_method, query=request.question, k=5)
        
        if not docs:
            return {
                "answer": "I couldn't find any relevant information to answer your question. Please try rephrasing or ask about customer churn patterns, segments, or retention strategies.",
                "sources": [],
                "metrics": {
                    "response_time_ms": int((time.time() - start_time) * 1000),
                    "tokens_used": 0,
                    "retrieval_method": request.retriever_type,
                    "documents_found": 0
                }
            }
        
        # Prepare context from retrieved documents
        context = "\n\n".join([
            f"[Document {i+1}]\n{doc.page_content}"
            for i, doc in enumerate(docs)
        ])
        
        # Generate answer using LLM
        logger.info("🤖 Generating answer...")
        from langchain.prompts import ChatPromptTemplate
        
        llm = chat_model(temperature=0.7)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a customer churn analysis expert. Answer questions based on the provided context about customer churn data.
            
Be specific, cite data from the context, and provide actionable insights. If the context doesn't contain enough information, say so clearly.

Keep your answer concise but informative (max {max_length} characters)."""),
            ("user", """Context:
{context}

Question: {question}

Answer:""")
        ])
        
        chain = prompt | llm
        result = await run_in_threadpool(chain.invoke, {
            "context": context,
            "question": request.question,
            "max_length": request.max_response_length,
        })
        
        answer = result.content
        
        # Calculate metrics
        response_time = int((time.time() - start_time) * 1000)
        tokens_estimate = len(answer.split()) * 1.3  # Rough estimate
        
        logger.info(f"✅ Answer generated in {response_time}ms")
        
        return {
            "answer": answer,
            "sources": [
                {
                    "content": doc.page_content[:300] + ("..." if len(doc.page_content) > 300 else ""),
                    "metadata": doc.metadata,
                    "relevance_score": doc.metadata.get("score", 0.0) if hasattr(doc, "metadata") else 0.0
                }
                for doc in docs
            ],
            "metrics": {
                "response_time_ms": response_time,
                "tokens_used": int(tokens_estimate),
                "retrieval_method": request.retriever_type,
                "documents_found": len(docs)
            }
        }
        
    except Exception as e:
        logger.error(f"Error answering question: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Question answering failed: {str(e)}"
        )


# Multi-Agent Analysis endpoint
@app.post("/multi-agent-analyze", response_model=MultiAgentResponse)
async def multi_agent_analyze(request: MultiAgentRequest):
    """
    Comprehensive churn analysis using multi-agent system
    
    Uses two specialized agent teams:
    - Team 1 (Research Team): Gathers background context using RAG and Tavily search
    - Team 2 (Writing Team): Generates detailed response with 5 sub-agents
      (Writer, Editor, Note Taker, Empathy Editor, Style Guide)
    
    This endpoint provides the most comprehensive analysis with:
    - High-level background context
    - Specific use cases
    - Empathetic, well-cited responses
    - Style compliance
    """
    logger.info(f"Multi-agent analysis request: {request.query[:50]}...")
    
    # Check if multi-agent system is initialized
    multi_agent_system = require_ai("multi_agent_system")
    
    start_time = time.time()
    
    try:
        # Run multi-agent analysis
        logger.info("🤖 Running multi-agent analysis...")
        result = await run_in_threadpool(multi_agent_system.analyze, query=request.query)
        
        # Calculate metrics
        response_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ Multi-agent analysis completed in {response_time}ms")
        
        # Build response
        response_data = {
            "query": result.get("query", request.query),
            "query_type": result.get("query_type"),
            "response": result.get("response", ""),
            "background_context": result.get("background_context", "") if request.include_background else None,
            "key_insights": result.get("key_insights", []),
            "citations": result.get("citations", []) if request.include_citations else [],
            "style_notes": result.get("style_notes", []),
            "confidence_score": result.get("confidence_score", 0.0),
            "processing_stages": result.get("processing_stages", []),
            "total_sources": result.get("total_sources", 0),
            "errors": result.get("errors", [])
        }
        
        return MultiAgentResponse(**response_data)
        
    except Exception as e:
        logger.error(f"Error in multi-agent analysis: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Multi-agent analysis failed: {str(e)}"
        )


# Root endpoint
@app.get("/evaluation-results")
async def get_evaluation_results():
    """
    Get RAGAS evaluation results for all retrieval methods
    
    Returns comparison metrics for 5 retrieval strategies
    """
    import pandas as pd
    from pathlib import Path
    
    try:
        # Load evaluation results
        metrics_path = Path("metrics/ragas_evaluation_results.csv")
        
        if not metrics_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Evaluation results not found. Run evaluation first."
            )
        
        df = pd.read_csv(metrics_path)
        
        # Convert to list of dictionaries with formatted values
        # Replace NaN values with 0.0 to avoid JSON serialization errors
        df = df.fillna(0.0)
        
        results = []
        for _, row in df.iterrows():
            results.append({
                "method": row["Method"].replace("_", " ").title(),
                "faithfulness": round(float(row["faithfulness"]) * 100, 1),
                "answer_relevancy": round(float(row["answer_relevancy"]) * 100, 1),
                "context_recall": round(float(row["context_recall"]) * 100, 1),
                "context_precision": round(float(row["context_precision"]) * 100, 1),
                "answer_correctness": round(float(row["answer_correctness"]) * 100, 1),
                "semantic_similarity": round(float(row["semantic_similarity"]) * 100, 1)
            })
        
        return {
            "results": results,
            "metrics_info": {
                "faithfulness": "Answer grounded in retrieved context (0-100%)",
                "answer_relevancy": "Relevance to the question (0-100%)",
                "context_recall": "Retrieved all relevant information (0-100%)",
                "context_precision": "Only relevant contexts retrieved (0-100%)",
                "answer_correctness": "Factual accuracy (0-100%)",
                "semantic_similarity": "Semantic match quality (0-100%)"
            },
            "note": "Based on RAGAS evaluation with 54 test questions"
        }
        
    except Exception as e:
        logger.error(f"Error loading evaluation results: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load evaluation results: {str(e)}"
        )


# Customer Health Scoring Endpoints
@app.get("/at-risk-customers")
async def get_at_risk_customers(
    risk_threshold: float = 60.0,
    limit: int = 10
):
    """
    Get list of at-risk customers based on health scoring

    Returns customers with risk scores above the threshold,
    sorted by risk level (highest first)
    """
    health_scorer = require_health_scorer()

    try:
        customers = health_scorer.get_at_risk_customers(
            risk_threshold=risk_threshold,
            limit=limit
        )

        return {
            "at_risk_customers": customers,
            "total_count": len(customers),
            "risk_threshold": risk_threshold
        }

    except Exception as e:
        logger.error(f"Error getting at-risk customers: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get at-risk customers: {str(e)}"
        )


@app.get("/dashboard-stats")
async def get_dashboard_stats():
    """
    Get aggregated statistics for the dashboard

    Returns:
    - Total at-risk customers
    - Critical risk count
    - Total ARR at risk
    - Average days to churn
    - Prediction accuracy
    """
    health_scorer = require_health_scorer()

    try:
        stats = health_scorer.get_dashboard_stats()
        return stats

    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get dashboard stats: {str(e)}"
        )


class CustomerHealthRequest(BaseModel):
    """Request model for customer health calculation"""
    customer_id: Optional[str] = None
    segment: str = Field(..., description="Customer segment (SMB, Commercial, Enterprise)")
    tenure_years: float = Field(..., description="Years as customer", ge=0)
    arr: float = Field(..., description="Annual recurring revenue", ge=0)
    engagement_score: float = Field(0.5, description="Engagement score 0-1", ge=0, le=1)
    support_tickets_30d: int = Field(0, description="Support tickets in last 30 days", ge=0)


@app.post("/calculate-health")
async def calculate_customer_health(request: CustomerHealthRequest):
    """
    Calculate health/risk score for a specific customer

    Returns risk score, risk level, risk factors, and confidence
    """
    health_scorer = require_health_scorer()

    try:
        customer_data = {
            'segment': request.segment,
            'tenure_years': request.tenure_years,
            'arr': request.arr,
            'engagement_score': request.engagement_score,
            'support_tickets_30d': request.support_tickets_30d
        }

        result = health_scorer.calculate_customer_health(customer_data)

        return {
            "customer_id": request.customer_id,
            **result
        }

    except Exception as e:
        logger.error(f"Error calculating customer health: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to calculate customer health: {str(e)}"
        )


@app.get("/customer/{customer_id}/evidence")
async def get_customer_evidence(customer_id: int):
    """What in this account's record supports its risk score.

    Scoped to the customer by key, not by similarity, so another account's story
    cannot be returned. Retrieval explains the prediction here; it does not produce
    one -- see docs/ARCHITECTURE.md.
    """
    health_scorer = require_health_scorer()

    if state.evidence is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "evidence is not available",
                    "reason": state.errors.get("evidence", "not initialized")},
        )

    customer = health_scorer.get_customer(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    found = state.evidence.for_customer(customer["customer_id"], customer["risk_reason"])

    return {
        "customer_id": customer["customer_id"],
        "name": customer["name"],
        "risk_reason": customer["risk_reason"],
        "evidence": [e.to_dict() for e in found],
        "basis": "Passages from this account's own record, ranked against its risk driver",
    }


@app.get("/customer/{customer_id}/likelihood")
async def get_customer_likelihood(customer_id: int):
    """How likely this account is to churn within a quarter.

    Reported as a band and a lift against the book average, not a date and not a
    bare percentage. The model ranks well and underpredicts the absolute level by
    roughly two, because the hazard rate rises across the observation window. A
    lift is invariant to that -- both sides shift together -- so it survives a bias
    a printed percentage would inherit. See ADR-0009.
    """
    health_scorer = require_health_scorer()

    if state.survival is None or state.survival_frame is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "survival_model is not available",
                    "reason": state.errors.get("survival_model", "not initialized")},
        )

    customer = health_scorer.get_customer(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    frame = state.survival_frame
    row = frame[frame["customer_id"] == customer["customer_id"]]
    if row.empty:
        raise HTTPException(status_code=404, detail="No feature row for this customer")

    ranked = await run_in_threadpool(state.survival.rank_book, frame, 13)
    mine = ranked.loc[row.index[0]]

    return {
        "customer_id": customer["customer_id"],
        "name": customer["name"],
        "horizon_weeks": 13,
        "band": mine["band"],
        "lift": round(float(mine["lift"]), 2),
        "probability": round(float(mine["probability"]), 4),
        "as_of": str(row.iloc[0]["week_start"])[:10],
        "caveat": (
            "Lift against the book average over one quarter. The absolute "
            "probability underpredicts by roughly 2x; the band and lift are the "
            "figures to act on."
        ),
    }


@app.get("/customer/{customer_id}/plays")
async def get_customer_plays(customer_id: int):
    """What has worked on comparable accounts.

    Solutions are ranked by how much evidence supports them, not by how large the
    recorded effect was -- a big gain from two cases is weaker guidance than a
    modest one from twelve. Every play carries its case count and measured outcome
    so the recommendation can be argued with rather than taken on trust.
    """
    health_scorer = require_health_scorer()

    if state.playbook is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "playbook is not available",
                    "reason": state.errors.get("playbook", "not initialized")},
        )

    customer = health_scorer.get_customer(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    plays = state.playbook.plays_for(customer["risk_reason"], customer["segment"])

    return {
        "customer_id": customer["customer_id"],
        "name": customer["name"],
        "risk_reason": customer["risk_reason"],
        "segment": customer["segment"],
        "plays": [p.to_dict() for p in plays],
        "basis": "Outcomes recorded on accounts that faced the same challenge",
    }


@app.get("/book/exposure")
async def get_book_exposure(horizon_weeks: int = 13, top_n: int = 10):
    """What the risk in the book is worth this quarter.

    Expected loss is P(churn within the horizon) x ARR, summed. That is an
    expectation over a book, not a forecast for any one account -- each account
    either renews or does not.

    Two figures carry more weight than the headline total. The **share of loss by
    band** is a ratio, so it is unaffected by the calibration bias the dollar
    totals inherit. And **recoverable** is the model's response to adoption moving
    by the median gain recorded on comparable accounts: a sensitivity, biased
    upward because only successful interventions were ever written down.
    """
    health_scorer = require_health_scorer()

    if state.exposure is None or state.survival_frame is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "exposure is not available",
                    "reason": state.errors.get("survival_model", "not initialized")},
        )

    book = await run_in_threadpool(
        state.exposure.for_book,
        state.survival_frame,
        health_scorer.score_active_customers(),
        horizon_weeks,
        top_n,
    )
    return book.to_dict()


@app.get("/customer/{customer_id}/exposure")
async def get_customer_exposure(customer_id: int, horizon_weeks: int = 13):
    """One account's ARR weighted by its probability of leaving this quarter."""
    health_scorer = require_health_scorer()

    if state.exposure is None or state.survival_frame is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "exposure is not available",
                    "reason": state.errors.get("survival_model", "not initialized")},
        )

    customer = health_scorer.get_customer(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    one = await run_in_threadpool(
        state.exposure.for_customer,
        state.survival_frame,
        health_scorer.score_active_customers(),
        customer["customer_id"],
        horizon_weeks,
    )
    if one is None:
        raise HTTPException(status_code=404, detail="No feature row for this customer")

    return {
        **one.to_dict(),
        "horizon_weeks": horizon_weeks,
        "caveat": (
            "Expected loss is a probability times ARR. Over a book that is a "
            "meaningful total; for one account it is neither a forecast nor a "
            "figure to quote to that customer."
        ),
    }


@app.get("/customer/{customer_id}/detailed-analysis")
async def get_customer_detailed_analysis(customer_id: int):
    """Detailed analysis for one customer, built from observed data.

    Every series here -- engagement history, tickets, interactions -- comes from the
    dataset. The previous implementation generated them with unseeded random calls on
    each request, so the same customer rendered different charts on every refresh.
    """
    health_scorer = require_health_scorer()

    try:
        detail = health_scorer.get_customer_detail(customer_id)
        if not detail:
            raise HTTPException(status_code=404, detail="Customer not found")
        return detail

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting customer detailed analysis: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate detailed analysis: {str(e)}"
        )

@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "service": "Customer Churn RAG API",
        "version": "0.3.0",
        "status": "running",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "analyze_churn": "/analyze-churn - Single agent analysis",
            "ask": "/ask - General Q&A with RAG",
            "multi_agent": "/multi-agent-analyze - Comprehensive multi-agent analysis",
            "evaluation": "/evaluation-results - RAGAS evaluation metrics",
            "at_risk_customers": "/at-risk-customers - Get at-risk customer list",
            "dashboard_stats": "/dashboard-stats - Dashboard statistics",
            "calculate_health": "/calculate-health - Calculate customer health score",
            "customer_analysis": "/customer/{customer_id}/detailed-analysis - Get detailed customer analysis"
        },
        "features": [
            "Multi-Agent System (Research Team + Writing Team)",
            "LangGraph Agents",
            "RAG with Multiple Retrieval Strategies",
            "Knowledge Graph Integration",
            "Tavily Search",
            "Empathetic Response Generation",
            "RAGAS Evaluation Metrics",
            "Customer Health Scoring",
            "Predictive Churn Risk Analysis"
        ]
    }


if __name__ == "__main__":
    # Get configuration from environment
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8000"))
    
    logger.info(f"Starting Customer Churn RAG API on {host}:{port}")
    
    # Run the server
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=os.getenv("LOG_LEVEL", "info").lower()
    )

