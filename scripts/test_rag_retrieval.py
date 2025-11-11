"""
Test RAG Retrieval System
Demonstrates querying the vector database with various customer scenarios
"""

import os
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Qdrant
from qdrant_client import QdrantClient

# Load environment variables
load_dotenv()

# Configuration
COLLECTION_NAME = "churnguard_knowledge"
QDRANT_PATH = "./qdrant_storage"

def test_rag_queries():
    """Test various queries against the RAG system"""

    print("=" * 70)
    print("ChurnGuard AI - RAG Retrieval Test")
    print("=" * 70)
    print()

    # Initialize embeddings
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "your_key_here":
        print("❌ Error: OPENAI_API_KEY not configured")
        return

    embeddings = OpenAIEmbeddings(openai_api_key=api_key)

    # Initialize Qdrant client
    client = QdrantClient(path=QDRANT_PATH)

    # Create vectorstore
    vectorstore = Qdrant(
        client=client,
        collection_name=COLLECTION_NAME,
        embeddings=embeddings
    )

    print(f"✅ Connected to Qdrant")
    print(f"📁 Collection: {COLLECTION_NAME}")
    print(f"📍 Location: {QDRANT_PATH}")
    print()

    # Test queries
    test_queries = [
        {
            "scenario": "Enterprise Customer - Pricing Concerns",
            "query": "How to handle Enterprise customers with pricing concerns?",
            "k": 3
        },
        {
            "scenario": "SMB Customer - Low Adoption",
            "query": "What strategies work for SMB customers struggling with product adoption?",
            "k": 3
        },
        {
            "scenario": "Support Issues",
            "query": "How to resolve high support ticket volume and customer dissatisfaction?",
            "k": 3
        },
        {
            "scenario": "Success Stories - Retention",
            "query": "Show me examples of successful customer retention strategies",
            "k": 3
        },
        {
            "scenario": "Customer Interaction Patterns",
            "query": "What are common interaction patterns for at-risk customers?",
            "k": 3
        }
    ]

    for i, test in enumerate(test_queries, 1):
        print(f"\n{'='*70}")
        print(f"Test Query #{i}: {test['scenario']}")
        print(f"{'='*70}")
        print(f"Query: \"{test['query']}\"")
        print()

        # Perform similarity search
        results = vectorstore.similarity_search(test['query'], k=test['k'])

        print(f"📊 Retrieved {len(results)} documents:")
        print()

        for j, doc in enumerate(results, 1):
            source_type = doc.metadata.get('source_type', 'unknown')

            print(f"  {j}. [{source_type.upper()}]")

            # Show relevant metadata based on source type
            if source_type == 'churn_analysis':
                print(f"     Collection: {doc.metadata.get('collection', 'N/A')}")
            elif source_type == 'success_story':
                print(f"     Company: {doc.metadata.get('company_name', 'N/A')}")
                print(f"     Segment: {doc.metadata.get('segment', 'N/A')}")
                print(f"     Challenge: {doc.metadata.get('challenge_category', 'N/A')}")
                print(f"     ARR: ${doc.metadata.get('arr', 0):,.0f}")
            elif source_type == 'support_ticket':
                print(f"     Company: {doc.metadata.get('company_name', 'N/A')}")
                print(f"     Category: {doc.metadata.get('category', 'N/A')}")
                print(f"     Severity: {doc.metadata.get('severity', 'N/A')}")
                print(f"     CSAT: {doc.metadata.get('csat_score', 'N/A')}/5")
            elif source_type == 'interaction_history':
                print(f"     Company: {doc.metadata.get('company_name', 'N/A')}")
                print(f"     Segment: {doc.metadata.get('segment', 'N/A')}")
                print(f"     Total Interactions: {doc.metadata.get('total_interactions', 'N/A')}")

            # Show content preview
            content_preview = doc.page_content[:200].replace('\n', ' ')
            print(f"     Preview: {content_preview}...")
            print()

    print("=" * 70)
    print("✨ RAG Retrieval Test Complete!")
    print("=" * 70)
    print()
    print("🎯 Next Steps:")
    print("  1. Integrate RAG retrieval into multi-agent analysis")
    print("  2. Use retrieved context to enhance AI responses")
    print("  3. Build conversational interface with RAG")
    print()

def test_with_score():
    """Test retrieval with similarity scores"""

    print("\n" + "=" * 70)
    print("Testing Retrieval with Similarity Scores")
    print("=" * 70)
    print()

    api_key = os.getenv("OPENAI_API_KEY")
    embeddings = OpenAIEmbeddings(openai_api_key=api_key)
    client = QdrantClient(path=QDRANT_PATH)

    vectorstore = Qdrant(
        client=client,
        collection_name=COLLECTION_NAME,
        embeddings=embeddings
    )

    query = "Commercial customer facing onboarding challenges"
    print(f"Query: \"{query}\"")
    print()

    # Search with scores
    results = vectorstore.similarity_search_with_score(query, k=5)

    print(f"📊 Top 5 Results with Similarity Scores:")
    print()

    for i, (doc, score) in enumerate(results, 1):
        source_type = doc.metadata.get('source_type', 'unknown')
        company = doc.metadata.get('company_name', 'N/A')

        print(f"  {i}. Score: {score:.4f} | Type: {source_type} | Company: {company}")
        print(f"     Preview: {doc.page_content[:150].replace(chr(10), ' ')}...")
        print()

if __name__ == "__main__":
    # Run basic tests
    test_rag_queries()

    # Run test with scores
    test_with_score()
