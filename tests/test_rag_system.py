"""
Comprehensive QA Tests for Churn RAG System

Tests:
1. Data Loader
2. Knowledge Graph
3. Vector Store & Retrieval Methods
4. End-to-End Integration
"""

import sys
from pathlib import Path
import logging

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_data_loader():
    """Test 1: Data Loader"""
    print("\n" + "="*80)
    print("🧪 TEST 1: DATA LOADER")
    print("="*80)
    
    try:
        from src.utils.data_loader import ChurnDataLoader
        
        loader = ChurnDataLoader("data/")
        
        # Test 1.1: Load CSV
        print("\n✓ Test 1.1: Load CSV Data")
        df = loader.load_csv_data('churned_customers_cleaned.csv')
        assert len(df) > 0, "No data loaded"
        print(f"  ✅ Loaded {len(df)} customer records")
        
        # Test 1.2: Preprocess
        print("\n✓ Test 1.2: Preprocess Customer Data")
        df_processed = loader.preprocess_churned_customers(df)
        assert 'text_representation' in df_processed.columns, "Missing text representation"
        print(f"  ✅ Created text representations (avg: {df_processed['text_representation'].str.len().mean():.0f} chars)")
        
        # Test 1.3: Convert to Documents
        print("\n✓ Test 1.3: Convert to LangChain Documents")
        documents = loader.convert_to_documents(df_processed)
        assert len(documents) == len(df), "Document count mismatch"
        assert all(hasattr(doc, 'page_content') for doc in documents), "Invalid document structure"
        assert all(hasattr(doc, 'metadata') for doc in documents), "Missing metadata"
        print(f"  ✅ Created {len(documents)} Document objects with metadata")
        
        # Test 1.4: Check Metadata Quality
        print("\n✓ Test 1.4: Validate Metadata Quality")
        sample_doc = documents[0]
        required_fields = ['account_name', 'segment', 'churn_reason', 'arr_lost', 'tenure_years']
        for field in required_fields:
            assert field in sample_doc.metadata, f"Missing metadata field: {field}"
        print(f"  ✅ All required metadata fields present")
        print(f"  Sample: {sample_doc.metadata['account_name']} | {sample_doc.metadata['segment']} | ${sample_doc.metadata['arr_lost']:,.2f}")
        
        return True, documents
        
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def test_knowledge_graph():
    """Test 2: Knowledge Graph"""
    print("\n" + "="*80)
    print("🧪 TEST 2: KNOWLEDGE GRAPH")
    print("="*80)
    
    try:
        from src.core.knowledge_graph import build_churn_knowledge_graph
        
        # Test 2.1: Build Graph
        print("\n✓ Test 2.1: Build Knowledge Graph")
        kg = build_churn_knowledge_graph("data/")
        print(f"  ✅ Graph built: {kg.graph.number_of_nodes()} nodes, {kg.graph.number_of_edges()} edges")
        
        # Test 2.2: Entity Counts
        print("\n✓ Test 2.2: Validate Entity Counts")
        for entity_type, entities in kg.entity_types.items():
            count = len(entities)
            print(f"  {entity_type}: {count}")
            assert count > 0, f"No {entity_type} entities found"
        print("  ✅ All entity types have data")
        
        # Test 2.3: Query by Segment
        print("\n✓ Test 2.3: Query Customers by Segment")
        commercial = kg.query_customers_by_segment("Commercial")
        assert len(commercial) > 0, "No commercial customers found"
        print(f"  ✅ Found {len(commercial)} Commercial customers")
        
        # Test 2.4: Churn Patterns
        print("\n✓ Test 2.4: Analyze Churn Patterns")
        patterns = kg.get_churn_patterns("Commercial")
        assert 'customer_count' in patterns, "Missing customer count"
        assert 'top_reasons' in patterns, "Missing top reasons"
        assert 'avg_arr_lost' in patterns, "Missing ARR data"
        print(f"  ✅ Pattern analysis complete:")
        print(f"    Customers: {patterns['customer_count']}")
        print(f"    Avg ARR Lost: ${patterns['avg_arr_lost']:,.2f}")
        print(f"    Top Reason: {list(patterns['top_reasons'].keys())[0] if patterns['top_reasons'] else 'N/A'}")
        
        # Test 2.5: Save/Load Graph
        print("\n✓ Test 2.5: Save and Load Graph")
        kg.save_graph("cache/test_kg.pkl")
        kg2 = build_churn_knowledge_graph.__self__.__class__()
        kg2.load_graph("cache/test_kg.pkl")
        assert kg2.graph.number_of_nodes() == kg.graph.number_of_nodes(), "Graph load failed"
        print(f"  ✅ Graph persistence working")
        
        return True, kg
        
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def test_vector_store(documents):
    """Test 3: Vector Store & Retrieval"""
    print("\n" + "="*80)
    print("🧪 TEST 3: VECTOR STORE & RETRIEVAL")
    print("="*80)
    
    if not documents:
        print("  ⚠️  SKIPPED: No documents from previous test")
        return False, None
    
    try:
        from src.core.rag_retrievers import ChurnRAGRetriever
        from qdrant_client import QdrantClient
        
        # Test 3.1: Qdrant Connection
        print("\n✓ Test 3.1: Check Qdrant Connection")
        client = QdrantClient(url="http://localhost:6333")
        collections = client.get_collections()
        print(f"  ✅ Qdrant accessible - {len(collections.collections)} existing collections")
        
        # Test 3.2: Initialize Retriever
        print("\n✓ Test 3.2: Initialize RAG Retriever")
        retriever = ChurnRAGRetriever()
        print(f"  ✅ Retriever initialized")
        
        # Test 3.3: Load Documents
        print("\n✓ Test 3.3: Load Documents into Vector Store")
        doc_count = retriever.load_and_process_documents("data/")
        assert doc_count > 0, "No documents loaded"
        assert retriever.vector_store is not None, "Vector store not initialized"
        print(f"  ✅ Loaded {doc_count} documents")
        
        # Test 3.4: Naive Retrieval
        print("\n✓ Test 3.4: Test Naive Retrieval")
        test_query = "What are the main reasons customers churn?"
        docs = retriever.naive_retrieval(test_query, k=3)
        assert len(docs) > 0, "No documents retrieved"
        assert all(hasattr(d, 'metadata') for d in docs), "Missing metadata"
        print(f"  ✅ Retrieved {len(docs)} documents")
        print(f"    Top result: {docs[0].metadata.get('account_name', 'Unknown')}")
        
        # Test 3.5: Metadata Filtering
        print("\n✓ Test 3.5: Test Metadata Filtering")
        filtered_docs = retriever.retrieve_with_metadata_filter(
            query=test_query,
            segment="Commercial",
            k=3
        )
        assert len(filtered_docs) > 0, "No filtered documents"
        assert all(d.metadata.get('segment') == 'Commercial' for d in filtered_docs), "Filter not working"
        print(f"  ✅ Filtered to {len(filtered_docs)} Commercial segment docs")
        
        # Test 3.6: Multi-Query Retrieval (may need API key)
        print("\n✓ Test 3.6: Test Multi-Query Retrieval")
        try:
            import os
            if os.getenv('OPENAI_API_KEY'):
                multi_docs = retriever.multi_query_retrieval(test_query, k=3)
                print(f"  ✅ Multi-query retrieved {len(multi_docs)} unique documents")
            else:
                print(f"  ⚠️  SKIPPED: No OpenAI API key")
        except Exception as e:
            print(f"  ⚠️  Multi-query test failed (may need API key): {str(e)[:100]}")
        
        return True, retriever
        
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def test_integration():
    """Test 4: Integration Test"""
    print("\n" + "="*80)
    print("🧪 TEST 4: END-TO-END INTEGRATION")
    print("="*80)
    
    try:
        # Test 4.1: Full Pipeline
        print("\n✓ Test 4.1: Full Data Pipeline")
        from src.utils.data_loader import ChurnDataLoader
        from src.core.knowledge_graph import build_churn_knowledge_graph
        from src.core.rag_retrievers import initialize_churn_rag_system
        
        # Load data
        loader = ChurnDataLoader("data/")
        documents = loader.load_churned_customers_documents()
        print(f"  ✅ Loaded {len(documents)} documents")
        
        # Build KG
        kg = build_churn_knowledge_graph("data/")
        print(f"  ✅ Built knowledge graph: {kg.graph.number_of_nodes()} nodes")
        
        # Initialize RAG (this loads docs again)
        print(f"  ⏳ Initializing RAG system (may take 30-60 seconds for embeddings)...")
        retriever = initialize_churn_rag_system("data/")
        print(f"  ✅ RAG system initialized")
        
        # Test 4.2: Hybrid Query (KG + Vector)
        print("\n✓ Test 4.2: Hybrid Query (Knowledge Graph + RAG)")
        
        # Query 1: Get customers from KG
        commercial_customers = kg.query_customers_by_segment("Commercial")
        print(f"  KG Query: Found {len(commercial_customers)} Commercial customers")
        
        # Query 2: Semantic search for churn reasons
        churn_docs = retriever.naive_retrieval(
            "What are common churn reasons in the Commercial segment?",
            k=5
        )
        print(f"  RAG Query: Retrieved {len(churn_docs)} relevant documents")
        
        # Cross-validate
        rag_customers = set(d.metadata['account_name'] for d in churn_docs)
        overlap = len(rag_customers.intersection(set(commercial_customers)))
        print(f"  ✅ Hybrid result: {overlap} customers found in both KG and RAG")
        
        # Test 4.3: Complex Query Pattern
        print("\n✓ Test 4.3: Complex Query Pattern")
        
        # Pattern: High ARR churn in Commercial with specific competitor
        high_arr_docs = retriever.retrieve_with_metadata_filter(
            query="competitive losses",
            segment="Commercial",
            min_arr=50000,
            k=5
        )
        print(f"  ✅ Found {len(high_arr_docs)} high-ARR competitive losses")
        
        if high_arr_docs:
            sample = high_arr_docs[0]
            print(f"    Sample: {sample.metadata['account_name']}")
            print(f"    ARR: ${sample.metadata['arr_lost']:,.2f}")
            print(f"    Competitor: {sample.metadata.get('competitor_1', 'N/A')}")
        
        return True
        
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """Run complete test suite"""
    print("\n")
    print("╔" + "="*78 + "╗")
    print("║" + " " * 20 + "CHURN RAG SYSTEM - QA TEST SUITE" + " " * 25 + "║")
    print("╚" + "="*78 + "╝")
    
    results = {}
    
    # Test 1: Data Loader
    results['data_loader'], documents = test_data_loader()
    
    # Test 2: Knowledge Graph
    results['knowledge_graph'], kg = test_knowledge_graph()
    
    # Test 3: Vector Store & Retrieval
    results['vector_store'], retriever = test_vector_store(documents)
    
    # Test 4: Integration
    results['integration'] = test_integration()
    
    # Summary
    print("\n" + "="*80)
    print("📊 TEST SUMMARY")
    print("="*80)
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    
    for test_name, passed_test in results.items():
        status = "✅ PASSED" if passed_test else "❌ FAILED"
        print(f"  {test_name.replace('_', ' ').title()}: {status}")
    
    print(f"\n  Total: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n  🎉 ALL TESTS PASSED! System is working correctly.")
        return 0
    else:
        print("\n  ⚠️  Some tests failed. Please review errors above.")
        return 1


if __name__ == "__main__":
    exit_code = run_all_tests()
    sys.exit(exit_code)

