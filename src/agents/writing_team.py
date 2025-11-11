"""
Document Writing Team Agent (Team 2)
Responsible for Finding Specific Use Cases and Generating Responses
with Style Guide, Case Study Expertise, and Multiple Sub-Agents
"""

import os
import logging
from typing import TypedDict, List, Dict, Optional, Annotated
import operator

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain.schema import Document

from core.rag_retrievers import ChurnRAGRetriever

logger = logging.getLogger(__name__)


class WritingTeamState(TypedDict):
    """State for Writing Team"""
    query: str
    background_context: str  # From Research Team
    use_cases: List[Document]  # Specific relevant use cases
    draft_response: str  # Initial draft
    edited_response: str  # Copy edited version
    citations: List[Dict]  # Research citations
    empathy_enhanced: str  # Final empathy-enhanced version
    style_notes: List[str]  # Style guide compliance notes
    errors: Annotated[List[str], operator.add]


class DocumentWriterAgent:
    """Sub-Agent: Document Writer for initial drafting"""
    
    def __init__(self, llm: ChatOpenAI):
        self.llm = llm
        logger.info("  ✓ Document Writer Agent initialized")
    
    def draft(self, query: str, background: str, use_cases: List[Document]) -> str:
        """
        Create initial draft response
        
        Focuses on:
        - Clear structure
        - Data-driven insights
        - Actionable recommendations
        """
        logger.info("✍️  Document Writer: Creating initial draft...")
        
        # Prepare use case context
        use_case_context = "\n\n".join([
            f"USE CASE {i+1}: {doc.metadata.get('account_name', 'Unknown')} ({doc.metadata.get('segment', 'N/A')})\n"
            f"Churn Reason: {doc.metadata.get('churn_reason', 'N/A')}\n"
            f"ARR Lost: ${doc.metadata.get('arr_lost', 0):,.2f}\n"
            f"Details: {doc.page_content[:500]}..."
            for i, doc in enumerate(use_cases[:5])
        ])
        
        drafting_prompt = f"""You are a customer success analyst drafting a comprehensive response.

QUESTION: {query}

BACKGROUND CONTEXT:
{background}

SPECIFIC USE CASES:
{use_case_context}

Write a comprehensive, well-structured response that:
1. Directly addresses the question
2. Uses specific examples from the use cases
3. Provides data-driven insights with numbers
4. Offers actionable recommendations
5. Maintains a professional, analytical tone

Structure your response with clear sections:
- Overview
- Key Findings (with specific data points)
- Recommendations
- Conclusion

Write 4-6 paragraphs."""
        
        try:
            response = self.llm.invoke(drafting_prompt)
            logger.info("  ✓ Initial draft created")
            return response.content
        except Exception as e:
            logger.error(f"Drafting failed: {e}")
            return f"Error creating draft: {str(e)}"


class CopyEditorAgent:
    """Sub-Agent: Copy Editor for editing and refinement"""
    
    def __init__(self, llm: ChatOpenAI):
        self.llm = llm
        logger.info("  ✓ Copy Editor Agent initialized")
    
    def edit(self, draft: str) -> str:
        """
        Edit and refine the draft
        
        Focuses on:
        - Clarity and conciseness
        - Grammar and style
        - Logical flow
        - Professional tone
        """
        logger.info("✂️  Copy Editor: Refining draft...")
        
        editing_prompt = f"""You are a professional copy editor reviewing a customer success analysis.

DRAFT RESPONSE:
{draft}

Edit this response to:
1. Improve clarity and conciseness
2. Fix any grammar or style issues
3. Ensure logical flow between sections
4. Maintain professional business tone
5. Strengthen transitions and connections
6. Remove redundancies
7. Ensure consistent formatting

Return the edited version with improvements. Maintain the same structure but enhance quality."""
        
        try:
            response = self.llm.invoke(editing_prompt)
            logger.info("  ✓ Draft edited and refined")
            return response.content
        except Exception as e:
            logger.error(f"Editing failed: {e}")
            return draft  # Return original if editing fails


class NoteTakerAgent:
    """Sub-Agent: Note Taker for citations and research notes"""
    
    def __init__(self, llm: ChatOpenAI):
        self.llm = llm
        logger.info("  ✓ Note Taker Agent initialized")
    
    def add_citations(self, response: str, use_cases: List[Document], sources: List[Dict]) -> List[Dict]:
        """
        Add proper citations and research notes
        
        Focuses on:
        - Source attribution
        - Data validation
        - Additional research notes
        - Reference documentation
        """
        logger.info("📝 Note Taker: Adding citations and research notes...")
        
        try:
            citations = []
            
            # Add use case citations
            for i, doc in enumerate(use_cases[:5], 1):
                citations.append({
                    "citation_id": f"UC{i}",
                    "type": "use_case",
                    "customer": doc.metadata.get("account_name", "Unknown"),
                    "segment": doc.metadata.get("segment", "N/A"),
                    "churn_reason": doc.metadata.get("churn_reason", "N/A"),
                    "arr_lost": f"${doc.metadata.get('arr_lost', 0):,.2f}",
                    "relevance": "high"
                })
            
            # Add external sources
            for i, source in enumerate(sources, 1):
                if source.get("type") == "external_research":
                    citations.append({
                        "citation_id": f"EXT{i}",
                        "type": "external_source",
                        "title": source.get("title", ""),
                        "url": source.get("url", ""),
                        "relevance": "supporting"
                    })
            
            logger.info(f"  ✓ Added {len(citations)} citations")
            return citations
            
        except Exception as e:
            logger.error(f"Citation addition failed: {e}")
            return []


class EmpathyEditorAgent:
    """Sub-Agent: Empathy Editor for compassion and customer understanding"""
    
    def __init__(self, llm: ChatOpenAI):
        self.llm = llm
        logger.info("  ✓ Empathy Editor Agent initialized")
    
    def enhance_empathy(self, response: str, query: str) -> str:
        """
        Enhance response with empathy and customer understanding
        
        Focuses on:
        - Customer-centric language
        - Empathy and compassion
        - Understanding customer pain points
        - Supportive recommendations
        """
        logger.info("💙 Empathy Editor: Enhancing with empathy and compassion...")
        
        empathy_prompt = f"""You are an empathy editor reviewing a customer success response.

ORIGINAL QUERY: {query}

RESPONSE TO ENHANCE:
{response}

Enhance this response to be more:
1. Customer-centric and empathetic
2. Understanding of customer challenges and pain points
3. Compassionate while maintaining professionalism
4. Supportive and encouraging
5. Focused on partnership and mutual success

Add phrases that:
- Acknowledge customer challenges ("We understand that...")
- Show empathy ("This is a common concern...")
- Offer support ("We're here to help you...")
- Build partnership ("Together, we can...")

Maintain all the data and insights but soften the tone to be more understanding and supportive.
Return the enhanced version."""
        
        try:
            response = self.llm.invoke(empathy_prompt)
            logger.info("  ✓ Response enhanced with empathy")
            return response.content
        except Exception as e:
            logger.error(f"Empathy enhancement failed: {e}")
            return response  # Return original if enhancement fails


class StyleGuideAgent:
    """Sub-Agent: Style Guide Checker for brand consistency"""
    
    def __init__(self, llm: ChatOpenAI):
        self.llm = llm
        logger.info("  ✓ Style Guide Agent initialized")
    
    def check_style(self, response: str) -> List[str]:
        """
        Check response against style guide
        
        Ensures:
        - Brand voice consistency
        - Terminology usage
        - Formatting standards
        - Professional standards
        """
        logger.info("📋 Style Guide: Checking compliance...")
        
        style_notes = []
        
        # Check for key style elements
        if "churn" in response.lower():
            style_notes.append("✓ Uses appropriate industry terminology")
        
        if any(word in response.lower() for word in ["recommend", "suggest", "strategy"]):
            style_notes.append("✓ Includes actionable recommendations")
        
        if "$" in response or "%" in response:
            style_notes.append("✓ Contains data-driven insights")
        
        if len(response.split()) > 200:
            style_notes.append("✓ Meets comprehensive response length requirements")
        
        logger.info(f"  ✓ Style check complete: {len(style_notes)} notes")
        return style_notes


class WritingTeam:
    """
    Document Writing Team
    
    Uses multiple specialized sub-agents:
    1. Document Writer Agent: Initial drafting
    2. Copy Editor Agent: Editing and refinement
    3. Note Taker Agent: Citations and research notes
    4. Empathy Editor Agent: Empathy and customer understanding
    5. Style Guide Agent: Brand consistency
    
    Works with specific use cases from RAG to generate high-quality,
    empathetic, well-cited responses.
    """
    
    def __init__(self, rag_retriever: Optional[ChurnRAGRetriever] = None):
        """
        Initialize Writing Team
        
        Args:
            rag_retriever: RAG retriever for finding specific use cases
        """
        logger.info("📝 Initializing Document Writing Team...")
        
        # Initialize LLM (higher temperature for creative writing)
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        
        # Store RAG retriever
        self.rag_retriever = rag_retriever
        
        # Initialize sub-agents
        logger.info("  Initializing sub-agents:")
        self.writer = DocumentWriterAgent(self.llm)
        self.editor = CopyEditorAgent(self.llm)
        self.note_taker = NoteTakerAgent(self.llm)
        self.empathy_editor = EmpathyEditorAgent(self.llm)
        self.style_guide = StyleGuideAgent(self.llm)
        
        # Build workflow
        self.graph = self._build_graph()
        self.app = self.graph.compile()
        
        logger.info("✅ Document Writing Team initialized with 5 sub-agents")
    
    def _build_graph(self) -> StateGraph:
        """Build Writing Team workflow"""
        logger.info("Building Writing Team workflow...")
        
        workflow = StateGraph(WritingTeamState)
        
        # Add nodes
        workflow.add_node("find_use_cases", self._find_use_cases)
        workflow.add_node("draft_response", self._draft_response)
        workflow.add_node("edit_response", self._edit_response)
        workflow.add_node("add_citations", self._add_citations)
        workflow.add_node("enhance_empathy", self._enhance_empathy)
        workflow.add_node("check_style", self._check_style)
        
        # Set entry point
        workflow.set_entry_point("find_use_cases")
        
        # Add edges (sequential workflow)
        workflow.add_edge("find_use_cases", "draft_response")
        workflow.add_edge("draft_response", "edit_response")
        workflow.add_edge("edit_response", "add_citations")
        workflow.add_edge("add_citations", "enhance_empathy")
        workflow.add_edge("enhance_empathy", "check_style")
        workflow.add_edge("check_style", END)
        
        logger.info("✓ Writing Team workflow built")
        return workflow
    
    def _find_use_cases(self, state: WritingTeamState) -> WritingTeamState:
        """Find specific use cases relevant to the query"""
        logger.info("🔍 Finding specific use cases...")
        
        if not self.rag_retriever:
            logger.warning("RAG retriever not initialized")
            state["use_cases"] = []
            return state
        
        try:
            query = state["query"]
            
            # Use reranking for most relevant use cases
            docs = self.rag_retriever.rerank_retrieval(query, k=8)
            
            state["use_cases"] = docs
            logger.info(f"✓ Found {len(docs)} relevant use cases")
            
        except Exception as e:
            logger.error(f"Use case retrieval failed: {e}")
            state["use_cases"] = []
            state["errors"] = [f"Use case retrieval error: {str(e)}"]
        
        return state
    
    def _draft_response(self, state: WritingTeamState) -> WritingTeamState:
        """Draft initial response using Document Writer Agent"""
        draft = self.writer.draft(
            query=state["query"],
            background=state.get("background_context", ""),
            use_cases=state.get("use_cases", [])
        )
        state["draft_response"] = draft
        return state
    
    def _edit_response(self, state: WritingTeamState) -> WritingTeamState:
        """Edit response using Copy Editor Agent"""
        edited = self.editor.edit(state.get("draft_response", ""))
        state["edited_response"] = edited
        return state
    
    def _add_citations(self, state: WritingTeamState) -> WritingTeamState:
        """Add citations using Note Taker Agent"""
        citations = self.note_taker.add_citations(
            response=state.get("edited_response", ""),
            use_cases=state.get("use_cases", []),
            sources=[]  # Additional sources can be passed from research team
        )
        state["citations"] = citations
        return state
    
    def _enhance_empathy(self, state: WritingTeamState) -> WritingTeamState:
        """Enhance with empathy using Empathy Editor Agent"""
        enhanced = self.empathy_editor.enhance_empathy(
            response=state.get("edited_response", ""),
            query=state["query"]
        )
        state["empathy_enhanced"] = enhanced
        return state
    
    def _check_style(self, state: WritingTeamState) -> WritingTeamState:
        """Check style compliance using Style Guide Agent"""
        style_notes = self.style_guide.check_style(
            state.get("empathy_enhanced", "")
        )
        state["style_notes"] = style_notes
        return state
    
    def write(self, query: str, background_context: str = "") -> Dict:
        """
        Generate a comprehensive response
        
        Args:
            query: User question
            background_context: Context from Research Team
        
        Returns:
            Comprehensive response with citations and metadata
        """
        logger.info(f"📝 Writing Team generating response for: {query[:100]}...")
        
        # Initialize state
        initial_state = WritingTeamState(
            query=query,
            background_context=background_context,
            use_cases=[],
            draft_response="",
            edited_response="",
            citations=[],
            empathy_enhanced="",
            style_notes=[],
            errors=[]
        )
        
        try:
            # Run writing workflow
            final_state = self.app.invoke(initial_state)
            
            return {
                "query": query,
                "final_response": final_state.get("empathy_enhanced", ""),
                "draft_response": final_state.get("draft_response", ""),
                "citations": final_state.get("citations", []),
                "style_notes": final_state.get("style_notes", []),
                "use_cases_found": len(final_state.get("use_cases", [])),
                "errors": final_state.get("errors", [])
            }
            
        except Exception as e:
            logger.error(f"Writing failed: {e}")
            return {
                "query": query,
                "error": str(e),
                "final_response": "",
                "citations": [],
                "errors": [str(e)]
            }


def create_writing_team(rag_retriever: Optional[ChurnRAGRetriever] = None) -> WritingTeam:
    """
    Factory function to create Writing Team
    
    Args:
        rag_retriever: RAG retriever instance
    
    Returns:
        Initialized WritingTeam
    """
    return WritingTeam(rag_retriever=rag_retriever)

