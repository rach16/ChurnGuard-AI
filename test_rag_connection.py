"""Quick test to verify RAG connection"""
import sys
sys.path.insert(0, 'src')

from core.rag_helper import get_rag_retriever

print("Testing RAG connection...")
retriever = get_rag_retriever()

if retriever.is_available():
    print("\n✅ RAG is available!")

    # Test a query
    print("\nTesting query: 'Enterprise customer with pricing concerns'")
    results = retriever.retrieve_context("Enterprise customer with pricing concerns", k=3)

    print(f"\nRetrieved {len(results)} results:")
    for i, result in enumerate(results, 1):
        print(f"\n{i}. Source: {result['source_type']}")
        print(f"   Preview: {result['content'][:100]}...")
else:
    print("\n❌ RAG is not available")
