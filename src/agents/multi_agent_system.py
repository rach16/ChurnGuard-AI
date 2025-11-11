"""
Multi-Agent System Coordinator
Orchestrates Research Team and Document Writing Team for comprehensive churn analysis
"""

import os
import logging
from typing import TypedDict, Dict, Optional, List, Annotated
import operator

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI

from agents.research_team import ResearchTeam, create_research_team
from agents.writing_team import WritingTeam, create_writing_team
from core.rag_retrievers import ChurnRAGRetriever
from core.knowledge_graph import ChurnKnowledgeGraph

logger = logging.getLogger(__name__)


class MultiAgentState(TypedDict):
    """State for multi-agent system"""
    query: str
    query_type: Optional[str]
    
    # Research Team outputs
    background_context: str
    research_insights: List[str]
    research_sources: List[Dict]
    
    # Writing Team outputs
    final_response: str
    draft_response: str
    citations: List[Dict]
    style_notes: List[str]
    
    # Metadata
    confidence_score: float
    processing_stages: List[str]
    errors: Annotated[List[str], operator.add]


class MultiAgentChurnSystem:
    """
    Multi-Agent Customer Churn Analysis System
    
    Architecture:
    
    ┌─────────────────────────────────────────────────────────────┐
    │                   MULTI-AGENT SYSTEM                         │
    └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │   Query Classification │
                    └───────────────────────┘
                                │
                ┌───────────────┴───────────────┐
                ▼                               ▼
    ┌───────────────────────┐       ┌───────────────────────┐
    │   TEAM 1: RESEARCH    │       │   TEAM 2: WRITING     │
    │   Policy Expertise    │──────▶│   Case Study Experts  │
    └───────────────────────┘       └───────────────────────┘
    │                                │
    │ • RAG Tool (Policies)          │ • RAG Tool (Use Cases)
    │ • Tavily Search               │ • Sub-Agents:
    │ • Knowledge Graph             │   - Document Writer
    │                                │   - Copy Editor
    │                                │   - Note Taker
    │                                │   - Empathy Editor
    │                                │   - Style Guide
    │                                │
    └────────────────────────────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │   Final Synthesis     │
                    └───────────────────────┘
    
    Flow:
    1. Query Understanding → Classify intent
    2. Research Team → Gather background context
    3. Writing Team → Generate detailed response
    4. Synthesis → Combine insights and validate
    """
    
    def __init__(
        self,
        rag_retriever: Optional[ChurnRAGRetriever] = None,
        knowledge_graph: Optional[ChurnKnowledgeGraph] = None,
        use_tavily: bool = True
    ):
        """
        Initialize Multi-Agent System
        
        Args:
            rag_retriever: RAG retriever for both teams
            knowledge_graph: Knowledge graph for research team
            use_tavily: Enable Tavily search for research team
        """
        logger.info("🤖 Initializing Multi-Agent Churn Analysis System...")
        logger.info("="*80)
        
        # Initialize LLM for coordination
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.5,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        
        # Initialize Agent Teams
        logger.info("\n🔬 Team 1: Research Team with Policy Expertise")
        logger.info("   Responsible for: High-Level Background Context")
        self.research_team = create_research_team(
            rag_retriever=rag_retriever,
            knowledge_graph=knowledge_graph,
            use_tavily=use_tavily
        )
        
        logger.info("\n📝 Team 2: Document Writing Team with Case Study Expertise")
        logger.info("   Responsible for: Finding Use Cases & Generating Responses")
        self.writing_team = create_writing_team(
            rag_retriever=rag_retriever
        )
        
        # Build coordination workflow
        self.graph = self._build_graph()
        self.app = self.graph.compile()
        
        logger.info("\n" + "="*80)
        logger.info("✅ Multi-Agent System Ready")
        logger.info(f"   • Research Team: RAG + Tavily + Knowledge Graph")
        logger.info(f"   • Writing Team: 5 Sub-Agents (Writer, Editor, Note Taker, Empathy, Style)")
        logger.info("="*80 + "\n")
    
    def _build_graph(self) -> StateGraph:
        """Build multi-agent coordination workflow"""
        logger.info("Building multi-agent coordination workflow...")
        
        workflow = StateGraph(MultiAgentState)
        
        # Add nodes
        workflow.add_node("classify_query", self._classify_query)
        workflow.add_node("research_phase", self._research_phase)
        workflow.add_node("writing_phase", self._writing_phase)
        workflow.add_node("synthesize_final", self._synthesize_final)
        
        # Set entry point
        workflow.set_entry_point("classify_query")
        
        # Add edges
        workflow.add_edge("classify_query", "research_phase")
        workflow.add_edge("research_phase", "writing_phase")
        workflow.add_edge("writing_phase", "synthesize_final")
        workflow.add_edge("synthesize_final", END)
        
        logger.info("✓ Multi-agent workflow built")
        return workflow
    
    def _classify_query(self, state: MultiAgentState) -> MultiAgentState:
        """
        Classify query type and intent
        
        Query types:
        - risk_assessment: Customer-specific churn risk
        - pattern_analysis: Churn patterns and trends
        - retention_strategy: Retention recommendations
        - competitive_intel: Competitor analysis
        - general_inquiry: General questions
        """
        logger.info("\n" + "="*80)
        logger.info("🎯 STAGE 1: Query Classification")
        logger.info("="*80)
        logger.info(f"Query: {state['query'][:100]}...")
        
        classification_prompt = f"""Classify this customer churn query:

Question: {state['query']}

Classify into ONE category:
1. risk_assessment - Predicting churn risk for specific customers
2. pattern_analysis - Analyzing churn patterns, trends, or segments
3. retention_strategy - Generating retention strategies
4. competitive_intel - Competitor analysis
5. general_inquiry - General questions

Return ONLY the category name."""
        
        try:
            response = self.llm.invoke(classification_prompt)
            query_type = response.content.strip().lower()
            
            # Validate and set query type
            valid_types = ["risk_assessment", "pattern_analysis", "retention_strategy", 
                          "competitive_intel", "general_inquiry"]
            
            if query_type in valid_types:
                state["query_type"] = query_type
            else:
                state["query_type"] = "pattern_analysis"  # Default
            
            logger.info(f"✓ Query classified as: {state['query_type']}")
            state["processing_stages"] = ["classification_complete"]
            
        except Exception as e:
            logger.error(f"Classification failed: {e}")
            state["query_type"] = "pattern_analysis"
            state["errors"] = [f"Classification error: {str(e)}"]
        
        return state
    
    def _research_phase(self, state: MultiAgentState) -> MultiAgentState:
        """
        Execute Research Team phase
        
        Research Team gathers:
        - High-level background context
        - Industry benchmarks and trends
        - Company policies and regulations
        - Historical patterns from knowledge graph
        """
        logger.info("\n" + "="*80)
        logger.info("🔬 STAGE 2: Research Team - Gathering Background Context")
        logger.info("="*80)
        logger.info("Tools: RAG (Policies) + Tavily Search + Knowledge Graph")
        
        try:
            # Execute research team
            research_results = self.research_team.research(state["query"])
            
            # Store results in state
            state["background_context"] = research_results.get("background_context", "")
            state["research_insights"] = research_results.get("key_insights", [])
            state["research_sources"] = research_results.get("sources", [])
            
            # Log results
            logger.info(f"✓ Background context generated ({len(state['background_context'])} chars)")
            logger.info(f"✓ Key insights: {len(state['research_insights'])}")
            logger.info(f"✓ Sources gathered: {len(state['research_sources'])}")
            
            # Add to processing stages
            stages = state.get("processing_stages", [])
            stages.append("research_complete")
            state["processing_stages"] = stages
            
        except Exception as e:
            logger.error(f"Research phase failed: {e}")
            state["background_context"] = ""
            state["research_insights"] = []
            state["research_sources"] = []
            state["errors"] = [f"Research phase error: {str(e)}"]
        
        return state
    
    def _writing_phase(self, state: MultiAgentState) -> MultiAgentState:
        """
        Execute Writing Team phase
        
        Writing Team generates:
        - Detailed response with specific use cases
        - Draft → Edit → Citations → Empathy → Style check
        - Using 5 specialized sub-agents
        """
        logger.info("\n" + "="*80)
        logger.info("📝 STAGE 3: Writing Team - Generating Detailed Response")
        logger.info("="*80)
        logger.info("Sub-Agents: Writer → Editor → Note Taker → Empathy → Style Guide")
        
        try:
            # Execute writing team
            writing_results = self.writing_team.write(
                query=state["query"],
                background_context=state.get("background_context", "")
            )
            
            # Store results in state
            state["final_response"] = writing_results.get("final_response", "")
            state["draft_response"] = writing_results.get("draft_response", "")
            state["citations"] = writing_results.get("citations", [])
            state["style_notes"] = writing_results.get("style_notes", [])
            
            # Log results
            logger.info(f"✓ Final response generated ({len(state['final_response'])} chars)")
            logger.info(f"✓ Citations added: {len(state['citations'])}")
            logger.info(f"✓ Style notes: {len(state['style_notes'])}")
            logger.info(f"✓ Use cases referenced: {writing_results.get('use_cases_found', 0)}")
            
            # Add to processing stages
            stages = state.get("processing_stages", [])
            stages.append("writing_complete")
            state["processing_stages"] = stages
            
        except Exception as e:
            logger.error(f"Writing phase failed: {e}")
            state["final_response"] = ""
            state["citations"] = []
            state["errors"] = [f"Writing phase error: {str(e)}"]
        
        return state
    
    def _synthesize_final(self, state: MultiAgentState) -> MultiAgentState:
        """
        Synthesize final results and calculate confidence
        
        Combines:
        - Research insights
        - Detailed response
        - Citations and sources
        - Quality metrics
        """
        logger.info("\n" + "="*80)
        logger.info("🎯 STAGE 4: Final Synthesis & Quality Check")
        logger.info("="*80)
        
        try:
            # Calculate confidence score
            factors = {
                "background_context": 0.2 if state.get("background_context") else 0,
                "research_insights": 0.15 * min(len(state.get("research_insights", [])) / 5, 1),
                "final_response": 0.3 if len(state.get("final_response", "")) > 500 else 0.15,
                "citations": 0.2 * min(len(state.get("citations", [])) / 5, 1),
                "style_compliance": 0.15 * min(len(state.get("style_notes", [])) / 4, 1)
            }
            
            confidence = sum(factors.values())
            state["confidence_score"] = round(confidence, 2)
            
            # Log final synthesis
            logger.info(f"✓ Confidence Score: {state['confidence_score']:.2%}")
            logger.info(f"✓ Processing Stages Completed: {len(state.get('processing_stages', []))}")
            logger.info(f"✓ Total Sources: {len(state.get('research_sources', []))} + {len(state.get('citations', []))}")
            
            # Add final stage
            stages = state.get("processing_stages", [])
            stages.append("synthesis_complete")
            state["processing_stages"] = stages
            
            logger.info("\n" + "="*80)
            logger.info("✅ MULTI-AGENT PROCESSING COMPLETE")
            logger.info("="*80 + "\n")
            
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            state["confidence_score"] = 0.0
            state["errors"] = [f"Synthesis error: {str(e)}"]
        
        return state
    
    def analyze(self, query: str) -> Dict:
        """
        Run complete multi-agent analysis
        
        Args:
            query: User question
        
        Returns:
            Comprehensive analysis with research, writing, and metadata
        """
        logger.info(f"\n{'='*80}")
        logger.info(f"🚀 STARTING MULTI-AGENT ANALYSIS")
        logger.info(f"{'='*80}")
        logger.info(f"Query: {query}")
        
        # Initialize state
        initial_state = MultiAgentState(
            query=query,
            query_type=None,
            background_context="",
            research_insights=[],
            research_sources=[],
            final_response="",
            draft_response="",
            citations=[],
            style_notes=[],
            confidence_score=0.0,
            processing_stages=[],
            errors=[]
        )
        
        try:
            # Run multi-agent system
            final_state = self.app.invoke(initial_state)
            
            # Format response
            response = {
                "query": query,
                "query_type": final_state.get("query_type"),
                
                # Main outputs
                "response": final_state.get("final_response", ""),
                "background_context": final_state.get("background_context", ""),
                
                # Supporting information
                "key_insights": final_state.get("research_insights", []),
                "citations": final_state.get("citations", []),
                "style_notes": final_state.get("style_notes", []),
                
                # Metadata
                "confidence_score": final_state.get("confidence_score", 0.0),
                "processing_stages": final_state.get("processing_stages", []),
                "total_sources": len(final_state.get("research_sources", [])) + len(final_state.get("citations", [])),
                
                # Research details
                "research_sources": final_state.get("research_sources", []),
                
                # Errors
                "errors": final_state.get("errors", [])
            }
            
            logger.info(f"\n{'='*80}")
            logger.info(f"✅ Analysis complete (confidence: {response['confidence_score']:.2%})")
            logger.info(f"{'='*80}\n")
            
            return response
            
        except Exception as e:
            logger.error(f"Multi-agent analysis failed: {e}")
            return {
                "query": query,
                "error": str(e),
                "response": "",
                "confidence_score": 0.0,
                "errors": [str(e)]
            }


def create_multi_agent_system(
    rag_retriever: Optional[ChurnRAGRetriever] = None,
    knowledge_graph: Optional[ChurnKnowledgeGraph] = None,
    use_tavily: bool = True
) -> MultiAgentChurnSystem:
    """
    Factory function to create multi-agent system
    
    Args:
        rag_retriever: RAG retriever instance
        knowledge_graph: Knowledge graph instance
        use_tavily: Enable Tavily search
    
    Returns:
        Initialized MultiAgentChurnSystem
    """
    return MultiAgentChurnSystem(
        rag_retriever=rag_retriever,
        knowledge_graph=knowledge_graph,
        use_tavily=use_tavily
    )


if __name__ == "__main__":
    # Test the multi-agent system
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s'
    )
    
    print("\n" + "="*80)
    print("🤖 TESTING MULTI-AGENT CHURN ANALYSIS SYSTEM")
    print("="*80)
    
    # Create system without dependencies for structure test
    system = create_multi_agent_system(
        rag_retriever=None,
        knowledge_graph=None,
        use_tavily=False
    )
    
    print(f"\n✅ Multi-Agent System created successfully")
    print(f"   Architecture:")
    print(f"   ├── Team 1: Research Team (RAG + Tavily + Knowledge Graph)")
    print(f"   └── Team 2: Writing Team (5 Sub-Agents)")
    print(f"       ├── Document Writer Agent")
    print(f"       ├── Copy Editor Agent")
    print(f"       ├── Note Taker Agent")
    print(f"       ├── Empathy Editor Agent")
    print(f"       └── Style Guide Agent")
    
    print("\n💡 To test with full functionality:")
    print("   1. Initialize RAG: retriever = initialize_churn_rag_system()")
    print("   2. Load KG: kg = build_churn_knowledge_graph()")
    print("   3. Create system: system = create_multi_agent_system(retriever, kg)")
    print("   4. Run analysis: result = system.analyze('What are main churn reasons?')")
    
    print("\n" + "="*80)

