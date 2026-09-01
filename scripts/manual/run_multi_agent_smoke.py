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
        
        # Create multi-agent system
        system = create_multi_agent_system(
            rag_retriever=rag_retriever,
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

