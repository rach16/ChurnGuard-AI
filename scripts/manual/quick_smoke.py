#!/usr/bin/env python3
"""
Quick 60-second smoke test for Churn RAG System
Tests basic functionality without requiring OpenAI API
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

print("\n🚀 Quick Smoke Test - Churn RAG System\n")

# Test 1: Data Loader
print("1️⃣ Testing Data Loader...")
try:
    from src.utils.data_loader import ChurnDataLoader
    loader = ChurnDataLoader("data/")
    documents = loader.load_churned_customers_documents()
    print(f"   ✅ Loaded {len(documents)} documents")
    print(f"   ✅ Sample: {documents[0].metadata['account_name']} (${documents[0].metadata['arr_lost']:,.2f})")
except Exception as e:
    print(f"   ❌ FAILED: {e}")
    sys.exit(1)

# Test 2: Knowledge Graph
print("\n2️⃣ Testing Knowledge Graph...")
try:
except Exception as e:
    print(f"   ❌ FAILED: {e}")
    sys.exit(1)

# Test 3: Qdrant Connection
print("\n3️⃣ Testing Qdrant Connection...")
try:
    from qdrant_client import QdrantClient
    client = QdrantClient(url="http://localhost:6333")
    collections = client.get_collections()
    print(f"   ✅ Qdrant accessible ({len(collections.collections)} collections)")
except Exception as e:
    print(f"   ⚠️  Qdrant not accessible: {e}")
    print(f"   💡 Start with: docker-compose up -d")

# Test 4: Implementation Files
print("\n4️⃣ Checking Implementation Files...")
files = {
    "RAG Retrievers": "src/core/rag_retrievers.py",
    "Data Loader": "src/utils/data_loader.py",
}

for name, filepath in files.items():
    path = Path(filepath)
    if path.exists():
        size = path.stat().st_size
        if filepath.endswith('.py'):
            lines = len(path.read_text().split('\n'))
            print(f"   ✅ {name}: {lines} lines")
        else:
            print(f"   ✅ {name}: {size} bytes")
    else:
        print(f"   ❌ {name}: NOT FOUND")

print("\n" + "="*60)
print("✅ SMOKE TEST COMPLETE!")
print("="*60)
print("\n💡 Next steps:")
print("   1. For full testing: python3 tests/test_rag_system.py")
print("   2. For Jupyter testing: See tests/MANUAL_QA_CHECKLIST.md")
print("   3. For RAG retrieval test: Needs OpenAI API key in .env")
print("\n")

