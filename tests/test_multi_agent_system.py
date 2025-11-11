"""
Test Multi-Agent System
Comprehensive testing of the Research Team and Writing Team
"""

import os
import sys
import logging
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from agents.multi_agent_system import create_multi_agent_system
from agents.research_team import create_research_team
from agents.writing_team import create_writing_team
from core.rag_retrievers import initialize_churn_rag_system
from core.knowledge_graph import build_churn_knowledge_graph

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_research_team():
    """Test Research Team independently"""
    print("\n" + "="*80)
    print("🔬 TESTING RESEARCH TEAM")
    print("="*80)
    
    try:
        # Initialize components
        logger.info("Initializing RAG retriever...")
        rag_retriever = initialize_churn_rag_system()
        
        logger.info("Loading knowledge graph...")
        kg_path = Path("cache/churn_knowledge_graph.pkl")
        if kg_path.exists():
            from core.knowledge_graph import ChurnKnowledgeGraph
            kg = ChurnKnowledgeGraph.load(str(kg_path))
        else:
            logger.warning("Knowledge graph not found, building from scratch...")
            kg = build_churn_knowledge_graph()
        
        # Create research team
        research_team = create_research_team(
            rag_retriever=rag_retriever,
            knowledge_graph=kg,
            use_tavily=bool(os.getenv("TAVILY_API_KEY"))
        )
        
        # Test query
        test_query = "What are the main reasons customers churn in the Commercial segment?"
        
        print(f"\n📋 Test Query: {test_query}")
        print("\nRunning research team...")
        
        result = research_team.research(test_query)
        
        print("\n✅ RESEARCH TEAM RESULTS:")
        print("-" * 80)
        print(f"\nBackground Context ({len(result['background_context'])} chars):")
        print(result['background_context'][:500] + "..." if len(result['background_context']) > 500 else result['background_context'])
        
        print(f"\n\nKey Insights ({len(result['key_insights'])} found):")
        for i, insight in enumerate(result['key_insights'], 1):
            print(f"  {i}. {insight}")
        
        print(f"\n\nSources ({len(result['sources'])} found):")
        for i, source in enumerate(result['sources'][:5], 1):
            print(f"  {i}. {source}")
        
        print(f"\n\nMetrics:")
        print(f"  - Documents Retrieved: {result['documents_retrieved']}")
        print(f"  - Web Results: {result['web_results']}")
        print(f"  - Errors: {len(result.get('errors', []))}")
        
        return True
        
    except Exception as e:
        logger.error(f"Research team test failed: {e}", exc_info=True)
        return False


def test_writing_team():
    """Test Writing Team independently"""
    print("\n" + "="*80)
    print("📝 TESTING WRITING TEAM")
    print("="*80)
    
    try:
        # Initialize components
        logger.info("Initializing RAG retriever...")
        rag_retriever = initialize_churn_rag_system()
        
        # Create writing team
        writing_team = create_writing_team(rag_retriever=rag_retriever)
        
        # Test query and background context
        test_query = "What are the main reasons customers churn in the Commercial segment?"
        background_context = """Based on our internal data and industry research, customer churn in the Commercial segment 
        is influenced by several key factors including pricing concerns, lack of feature adoption, competitive alternatives, 
        and insufficient customer success engagement. The data shows that Commercial customers have a higher price sensitivity 
        compared to Enterprise customers, with approximately 35% of churned Commercial customers citing pricing as a primary factor."""
        
        print(f"\n📋 Test Query: {test_query}")
        print(f"\n📚 Background Context Provided: {len(background_context)} chars")
        print("\nRunning writing team (5 sub-agents)...")
        
        result = writing_team.write(test_query, background_context)
        
        print("\n✅ WRITING TEAM RESULTS:")
        print("-" * 80)
        print(f"\nFinal Response ({len(result['final_response'])} chars):")
        print(result['final_response'])
        
        print(f"\n\nCitations ({len(result['citations'])} found):")
        for i, citation in enumerate(result['citations'][:5], 1):
            print(f"  {i}. {citation}")
        
        print(f"\n\nStyle Notes ({len(result['style_notes'])} found):")
        for i, note in enumerate(result['style_notes'], 1):
            print(f"  {i}. {note}")
        
        print(f"\n\nMetrics:")
        print(f"  - Use Cases Found: {result['use_cases_found']}")
        print(f"  - Draft Response Length: {len(result.get('draft_response', ''))} chars")
        print(f"  - Final Response Length: {len(result['final_response'])} chars")
        print(f"  - Errors: {len(result.get('errors', []))}")
        
        return True
        
    except Exception as e:
        logger.error(f"Writing team test failed: {e}", exc_info=True)
        return False


def test_multi_agent_system():
    """Test complete multi-agent system"""
    print("\n" + "="*80)
    print("🤖 TESTING COMPLETE MULTI-AGENT SYSTEM")
    print("="*80)
    
    try:
        # Initialize components
        logger.info("Initializing RAG retriever...")
        rag_retriever = initialize_churn_rag_system()
        
        logger.info("Loading knowledge graph...")
        kg_path = Path("cache/churn_knowledge_graph.pkl")
        if kg_path.exists():
            from core.knowledge_graph import ChurnKnowledgeGraph
            kg = ChurnKnowledgeGraph.load(str(kg_path))
        else:
            logger.warning("Knowledge graph not found, building from scratch...")
            kg = build_churn_knowledge_graph()
        
        # Create multi-agent system
        system = create_multi_agent_system(
            rag_retriever=rag_retriever,
            knowledge_graph=kg,
            use_tavily=bool(os.getenv("TAVILY_API_KEY"))
        )
        
        # Test queries
        test_queries = [
            "What are the main churn reasons for Commercial segment customers?",
            "Which competitors are our customers switching to and why?",
            "What retention strategies would work best for high-value Enterprise customers?"
        ]
        
        for i, query in enumerate(test_queries, 1):
            print(f"\n\n{'='*80}")
            print(f"TEST QUERY {i}/{len(test_queries)}")
            print("="*80)
            print(f"\n📋 Query: {query}")
            print("\nRunning multi-agent analysis...")
            
            result = system.analyze(query)
            
            print("\n✅ MULTI-AGENT ANALYSIS RESULTS:")
            print("-" * 80)
            
            print(f"\nQuery Type: {result.get('query_type', 'Unknown')}")
            
            print(f"\n\nBackground Context ({len(result.get('background_context', ''))} chars):")
            bg = result.get('background_context', '')
            print(bg[:400] + "..." if len(bg) > 400 else bg)
            
            print(f"\n\nFinal Response ({len(result.get('response', ''))} chars):")
            print(result.get('response', 'No response generated'))
            
            print(f"\n\nKey Insights ({len(result.get('key_insights', []))} found):")
            for j, insight in enumerate(result.get('key_insights', [])[:5], 1):
                print(f"  {j}. {insight}")
            
            print(f"\n\nProcessing Stages:")
            for stage in result.get('processing_stages', []):
                print(f"  ✓ {stage}")
            
            print(f"\n\nMetrics:")
            print(f"  - Confidence Score: {result.get('confidence_score', 0):.2%}")
            print(f"  - Total Sources: {result.get('total_sources', 0)}")
            print(f"  - Citations: {len(result.get('citations', []))}")
            print(f"  - Style Notes: {len(result.get('style_notes', []))}")
            print(f"  - Errors: {len(result.get('errors', []))}")
            
            if result.get('errors'):
                print(f"\n⚠️  Errors encountered:")
                for error in result['errors']:
                    print(f"  - {error}")
        
        return True
        
    except Exception as e:
        logger.error(f"Multi-agent system test failed: {e}", exc_info=True)
        return False


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("🧪 MULTI-AGENT SYSTEM TEST SUITE")
    print("="*80)
    
    # Check prerequisites
    print("\n📋 Checking Prerequisites...")
    
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY not set")
        sys.exit(1)
    print("✓ OPENAI_API_KEY set")
    
    if os.getenv("TAVILY_API_KEY"):
        print("✓ TAVILY_API_KEY set (external research enabled)")
    else:
        print("⚠️  TAVILY_API_KEY not set (external research disabled)")
    
    # Check Qdrant
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))
        collections = client.get_collections()
        print("✓ Qdrant is accessible")
    except Exception as e:
        print(f"❌ Qdrant not accessible: {e}")
        print("Please start Qdrant: docker-compose up -d qdrant")
        sys.exit(1)
    
    # Run tests
    results = {}
    
    print("\n" + "="*80)
    print("RUNNING TESTS")
    print("="*80)
    
    # Test 1: Research Team
    print("\n[1/3] Testing Research Team...")
    results['research_team'] = test_research_team()
    
    # Test 2: Writing Team
    print("\n[2/3] Testing Writing Team...")
    results['writing_team'] = test_writing_team()
    
    # Test 3: Multi-Agent System
    print("\n[3/3] Testing Multi-Agent System...")
    results['multi_agent_system'] = test_multi_agent_system()
    
    # Summary
    print("\n\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    for test_name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status} - {test_name}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n🎉 ALL TESTS PASSED!")
    else:
        print("\n⚠️  SOME TESTS FAILED")
    
    print("="*80 + "\n")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

