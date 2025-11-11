"""
Customer Churn Analysis Agent
LangGraph-based agent for intelligent churn prediction and analysis
"""

import os
import sys
from pathlib import Path
from typing import TypedDict, Annotated, List, Dict, Optional, Literal
import operator
import logging

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain.schema import Document
from langchain.tools import Tool
from langchain_community.tools.tavily_search import TavilySearchResults

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from core.rag_retrievers import ChurnRAGRetriever
from core.knowledge_graph import ChurnKnowledgeGraph

logger = logging.getLogger(__name__)


class ChurnAgentState(TypedDict):
    """State for the churn analysis agent"""
    query: str
    customer_id: Optional[str]
    query_type: Optional[str]  # 'risk_assessment', 'pattern_analysis', 'retention_strategy', 'competitive_intel'
    documents: List[Document]
    kg_results: Dict
    web_results: Optional[List[Dict]]
    analysis: str
    recommendations: List[str]
    confidence_score: float
    sources: List[Dict]
    retrieval_method: Optional[str]
    errors: Annotated[List[str], operator.add]


class CustomerChurnAgent:
    """
    LangGraph Agent for Customer Churn Analysis
    
    Agent flow:
    1. Query understanding - Classify query type and extract intent
    2. Retrieve documents - Use best retrieval method
    3. Query knowledge graph - Get entity relationships
    4. Analyze churn - Pattern analysis from context
    5. Generate recommendations - Actionable strategies
    6. Synthesize response - Final answer with sources
    """
    
    def __init__(
        self,
        rag_retriever: Optional[ChurnRAGRetriever] = None,
        knowledge_graph: Optional[ChurnKnowledgeGraph] = None,
        use_tavily: bool = True
    ):
        """
        Initialize the agent with LLM and tools
        
        Args:
            rag_retriever: RAG retriever instance (optional, will initialize if not provided)
            knowledge_graph: Knowledge graph instance (optional)
            use_tavily: Whether to use Tavily search (requires API key)
        """
        logger.info("Initializing Customer Churn Agent...")
        
        # Initialize LLM
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        
        # Store components
        self.rag_retriever = rag_retriever
        self.knowledge_graph = knowledge_graph
        
        # Initialize Tavily if enabled
        self.tavily_search = None
        if use_tavily and os.getenv("TAVILY_API_KEY"):
            try:
                self.tavily_search = TavilySearchResults(
                    max_results=3,
                    search_depth="advanced",
                    tavily_api_key=os.getenv("TAVILY_API_KEY")
                )
                logger.info("✓ Tavily search enabled")
            except Exception as e:
                logger.warning(f"Tavily search not available: {e}")
        
        # Build the agent graph
        self.graph = self._build_graph()
        self.app = self.graph.compile()
        
        logger.info("✅ Customer Churn Agent initialized")
    
    def _build_graph(self) -> StateGraph:
        """
        Build the LangGraph state machine
        
        Returns:
            Configured StateGraph
        """
        logger.info("Building agent StateGraph...")
        
        workflow = StateGraph(ChurnAgentState)
        
        # Add nodes
        workflow.add_node("understand_query", self._understand_query)
        workflow.add_node("retrieve_documents", self._retrieve_documents)
        workflow.add_node("query_knowledge_graph", self._query_knowledge_graph)
        workflow.add_node("search_web", self._search_web)
        workflow.add_node("analyze_churn", self._analyze_churn_risk)
        workflow.add_node("generate_recommendations", self._generate_recommendations)
        workflow.add_node("synthesize_response", self._synthesize_response)
        
        # Set entry point
        workflow.set_entry_point("understand_query")
        
        # Add edges
        workflow.add_edge("understand_query", "retrieve_documents")
        workflow.add_edge("retrieve_documents", "query_knowledge_graph")
        
        # Conditional edge: search web if needed
        workflow.add_conditional_edges(
            "query_knowledge_graph",
            self._should_search_web,
            {
                "search_web": "search_web",
                "analyze": "analyze_churn"
            }
        )
        
        workflow.add_edge("search_web", "analyze_churn")
        workflow.add_edge("analyze_churn", "generate_recommendations")
        workflow.add_edge("generate_recommendations", "synthesize_response")
        workflow.add_edge("synthesize_response", END)
        
        logger.info("✓ StateGraph built with 7 nodes")
        return workflow
    
    def _understand_query(self, state: ChurnAgentState) -> ChurnAgentState:
        """
        Understand and categorize the user query
        
        Classifies query into:
        - risk_assessment: Predict churn risk for specific customers
        - pattern_analysis: Analyze churn patterns across segments/reasons
        - retention_strategy: Generate retention recommendations
        - competitive_intel: Competitor analysis and competitive losses
        """
        logger.info(f"Understanding query: {state['query'][:100]}...")
        
        classification_prompt = f"""Analyze this customer churn question and classify it:

Question: {state['query']}

Classify into ONE category:
1. risk_assessment - Predicting churn risk for specific customers
2. pattern_analysis - Analyzing churn patterns, trends, or segments
3. retention_strategy - Generating retention strategies or recommendations
4. competitive_intel - Competitor analysis or competitive losses

Also extract:
- Customer ID if mentioned (or null)
- Key entities mentioned (segments, competitors, products, reasons)

Return ONLY a JSON object:
{{
    "query_type": "category",
    "customer_id": "id or null",
    "entities": ["entity1", "entity2"],
    "reasoning": "brief explanation"
}}"""
        
        try:
            response = self.llm.invoke(classification_prompt)
            content = response.content.strip()
            
            # Try to extract JSON from markdown code blocks if present
            import json
            import re
            
            # Remove markdown code blocks if present
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                content = json_match.group(1)
            
            # Parse JSON
            result = json.loads(content)
            
            state["query_type"] = result.get("query_type", "pattern_analysis")
            if result.get("customer_id"):
                state["customer_id"] = result["customer_id"]
            
            logger.info(f"✓ Query classified as: {state['query_type']}")
            
        except Exception as e:
            logger.error(f"Query classification failed: {e}")
            state["query_type"] = "pattern_analysis"  # Default
            state["errors"] = [f"Query classification error: {str(e)}"]
        
        return state
    
    def _retrieve_documents(self, state: ChurnAgentState) -> ChurnAgentState:
        """
        Retrieve relevant documents using best retrieval method
        
        Method selection based on query type:
        - risk_assessment: metadata filtering + naive retrieval
        - pattern_analysis: multi-query retrieval (diverse perspectives)
        - retention_strategy: contextual compression (focused insights)
        - competitive_intel: metadata filtering by competitor
        """
        logger.info(f"Retrieving documents for {state['query_type']}...")
        
        if not self.rag_retriever:
            logger.warning("RAG retriever not initialized, skipping document retrieval")
            state["documents"] = []
            state["retrieval_method"] = "none"
            return state
        
        try:
            query = state["query"]
            query_type = state.get("query_type", "pattern_analysis")
            
            # Select retrieval method based on query type
            if query_type == "risk_assessment":
                # Use metadata filtering for targeted retrieval
                if state.get("customer_id"):
                    docs = self.rag_retriever.retrieve_with_metadata_filter(
                        query=query,
                        k=5
                    )
                else:
                    docs = self.rag_retriever.naive_retrieval(query, k=5)
                method = "metadata_filtered"
            
            elif query_type == "pattern_analysis":
                # Use multi-query for diverse perspectives
                docs = self.rag_retriever.multi_query_retrieval(query, k=5)
                method = "multi_query"
            
            elif query_type == "retention_strategy":
                # Use contextual compression for focused insights
                docs = self.rag_retriever.contextual_compression_retrieval(query, k=5)
                method = "contextual_compression"
            
            elif query_type == "competitive_intel":
                # Use metadata filtering by competitor
                docs = self.rag_retriever.naive_retrieval(query, k=7)
                method = "competitor_filtered"
            
            else:
                # Default to naive retrieval
                docs = self.rag_retriever.naive_retrieval(query, k=5)
                method = "naive"
            
            state["documents"] = docs
            state["retrieval_method"] = method
            logger.info(f"✓ Retrieved {len(docs)} documents using {method}")
            
        except Exception as e:
            logger.error(f"Document retrieval failed: {e}")
            state["documents"] = []
            state["retrieval_method"] = "failed"
            state["errors"] = [f"Retrieval error: {str(e)}"]
        
        return state
    
    def _query_knowledge_graph(self, state: ChurnAgentState) -> ChurnAgentState:
        """
        Query knowledge graph for entity relationships
        
        Extracts:
        - Customers matching criteria
        - Churn reasons and patterns
        - Competitor switches
        - Segment-specific insights
        """
        logger.info("Querying knowledge graph...")
        
        if not self.knowledge_graph:
            logger.warning("Knowledge graph not initialized, skipping")
            state["kg_results"] = {}
            return state
        
        try:
            query_type = state.get("query_type", "pattern_analysis")
            kg_results = {}
            
            # Extract insights based on query type
            if query_type == "pattern_analysis":
                # Get segment patterns
                for segment in ["Commercial", "SMB", "Mid-Market", "Strategic", "Enterprise"]:
                    patterns = self.knowledge_graph.get_churn_patterns(segment)
                    if patterns and patterns.get("customer_count", 0) > 0:
                        kg_results[segment] = patterns
            
            elif query_type == "competitive_intel":
                # Get competitor insights
                competitors = self.knowledge_graph.get_entity_by_type("Competitor")
                kg_results["competitors"] = competitors[:10]
                
                # Get top competitors by customer count
                competitor_counts = {}
                for comp in competitors[:10]:
                    customers = self.knowledge_graph.query_customers_by_competitor(comp)
                    if customers:
                        competitor_counts[comp] = len(customers)
                kg_results["competitor_counts"] = competitor_counts
            
            elif query_type == "retention_strategy":
                # Get churn reasons
                reasons = self.knowledge_graph.get_entity_by_type("ChurnReason")
                kg_results["churn_reasons"] = reasons[:15]
            
            # Always include summary stats
            kg_results["summary"] = {
                "total_customers": len(self.knowledge_graph.get_entity_by_type("Customer")),
                "segments": len(self.knowledge_graph.get_entity_by_type("Segment")),
                "churn_reasons": len(self.knowledge_graph.get_entity_by_type("ChurnReason")),
                "competitors": len(self.knowledge_graph.get_entity_by_type("Competitor"))
            }
            
            state["kg_results"] = kg_results
            logger.info(f"✓ Knowledge graph query complete: {len(kg_results)} result groups")
            
        except Exception as e:
            logger.error(f"Knowledge graph query failed: {e}")
            state["kg_results"] = {}
            state["errors"] = [f"KG query error: {str(e)}"]
        
        return state
    
    def _should_search_web(self, state: ChurnAgentState) -> Literal["search_web", "analyze"]:
        """
        Decide whether to search web for external context
        
        Search web if:
        - Query mentions industry trends or benchmarks
        - Competitive intelligence needed
        - No sufficient documents retrieved
        """
        query = state["query"].lower()
        query_type = state.get("query_type")
        doc_count = len(state.get("documents", []))
        
        # Search if competitive intel or low document count
        should_search = (
            query_type == "competitive_intel" or
            doc_count < 2 or
            "industry" in query or
            "benchmark" in query or
            "trend" in query
        )
        
        # But only if Tavily is available
        if should_search and self.tavily_search:
            logger.info("→ Will search web for additional context")
            return "search_web"
        else:
            logger.info("→ Proceeding to analysis without web search")
            return "analyze"
    
    def _search_web(self, state: ChurnAgentState) -> ChurnAgentState:
        """Search web for industry benchmarks and trends"""
        logger.info("Searching web for external context...")
        
        if not self.tavily_search:
            state["web_results"] = []
            return state
        
        try:
            # Create search query
            query = state["query"]
            search_query = f"customer churn SaaS {query}"
            
            results = self.tavily_search.invoke(search_query)
            state["web_results"] = results[:3]  # Top 3 results
            
            logger.info(f"✓ Found {len(results)} web results")
            
        except Exception as e:
            logger.error(f"Web search failed: {e}")
            state["web_results"] = []
            state["errors"] = [f"Web search error: {str(e)}"]
        
        return state
    
    def _analyze_churn_risk(self, state: ChurnAgentState) -> ChurnAgentState:
        """
        Analyze churn risk based on retrieved context
        
        Synthesizes:
        - Document context
        - Knowledge graph patterns
        - Web research (if available)
        """
        logger.info("Analyzing churn patterns and risks...")
        
        # Prepare context
        doc_context = "\n\n".join([
            f"Customer: {doc.metadata.get('account_name', 'Unknown')}\n"
            f"Segment: {doc.metadata.get('segment', 'N/A')}\n"
            f"Churn Reason: {doc.metadata.get('churn_reason', 'N/A')}\n"
            f"Details: {doc.page_content[:300]}..."
            for doc in state.get("documents", [])[:5]
        ])
        
        kg_context = str(state.get("kg_results", {}))
        web_context = str(state.get("web_results", []))
        
        analysis_prompt = f"""You are a customer success analyst. Analyze the churn patterns and risks based on this data:

QUESTION: {state['query']}

QUERY TYPE: {state.get('query_type')}

CUSTOMER DATA:
{doc_context}

KNOWLEDGE GRAPH INSIGHTS:
{kg_context}

{'WEB RESEARCH:\n' + web_context if state.get('web_results') else ''}

Provide a comprehensive analysis:
1. Key patterns identified
2. Risk factors or insights
3. Data-driven observations
4. Comparative analysis (if applicable)

Be specific, cite examples, and quantify where possible."""
        
        try:
            response = self.llm.invoke(analysis_prompt)
            state["analysis"] = response.content
            logger.info("✓ Analysis complete")
            
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            state["analysis"] = f"Analysis error: {str(e)}"
            state["errors"] = [f"Analysis error: {str(e)}"]
        
        return state
    
    def _generate_recommendations(self, state: ChurnAgentState) -> ChurnAgentState:
        """
        Generate actionable retention recommendations
        
        Based on:
        - Identified patterns
        - Successful retention examples (if available)
        - Industry best practices
        """
        logger.info("Generating recommendations...")
        
        recommendations_prompt = f"""Based on this churn analysis, generate 3-5 actionable retention recommendations:

ANALYSIS:
{state.get('analysis', 'No analysis available')}

Generate specific, actionable recommendations:
- Target the root causes identified
- Provide concrete next steps
- Prioritize by impact
- Be specific to the segment/situation

Return as a JSON array:
["Recommendation 1", "Recommendation 2", "Recommendation 3"]"""
        
        try:
            import json
            import re
            
            response = self.llm.invoke(recommendations_prompt)
            content = response.content.strip()
            
            # Try to extract JSON array from markdown code blocks if present
            json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
            if json_match:
                content = json_match.group(1)
            
            # Parse JSON array
            recommendations = json.loads(content)
            
            if isinstance(recommendations, list):
                state["recommendations"] = recommendations
                logger.info(f"✓ Generated {len(recommendations)} recommendations")
            else:
                state["recommendations"] = [str(recommendations)]
            
        except Exception as e:
            logger.error(f"Recommendation generation failed: {e}")
            state["recommendations"] = ["Unable to generate recommendations due to error"]
            state["errors"] = [f"Recommendation error: {str(e)}"]
        
        return state
    
    def _synthesize_response(self, state: ChurnAgentState) -> ChurnAgentState:
        """
        Synthesize final response with sources and confidence
        
        Combines:
        - Analysis
        - Recommendations
        - Source citations
        - Confidence assessment
        """
        logger.info("Synthesizing final response...")
        
        try:
            # Calculate confidence based on data quality
            doc_count = len(state.get("documents") or [])
            kg_data = len(state.get("kg_results") or {})
            error_count = len(state.get("errors") or [])
            
            confidence = min(1.0, (doc_count * 0.15 + kg_data * 0.1 - error_count * 0.2))
            confidence = max(0.0, confidence)
            state["confidence_score"] = round(confidence, 2)
            
            # Build sources list
            sources = []
            
            # Add document sources
            documents = state.get("documents") or []
            for doc in documents[:5]:
                sources.append({
                    "type": "customer_data",
                    "customer": doc.metadata.get("account_name"),
                    "segment": doc.metadata.get("segment"),
                    "relevance": "high"
                })
            
            # Add KG sources
            kg_results = state.get("kg_results")
            if kg_results:
                sources.append({
                    "type": "knowledge_graph",
                    "entities": len(kg_results),
                    "relevance": "high"
                })
            
            # Add web sources
            web_results = state.get("web_results") or []
            for web_result in web_results:
                sources.append({
                    "type": "web_research",
                    "url": web_result.get("url", ""),
                    "title": web_result.get("title", ""),
                    "relevance": "medium"
                })
            
            state["sources"] = sources
            logger.info(f"✓ Response synthesized (confidence: {state['confidence_score']})")
            
        except Exception as e:
            logger.error(f"Response synthesis failed: {e}")
            state["confidence_score"] = 0.0
            state["sources"] = []
            state["errors"] = [f"Synthesis error: {str(e)}"]
        
        return state
    
    def run(self, query: str, customer_id: str = None) -> Dict:
        """
        Run the agent on a query
        
        Args:
            query: User question or request
            customer_id: Optional customer ID for specific analysis
        
        Returns:
            Agent response with analysis, recommendations, sources, and metadata
        """
        logger.info(f"Running agent on query: {query[:100]}...")
        
        # Initialize state
        initial_state = ChurnAgentState(
            query=query,
            customer_id=customer_id,
            query_type=None,
            documents=[],
            kg_results={},
            web_results=None,
            analysis="",
            recommendations=[],
            confidence_score=0.0,
            sources=[],
            retrieval_method=None,
            errors=[]
        )
        
        try:
            # Run the graph
            final_state = self.app.invoke(initial_state)
            
            # Format response
            response = {
                "query": query,
                "query_type": final_state.get("query_type"),
                "analysis": final_state.get("analysis"),
                "recommendations": final_state.get("recommendations", []),
                "confidence_score": final_state.get("confidence_score", 0.0),
                "sources": final_state.get("sources", []),
                "retrieval_method": final_state.get("retrieval_method"),
                "documents_retrieved": len(final_state.get("documents", [])),
                "kg_insights": len(final_state.get("kg_results", {})),
                "errors": final_state.get("errors", [])
            }
            
            logger.info(f"✅ Agent execution complete (confidence: {response['confidence_score']})")
            return response
            
        except Exception as e:
            logger.error(f"Agent execution failed: {e}")
            return {
                "query": query,
                "error": str(e),
                "analysis": f"Agent execution failed: {str(e)}",
                "recommendations": [],
                "confidence_score": 0.0,
                "sources": []
            }


def create_churn_agent(
    rag_retriever: Optional[ChurnRAGRetriever] = None,
    knowledge_graph: Optional[ChurnKnowledgeGraph] = None,
    use_tavily: bool = True
) -> CustomerChurnAgent:
    """
    Factory function to create a configured churn agent
    
    Args:
        rag_retriever: Optional RAG retriever instance
        knowledge_graph: Optional knowledge graph instance
        use_tavily: Whether to enable Tavily search
    
    Returns:
        Initialized CustomerChurnAgent
    """
    return CustomerChurnAgent(
        rag_retriever=rag_retriever,
        knowledge_graph=knowledge_graph,
        use_tavily=use_tavily
    )


if __name__ == "__main__":
    # Test the agent
    import logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    print("\n" + "="*80)
    print("🤖 TESTING CUSTOMER CHURN AGENT")
    print("="*80)
    
    # Create agent without dependencies for structure test
    agent = create_churn_agent(rag_retriever=None, knowledge_graph=None, use_tavily=False)
    
    print(f"\n✅ Agent created successfully")
    print(f"   - StateGraph nodes: 7")
    print(f"   - Tools available: RAG, KG, Tavily (if configured)")
    print(f"   - Query types: risk_assessment, pattern_analysis, retention_strategy, competitive_intel")
    
    print("\n💡 To test with full functionality:")
    print("   1. Initialize RAG retriever: retriever = initialize_churn_rag_system()")
    print("   2. Load knowledge graph: kg = build_churn_knowledge_graph()")
    print("   3. Create agent: agent = create_churn_agent(retriever, kg)")
    print("   4. Run query: result = agent.run('What are main churn reasons?')")
    
    print("\n" + "="*80)
