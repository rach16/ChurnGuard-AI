"""
RAG Retrieval Implementations
Multiple retrieval strategies for customer churn analysis
"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Dict
import logging

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_qdrant import QdrantVectorStore
from langchain.retrievers import ContextualCompressionRetriever, ParentDocumentRetriever
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor
from langchain.storage import InMemoryStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.schema import Document
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

# Cohere reranking
try:
    from langchain_cohere import CohereRerank
    COHERE_AVAILABLE = True
except ImportError:
    COHERE_AVAILABLE = False
    logger.warning("langchain-cohere not available. Reranking will fall back to contextual compression.")

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from utils.data_loader import ChurnDataLoader

logger = logging.getLogger(__name__)


class ChurnRAGRetriever:
    """
    Customer Churn RAG Retriever with multiple retrieval strategies
    
    Implements:
    - Naive retrieval: Basic similarity search
    - Multi-query retrieval: Generates multiple query variations for better recall
    - Contextual compression: Uses LLM filtering to extract relevant content
    - Parent-document retrieval: Balances precision with context
    - Reranking (Cohere): Uses Cohere to reorder results by relevance
    """
    
    def __init__(
        self,
        collection_name: str = "customer_churn",
        qdrant_url: Optional[str] = None
    ):
        """Initialize the retriever with Qdrant connection"""
        self.collection_name = collection_name
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")
        
        logger.info(f"Initializing Churn RAG Retriever with Qdrant at {self.qdrant_url}")
        
        # Initialize embeddings
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        
        # Initialize LLM for query generation and compression
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        
        # Initialize Qdrant client
        self.client = QdrantClient(url=self.qdrant_url)
        
        # Vector store (will be initialized after loading documents)
        self.vector_store = None
        self.documents = []
        
        # Parent document storage
        self.parent_store = InMemoryStore()
        self.parent_splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200)
        self.child_splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50)
        self.parent_retriever = None  # Will be initialized after loading documents
        
    def load_and_process_documents(self, data_folder: str = "data/"):
        """
        Load documents from data folder and create vector embeddings
        
        Args:
            data_folder: Path to data folder with CSV files
        """
        logger.info(f"Loading documents from {data_folder}...")
        
        # Load churned customer documents using data loader
        data_loader = ChurnDataLoader(data_folder)
        self.documents = data_loader.load_churned_customers_documents()
        
        logger.info(f"✓ Loaded {len(self.documents)} customer churn documents")
        
        # Initialize vector store (empty initially - parent retriever will populate it)
        self._init_empty_vector_store()
        
        # Initialize parent document retriever once
        logger.info("Setting up parent document retriever...")
        self.parent_retriever = ParentDocumentRetriever(
            vectorstore=self.vector_store,
            docstore=self.parent_store,
            child_splitter=self.child_splitter,
            parent_splitter=self.parent_splitter,
        )
        
        # Add documents to parent retriever (creates parent-child relationships)
        logger.info("Adding documents to parent retriever (creating parent-child chunks)...")
        self.parent_retriever.add_documents(self.documents)
        logger.info(f"✓ Parent document retriever initialized with {len(self.documents)} documents")
        
        logger.info("✅ Vector store created and documents indexed")
        
        return len(self.documents)
    
    def _init_empty_vector_store(self):
        """Initialize empty Qdrant vector store for parent retriever"""
        logger.info("Initializing empty Qdrant vector store...")
        
        # Check if collection exists, delete if it does
        try:
            self.client.delete_collection(collection_name=self.collection_name)
            logger.info(f"Deleted existing collection: {self.collection_name}")
        except Exception:
            pass
        
        # Create collection with empty documents to initialize structure
        # ParentDocumentRetriever will then add documents properly
        self.vector_store = QdrantVectorStore.from_documents(
            documents=[],  # Empty - parent retriever will populate
            embedding=self.embeddings,
            url=self.qdrant_url,
            collection_name=self.collection_name,
        )
        
        logger.info(f"✓ Initialized empty vector store collection: {self.collection_name}")
    
    def _create_vector_store(self):
        """Create Qdrant vector store and index documents (for non-parent retrievers)"""
        logger.info("Creating Qdrant vector store...")
        
        # Check if collection exists, recreate if it does
        try:
            self.client.delete_collection(collection_name=self.collection_name)
            logger.info(f"Deleted existing collection: {self.collection_name}")
        except Exception:
            pass
        
        # Create vector store with documents
        self.vector_store = QdrantVectorStore.from_documents(
            documents=self.documents,
            embedding=self.embeddings,
            url=self.qdrant_url,
            collection_name=self.collection_name,
            force_recreate=True
        )
        
        logger.info(f"✓ Created collection '{self.collection_name}' with {len(self.documents)} documents")
    
    def naive_retrieval(self, query: str, k: int = 5, filters: Optional[Dict] = None) -> List[Document]:
        """
        Basic similarity search retrieval
        
        Args:
            query: Search query
            k: Number of documents to retrieve
            filters: Optional metadata filters (e.g., {"segment": "Commercial"})
        
        Returns:
            List of relevant documents
        """
        if not self.vector_store:
            raise ValueError("Vector store not initialized. Call load_and_process_documents() first.")
        
        logger.info(f"Naive retrieval for query: '{query}' (k={k})")
        
        # Perform similarity search
        if filters:
            docs = self.vector_store.similarity_search(
                query=query,
                k=k,
                filter=filters
            )
        else:
            docs = self.vector_store.similarity_search(query=query, k=k)
        
        logger.info(f"✓ Retrieved {len(docs)} documents")
        return docs
    
    def multi_query_retrieval(self, query: str, k: int = 5) -> List[Document]:
        """
        Multi-query retrieval for diverse perspectives
        
        Generates multiple query variations to retrieve diverse results
        
        Args:
            query: Original search query
            k: Number of documents to retrieve per query
        
        Returns:
            List of unique relevant documents
        """
        if not self.vector_store:
            raise ValueError("Vector store not initialized. Call load_and_process_documents() first.")
        
        logger.info(f"Multi-query retrieval for: '{query}'")
        
        # Create base retriever
        base_retriever = self.vector_store.as_retriever(search_kwargs={"k": k})
        
        # Create multi-query retriever
        multi_query_retriever = MultiQueryRetriever.from_llm(
            retriever=base_retriever,
            llm=self.llm
        )
        
        # Retrieve documents
        docs = multi_query_retriever.invoke(query)
        
        logger.info(f"✓ Retrieved {len(docs)} unique documents from multiple queries")
        return docs
    
    def contextual_compression_retrieval(self, query: str, k: int = 5) -> List[Document]:
        """
        Contextual compression for focused results
        
        Compresses retrieved documents to most relevant portions using LLM
        
        Args:
            query: Search query
            k: Number of initial documents to retrieve
        
        Returns:
            List of compressed documents with only relevant content
        """
        if not self.vector_store:
            raise ValueError("Vector store not initialized. Call load_and_process_documents() first.")
        
        logger.info(f"Contextual compression retrieval for: '{query}'")
        
        # Create base retriever
        base_retriever = self.vector_store.as_retriever(search_kwargs={"k": k})
        
        # Create compressor
        compressor = LLMChainExtractor.from_llm(self.llm)
        
        # Create compression retriever
        compression_retriever = ContextualCompressionRetriever(
            base_compressor=compressor,
            base_retriever=base_retriever
        )
        
        # Retrieve and compress
        docs = compression_retriever.invoke(query)
        
        logger.info(f"✓ Retrieved and compressed {len(docs)} documents")
        return docs
    
    def parent_document_retrieval(self, query: str, k: int = 5) -> List[Document]:
        """
        Parent document retrieval for full context
        
        Retrieves small chunks for precision but returns full parent documents for context
        
        Args:
            query: Search query
            k: Number of parent documents to retrieve
        
        Returns:
            List of full parent documents
        """
        if not self.parent_retriever:
            raise ValueError("Parent document retriever not initialized. Call load_and_process_documents() first.")
        
        logger.info(f"Parent document retrieval for: '{query}'")
        
        # Retrieve using pre-initialized parent retriever
        # Use get_relevant_documents for compatibility with ParentDocumentRetriever
        try:
            docs = self.parent_retriever.get_relevant_documents(query)[:k]
        except AttributeError:
            # Fallback to invoke if get_relevant_documents doesn't exist
            docs = self.parent_retriever.invoke(query)[:k]
        
        logger.info(f"✓ Retrieved {len(docs)} parent documents")
        return docs
    
    def retrieve_with_metadata_filter(
        self, 
        query: str, 
        segment: Optional[str] = None,
        min_arr: Optional[float] = None,
        max_arr: Optional[float] = None,
        churn_reason: Optional[str] = None,
        k: int = 5
    ) -> List[Document]:
        """
        Retrieve documents with metadata filtering
        
        Args:
            query: Search query
            segment: Filter by customer segment
            min_arr: Minimum ARR lost
            max_arr: Maximum ARR lost
            churn_reason: Filter by churn reason
            k: Number of documents
        
        Returns:
            Filtered documents
        """
        filters = {}
        
        if segment:
            filters["segment"] = segment
        if churn_reason:
            filters["churn_reason"] = churn_reason
        if min_arr:
            filters["arr_lost"] = {"$gte": min_arr}
        if max_arr:
            if "arr_lost" in filters:
                filters["arr_lost"]["$lte"] = max_arr
            else:
                filters["arr_lost"] = {"$lte": max_arr}
        
        return self.naive_retrieval(query, k=k, filters=filters if filters else None)
    
    def rerank_retrieval(self, query: str, k: int = 5) -> List[Document]:
        """
        Reranking retrieval using Cohere Rerank
        
        Retrieves more documents initially, then reranks them using Cohere's
        reranking model to select the most relevant k documents.
        
        Args:
            query: Search query
            k: Number of final documents to return after reranking
        
        Returns:
            List of top-k reranked documents
        """
        if not self.vector_store:
            raise ValueError("Vector store not initialized. Call load_and_process_documents() first.")
        
        logger.info(f"Reranking retrieval for: '{query}'")
        
        # Check if Cohere is available and API key is set
        if not COHERE_AVAILABLE or not os.getenv("COHERE_API_KEY"):
            logger.warning("Cohere not available or API key not set. Falling back to contextual compression.")
            return self.contextual_compression_retrieval(query, k=k)
        
        try:
            # Create base retriever that fetches more documents
            base_retriever = self.vector_store.as_retriever(search_kwargs={"k": k * 3})
            
            # Create Cohere reranker
            compressor = CohereRerank(
                model="rerank-english-v3.0",
                top_n=k,
                cohere_api_key=os.getenv("COHERE_API_KEY")
            )
            
            # Create compression retriever with reranker
            rerank_retriever = ContextualCompressionRetriever(
                base_compressor=compressor,
                base_retriever=base_retriever
            )
            
            # Retrieve and rerank
            docs = rerank_retriever.invoke(query)
            
            logger.info(f"✓ Retrieved and reranked {len(docs)} documents")
            return docs
            
        except Exception as e:
            logger.error(f"Reranking failed: {e}. Falling back to contextual compression.")
            return self.contextual_compression_retrieval(query, k=k)
    
    def get_stats(self) -> Dict:
        """Get retriever statistics"""
        return {
            "total_documents": len(self.documents),
            "collection_name": self.collection_name,
            "qdrant_url": self.qdrant_url,
            "vector_store_initialized": self.vector_store is not None
        }


def initialize_churn_rag_system(data_folder: str = "data/"):
    """
    Initialize the complete RAG system
    
    Args:
        data_folder: Path to data folder
    
    Returns:
        Configured ChurnRAGRetriever instance with loaded documents
    """
    logger.info("🚀 Initializing Churn RAG System...")
    
    retriever = ChurnRAGRetriever()
    doc_count = retriever.load_and_process_documents(data_folder)
    
    logger.info(f"✅ RAG System initialized with {doc_count} documents")
    logger.info(f"📊 Stats: {retriever.get_stats()}")
    
    return retriever


if __name__ == "__main__":
    # Test the RAG system
    import logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    # Check if Qdrant is running
    try:
        test_client = QdrantClient(url="http://localhost:6333")
        collections = test_client.get_collections()
        print("✅ Qdrant is accessible")
    except Exception as e:
        print(f"❌ Qdrant not accessible: {e}")
        print("Please start Qdrant using Docker: docker-compose up -d qdrant")
        sys.exit(1)
    
    # Initialize system
    retriever = initialize_churn_rag_system()
    
    # Test query
    test_query = "What are the main reasons customers in the Commercial segment churn?"
    
    print("\n" + "="*80)
    print("🧪 TESTING RETRIEVAL METHODS")
    print("="*80)
    print(f"\nTest Query: {test_query}\n")
    
    # Test 1: Naive Retrieval
    print("\n1️⃣ NAIVE RETRIEVAL:")
    print("-" * 80)
    naive_docs = retriever.naive_retrieval(test_query, k=3)
    for i, doc in enumerate(naive_docs, 1):
        print(f"\n  Doc {i}: {doc.metadata.get('account_name', 'Unknown')}")
        print(f"    Segment: {doc.metadata.get('segment', 'N/A')}")
        print(f"    Reason: {doc.metadata.get('churn_reason', 'N/A')}")
        print(f"    ARR Lost: ${doc.metadata.get('arr_lost', 0):,.2f}")
    
    # Test 2: Multi-Query Retrieval
    print("\n\n2️⃣ MULTI-QUERY RETRIEVAL:")
    print("-" * 80)
    try:
        multi_docs = retriever.multi_query_retrieval(test_query, k=3)
        print(f"  Retrieved {len(multi_docs)} unique documents across multiple query variations")
        for i, doc in enumerate(multi_docs[:3], 1):
            print(f"\n  Doc {i}: {doc.metadata.get('account_name', 'Unknown')}")
            print(f"    Segment: {doc.metadata.get('segment', 'N/A')}")
    except Exception as e:
        print(f"  ⚠️ Multi-query retrieval failed: {e}")
    
    # Test 3: Contextual Compression
    print("\n\n3️⃣ CONTEXTUAL COMPRESSION RETRIEVAL:")
    print("-" * 80)
    try:
        compressed_docs = retriever.contextual_compression_retrieval(test_query, k=3)
        print(f"  Retrieved and compressed {len(compressed_docs)} documents")
        if compressed_docs:
            print(f"\n  Sample compressed content (first doc):")
            print(f"  {compressed_docs[0].page_content[:200]}...")
    except Exception as e:
        print(f"  ⚠️ Contextual compression failed: {e}")
    
    # Test 4: Metadata Filtering
    print("\n\n4️⃣ METADATA FILTERING (Commercial Segment):")
    print("-" * 80)
    filtered_docs = retriever.retrieve_with_metadata_filter(
        query=test_query,
        segment="Commercial",
        k=3
    )
    print(f"  Retrieved {len(filtered_docs)} documents from Commercial segment")
    for i, doc in enumerate(filtered_docs, 1):
        print(f"\n  Doc {i}: {doc.metadata.get('account_name', 'Unknown')}")
        print(f"    Segment: {doc.metadata.get('segment', 'N/A')}")
        print(f"    Reason: {doc.metadata.get('churn_reason', 'N/A')}")
    
    print("\n\n" + "="*80)
    print("✅ ALL RETRIEVAL METHODS TESTED")
    print("="*80)

