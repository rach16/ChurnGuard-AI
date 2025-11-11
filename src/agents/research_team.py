"""
Research Team Agent (Team 1)
Responsible for High-Level Background Context using RAG and Tavily Search
"""

import os
import logging
from typing import TypedDict, List, Dict, Optional, Annotated
import operator

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain.schema import Document
from langchain_community.tools.tavily_search import TavilySearchResults

from core.rag_retrievers import ChurnRAGRetriever
from core.knowledge_graph import ChurnKnowledgeGraph

logger = logging.getLogger(__name__)


class ResearchTeamState(TypedDict):
    """State for Research Team"""
    query: str
    background_context: str
    rag_documents: List[Document]
    web_research: List[Dict]
    key_insights: List[str]
    sources: List[Dict]
    errors: Annotated[List[str], operator.add]


class ResearchTeam:
    """
    Research Team Agent
    
    Provides high-level background context using:
    1. RAG Tool: Company-specific knowledge base of rules, policies, and regulations
    2. Tavily Search: Most relevant and up-to-date information available
    
    This team establishes the foundational context for deeper analysis.
    """
    
    def __init__(
        self,
        rag_retriever: Optional[ChurnRAGRetriever] = None,
        knowledge_graph: Optional[ChurnKnowledgeGraph] = None,
        use_tavily: bool = True
    ):
        """
        Initialize Research Team
        
        Args:
            rag_retriever: RAG retriever for company knowledge base
            knowledge_graph: Knowledge graph for entity relationships
            use_tavily: Enable Tavily search for external research
        """
        logger.info("🔬 Initializing Research Team Agent...")
        
        # Initialize LLM
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,  # Lower temperature for factual research
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        
        # Store tools
        self.rag_retriever = rag_retriever
        self.knowledge_graph = knowledge_graph
        
        # Initialize Tavily Search
        self.tavily_search = None
        if use_tavily and os.getenv("TAVILY_API_KEY"):
            try:
                self.tavily_search = TavilySearchResults(
                    max_results=5,
                    search_depth="advanced",
                    include_answer=True,
                    tavily_api_key=os.getenv("TAVILY_API_KEY")
                )
                logger.info("✓ Tavily Search enabled for external research")
            except Exception as e:
                logger.warning(f"Tavily search not available: {e}")
        
        # Build workflow
        self.graph = self._build_graph()
        self.app = self.graph.compile()
        
        logger.info("✅ Research Team initialized")
    
    def _build_graph(self) -> StateGraph:
        """Build Research Team workflow"""
        logger.info("Building Research Team workflow...")
        
        workflow = StateGraph(ResearchTeamState)
        
        # Add nodes
        workflow.add_node("gather_internal_knowledge", self._gather_internal_knowledge)
        workflow.add_node("search_external_sources", self._search_external_sources)
        workflow.add_node("synthesize_background", self._synthesize_background)
        
        # Set entry point
        workflow.set_entry_point("gather_internal_knowledge")
        
        # Add edges
        workflow.add_edge("gather_internal_knowledge", "search_external_sources")
        workflow.add_edge("search_external_sources", "synthesize_background")
        workflow.add_edge("synthesize_background", END)
        
        logger.info("✓ Research Team workflow built")
        return workflow
    
    def _gather_internal_knowledge(self, state: ResearchTeamState) -> ResearchTeamState:
        """
        Gather internal knowledge from RAG system
        
        Uses company-specific knowledge base for:
        - Historical churn patterns
        - Policy information
        - Internal regulations and guidelines
        """
        logger.info("📚 Gathering internal knowledge from RAG system...")
        
        if not self.rag_retriever:
            logger.warning("RAG retriever not initialized")
            state["rag_documents"] = []
            return state
        
        try:
            query = state["query"]
            
            # Use multi-query retrieval for comprehensive coverage
            docs = self.rag_retriever.multi_query_retrieval(query, k=10)
            
            state["rag_documents"] = docs
            logger.info(f"✓ Retrieved {len(docs)} internal documents")
            
            # Extract key insights from knowledge graph if available
            if self.knowledge_graph:
                # Get segment patterns
                segments = ["Commercial", "SMB", "Mid-Market", "Strategic", "Enterprise"]
                insights = []
                
                for segment in segments:
                    patterns = self.knowledge_graph.get_churn_patterns(segment)
                    if patterns and patterns.get("customer_count", 0) > 0:
                        insights.append(
                            f"{segment}: {patterns['customer_count']} customers, "
                            f"top reason: {patterns.get('top_reasons', ['N/A'])[0] if patterns.get('top_reasons') else 'N/A'}"
                        )
                
                state["key_insights"] = insights
                logger.info(f"✓ Extracted {len(insights)} key insights from knowledge graph")
            
        except Exception as e:
            logger.error(f"Internal knowledge gathering failed: {e}")
            state["rag_documents"] = []
            state["errors"] = [f"RAG retrieval error: {str(e)}"]
        
        return state
    
    def _search_external_sources(self, state: ResearchTeamState) -> ResearchTeamState:
        """
        Search external sources using Tavily
        
        Gathers:
        - Industry benchmarks and trends
        - Best practices
        - Recent research and insights
        """
        logger.info("🌐 Searching external sources with Tavily...")
        
        if not self.tavily_search:
            logger.warning("Tavily search not available")
            state["web_research"] = []
            return state
        
        try:
            query = state["query"]
            
            # Create comprehensive search queries
            search_queries = [
                f"SaaS customer churn {query}",
                f"customer retention best practices {query}",
                f"churn analysis industry trends {query}"
            ]
            
            all_results = []
            for search_query in search_queries:
                try:
                    results = self.tavily_search.invoke(search_query)
                    all_results.extend(results[:2])  # Top 2 from each query
                except Exception as e:
                    logger.warning(f"Search query failed: {e}")
            
            state["web_research"] = all_results
            logger.info(f"✓ Found {len(all_results)} external sources")
            
        except Exception as e:
            logger.error(f"External search failed: {e}")
            state["web_research"] = []
            state["errors"] = [f"Tavily search error: {str(e)}"]
        
        return state
    
    def _synthesize_background(self, state: ResearchTeamState) -> ResearchTeamState:
        """
        Synthesize background context from all sources
        
        Creates comprehensive background context that combines:
        - Internal company knowledge
        - Knowledge graph insights
        - External research and benchmarks
        """
        logger.info("🔄 Synthesizing background context...")
        
        try:
            # Prepare context from internal documents
            internal_context = "\n\n".join([
                f"[{doc.metadata.get('account_name', 'Unknown')} - {doc.metadata.get('segment', 'N/A')}]\n"
                f"Churn Reason: {doc.metadata.get('churn_reason', 'N/A')}\n"
                f"Details: {doc.page_content[:400]}..."
                for doc in state.get("rag_documents", [])[:8]
            ])
            
            # Prepare insights
            insights_context = "\n".join([
                f"- {insight}"
                for insight in state.get("key_insights", [])
            ])
            
            # Prepare web research
            web_context = "\n\n".join([
                f"Source: {result.get('title', 'Unknown')}\n"
                f"URL: {result.get('url', '')}\n"
                f"Content: {result.get('content', '')[:300]}..."
                for result in state.get("web_research", [])
            ])
            
            # Synthesize background
            synthesis_prompt = f"""You are a research analyst synthesizing background context for customer churn analysis.

QUERY: {state['query']}

INTERNAL COMPANY DATA:
{internal_context}

KEY INSIGHTS FROM KNOWLEDGE GRAPH:
{insights_context}

EXTERNAL RESEARCH & INDUSTRY BENCHMARKS:
{web_context}

Synthesize a comprehensive background context that:
1. Provides high-level overview of the topic
2. Highlights key patterns from internal data
3. Contextualizes with industry benchmarks and trends
4. Identifies relevant policies, regulations, or best practices
5. Sets the foundation for deeper analysis

Write 3-4 paragraphs of well-structured background context."""
            
            response = self.llm.invoke(synthesis_prompt)
            state["background_context"] = response.content
            
            # Compile sources
            sources = []
            
            # Add document sources
            for doc in state.get("rag_documents", [])[:5]:
                sources.append({
                    "type": "internal_data",
                    "customer": doc.metadata.get("account_name"),
                    "segment": doc.metadata.get("segment")
                })
            
            # Add web sources
            for result in state.get("web_research", []):
                sources.append({
                    "type": "external_research",
                    "title": result.get("title", ""),
                    "url": result.get("url", "")
                })
            
            state["sources"] = sources
            
            logger.info("✓ Background context synthesized")
            
        except Exception as e:
            logger.error(f"Background synthesis failed: {e}")
            state["background_context"] = "Unable to synthesize background context."
            state["errors"] = [f"Synthesis error: {str(e)}"]
        
        return state
    
    def research(self, query: str) -> Dict:
        """
        Conduct research for a query
        
        Args:
            query: Research question
        
        Returns:
            Research results with background context and sources
        """
        logger.info(f"🔬 Research Team analyzing: {query[:100]}...")
        
        # Initialize state
        initial_state = ResearchTeamState(
            query=query,
            background_context="",
            rag_documents=[],
            web_research=[],
            key_insights=[],
            sources=[],
            errors=[]
        )
        
        try:
            # Run research workflow
            final_state = self.app.invoke(initial_state)
            
            return {
                "query": query,
                "background_context": final_state.get("background_context", ""),
                "key_insights": final_state.get("key_insights", []),
                "sources": final_state.get("sources", []),
                "documents_retrieved": len(final_state.get("rag_documents", [])),
                "web_results": len(final_state.get("web_research", [])),
                "errors": final_state.get("errors", [])
            }
            
        except Exception as e:
            logger.error(f"Research failed: {e}")
            return {
                "query": query,
                "error": str(e),
                "background_context": "",
                "sources": [],
                "errors": [str(e)]
            }


def create_research_team(
    rag_retriever: Optional[ChurnRAGRetriever] = None,
    knowledge_graph: Optional[ChurnKnowledgeGraph] = None,
    use_tavily: bool = True
) -> ResearchTeam:
    """
    Factory function to create Research Team
    
    Args:
        rag_retriever: RAG retriever instance
        knowledge_graph: Knowledge graph instance
        use_tavily: Enable Tavily search
    
    Returns:
        Initialized ResearchTeam
    """
    return ResearchTeam(
        rag_retriever=rag_retriever,
        knowledge_graph=knowledge_graph,
        use_tavily=use_tavily
    )

