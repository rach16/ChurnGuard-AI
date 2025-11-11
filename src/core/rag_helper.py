"""
RAG Helper Module
Provides RAG retrieval functionality for the API
"""

import os
from typing import List, Dict, Optional
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Qdrant
from qdrant_client import QdrantClient

# Load environment variables
load_dotenv()

class RAGRetriever:
    """Handles RAG retrieval from Qdrant vector database"""

    def __init__(self, collection_name: str = "churnguard_knowledge", qdrant_url: str = None):
        """Initialize RAG retriever"""
        self.collection_name = collection_name
        # Use Docker network URL if not specified
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "http://qdrant:6333")
        self.vectorstore = None
        self._initialize()

    def _initialize(self):
        """Initialize the vector store connection"""
        try:
            # Check if API key is configured
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key or api_key == "your_key_here":
                print("⚠️  RAG disabled: OPENAI_API_KEY not configured")
                return

            # Initialize embeddings and client (connect to Qdrant server)
            embeddings = OpenAIEmbeddings(openai_api_key=api_key)
            client = QdrantClient(url=self.qdrant_url)

            # Check if collection exists
            try:
                collections = client.get_collections()
                collection_names = [c.name for c in collections.collections]
                if self.collection_name not in collection_names:
                    print(f"⚠️  RAG disabled: Collection '{self.collection_name}' not found")
                    print(f"   Available collections: {collection_names}")
                    print("   Run 'python3 scripts/ingest_rag_data_server.py' to create it")
                    return
            except Exception as e:
                print(f"⚠️  RAG disabled: Cannot connect to Qdrant server at {self.qdrant_url}")
                print(f"   Error: {e}")
                print("   Make sure Qdrant is running: docker start churn-qdrant")
                return

            # Create vectorstore
            self.vectorstore = Qdrant(
                client=client,
                collection_name=self.collection_name,
                embeddings=embeddings
            )

            print(f"✅ RAG initialized with collection '{self.collection_name}' at {self.qdrant_url}")

        except Exception as e:
            print(f"⚠️  RAG initialization failed: {e}")
            self.vectorstore = None

    def is_available(self) -> bool:
        """Check if RAG system is available"""
        return self.vectorstore is not None

    def retrieve_context(self, query: str, k: int = 5) -> List[Dict]:
        """
        Retrieve relevant context from RAG system

        Args:
            query: Search query
            k: Number of results to retrieve

        Returns:
            List of context documents with metadata
        """
        if not self.is_available():
            return []

        try:
            # Perform similarity search
            results = self.vectorstore.similarity_search(query, k=k)

            # Format results
            context_docs = []
            for doc in results:
                context_docs.append({
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                    "source_type": doc.metadata.get("source_type", "unknown")
                })

            return context_docs

        except Exception as e:
            print(f"❌ RAG retrieval error: {e}")
            return []

    def retrieve_with_scores(self, query: str, k: int = 5) -> List[Dict]:
        """
        Retrieve relevant context with similarity scores

        Args:
            query: Search query
            k: Number of results to retrieve

        Returns:
            List of context documents with scores and metadata
        """
        if not self.is_available():
            return []

        try:
            # Perform similarity search with scores
            results = self.vectorstore.similarity_search_with_score(query, k=k)

            # Format results
            context_docs = []
            for doc, score in results:
                context_docs.append({
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                    "source_type": doc.metadata.get("source_type", "unknown"),
                    "relevance_score": float(score)
                })

            return context_docs

        except Exception as e:
            print(f"❌ RAG retrieval error: {e}")
            return []

    def format_context_for_prompt(self, context_docs: List[Dict], max_docs: int = 3) -> str:
        """
        Format retrieved context for inclusion in AI prompt

        Args:
            context_docs: List of context documents
            max_docs: Maximum number of documents to include

        Returns:
            Formatted context string
        """
        if not context_docs:
            return ""

        # Limit to max_docs
        docs_to_use = context_docs[:max_docs]

        # Build context string
        context_parts = ["**Relevant Historical Context:**\n"]

        for i, doc in enumerate(docs_to_use, 1):
            source_type = doc['source_type']
            content = doc['content'][:500]  # Limit content length

            # Add source type label
            if source_type == "churn_analysis":
                label = "📊 Similar Churn Case"
            elif source_type == "success_story":
                label = "✅ Success Story"
            elif source_type == "support_ticket":
                label = "🎫 Support Ticket"
            elif source_type == "interaction_history":
                label = "💬 Customer Interaction"
            else:
                label = "📄 Reference"

            context_parts.append(f"\n{i}. {label}:")
            context_parts.append(f"{content}...")
            context_parts.append("")

        return "\n".join(context_parts)


# Global RAG retriever instance
_rag_retriever: Optional[RAGRetriever] = None


def get_rag_retriever() -> RAGRetriever:
    """Get or create global RAG retriever instance"""
    global _rag_retriever
    if _rag_retriever is None:
        _rag_retriever = RAGRetriever()
    return _rag_retriever
