"""
Customer Churn RAG - FastAPI Backend
Main API server with RAG endpoints for churn analysis
"""

import os
import sys
import logging
import time
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# Add src to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from core.rag_retrievers import ChurnRAGRetriever
from agents.churn_agent import CustomerChurnAgent
from agents.multi_agent_system import MultiAgentChurnSystem
from core.knowledge_graph import ChurnKnowledgeGraph
from core.health_scoring import CustomerHealthScorer

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Customer Churn RAG API",
    description="AI-powered customer churn prediction and analysis using RAG",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global RAG system components (initialized on startup)
rag_retriever: Optional[ChurnRAGRetriever] = None
churn_agent: Optional[CustomerChurnAgent] = None
multi_agent_system: Optional[MultiAgentChurnSystem] = None
health_scorer: Optional[CustomerHealthScorer] = None


@app.on_event("startup")
async def startup_event():
    """Initialize RAG system on startup"""
    global rag_retriever, churn_agent, multi_agent_system, health_scorer

    logger.info("🚀 Initializing RAG system...")
    
    try:
        # Check if OpenAI API key is set
        if not os.getenv("OPENAI_API_KEY"):
            logger.warning("⚠️  OPENAI_API_KEY not set - RAG system will not work")
            return
        
        # Initialize RAG retriever
        logger.info("📊 Loading RAG retriever...")
        rag_retriever = ChurnRAGRetriever(
            collection_name=os.getenv("COLLECTION_NAME", "customer_churn"),
            qdrant_url=os.getenv("QDRANT_URL", "http://qdrant:6333")
        )
        
        # Load and process documents
        data_folder = os.getenv("DATA_FOLDER", "data")
        num_docs = rag_retriever.load_and_process_documents(data_folder=data_folder)
        logger.info(f"✓ Loaded and indexed {num_docs} documents")
        
        # Initialize knowledge graph if available
        kg = None
        kg_path = Path("cache/churn_knowledge_graph.pkl")
        if kg_path.exists():
            try:
                kg = ChurnKnowledgeGraph.load(str(kg_path))
                logger.info("✓ Loaded knowledge graph from cache")
            except Exception as e:
                logger.warning(f"Could not load knowledge graph: {e}")
        
        # Initialize agent
        logger.info("🤖 Initializing churn agent...")
        churn_agent = CustomerChurnAgent(
            rag_retriever=rag_retriever,
            knowledge_graph=kg,
            use_tavily=bool(os.getenv("TAVILY_API_KEY"))
        )
        logger.info("✓ Churn agent initialized")
        
        # Initialize multi-agent system
        logger.info("🤖 Initializing multi-agent system...")
        multi_agent_system = MultiAgentChurnSystem(
            rag_retriever=rag_retriever,
            knowledge_graph=kg,
            use_tavily=bool(os.getenv("TAVILY_API_KEY"))
        )
        logger.info("✓ Multi-agent system initialized")

        # Initialize health scorer
        logger.info("💚 Initializing customer health scorer...")
        health_scorer = CustomerHealthScorer(
            churn_data_path="data/churned_customers_cleaned.csv"
        )
        logger.info("✓ Health scorer initialized")

        logger.info("✅ RAG system fully initialized and ready!")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize RAG system: {e}", exc_info=True)
        logger.warning("⚠️  API will run but RAG endpoints will return errors")


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
        "parent_document", 
        description="Retrieval method: 'naive', 'multi_query', 'parent_document', 'contextual_compression'"
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
    """Health check endpoint for Docker health checks"""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now().isoformat(),
        service="customer-churn-rag-api"
    )


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
    if not churn_agent:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Churn agent not initialized. Please ensure OPENAI_API_KEY is set and Qdrant is running."
        )
    
    start_time = time.time()
    
    try:
        # Run agent analysis
        logger.info("🤖 Running agent analysis...")
        result = churn_agent.run(
            query=request.query,
            customer_id=request.customer_id
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
    if not rag_retriever:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG system not initialized. Please ensure OPENAI_API_KEY is set and Qdrant is running."
        )
    
    start_time = time.time()
    
    try:
        # Select retrieval method (defaults to parent_document - best performer from RAGAS evaluation)
        logger.info(f"🔍 Retrieving relevant documents using: {request.retriever_type}")
        
        # Map retriever type to method
        retriever_methods = {
            "naive": rag_retriever.naive_retrieval,
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
        docs = retrieval_method(query=request.question, k=5)
        
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
        from langchain_openai import ChatOpenAI
        from langchain.prompts import ChatPromptTemplate
        
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
        
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
        result = chain.invoke({
            "context": context,
            "question": request.question,
            "max_length": request.max_response_length
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
    if not multi_agent_system:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Multi-agent system not initialized. Please ensure OPENAI_API_KEY is set and Qdrant is running."
        )
    
    start_time = time.time()
    
    try:
        # Run multi-agent analysis
        logger.info("🤖 Running multi-agent analysis...")
        result = multi_agent_system.analyze(query=request.query)
        
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
    if not health_scorer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Health scorer not initialized"
        )

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
    if not health_scorer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Health scorer not initialized"
        )

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
    if not health_scorer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Health scorer not initialized"
        )

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
            "calculate_health": "/calculate-health - Calculate customer health score"
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

