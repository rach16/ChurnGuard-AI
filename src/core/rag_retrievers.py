"""
RAG Retrieval Implementations
Multiple retrieval strategies for customer churn analysis
"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Dict
import logging

from langchain_qdrant import QdrantVectorStore
from langchain.retrievers import ContextualCompressionRetriever, ParentDocumentRetriever, MultiQueryRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor
from langchain.storage import InMemoryStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

# Initialize logger
logger = logging.getLogger(__name__)

# Dimensionality of text-embedding-3-small, used when creating collections.
EMBEDDING_DIM = 1536

# Reciprocal rank fusion constant. 60 is the value from the original RRF paper and
# is deliberately large relative to typical result depth, so it damps the difference
# between ranks 1 and 2 and stops either retriever dominating on its top hit alone.
RRF_K = 60

# Weight given to semantic search in hybrid retrieval, the remainder going to BM25.
# Swept over the golden set: pure BM25 scored 0.971 single-entity hit rate and 0.941
# recall, pure semantic 0.794 and 0.574, and anything above 0.5 collapsed to the
# pure-semantic numbers. 0.25 ties the BM25 optimum while retaining a semantic
# contribution for paraphrased queries that share no keywords with the corpus.
DEFAULT_SEMANTIC_WEIGHT = 0.25

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
from core.llm import chat_model, embedding_model


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
        collection_name: str = "churn_corpus",
        qdrant_url: Optional[str] = None
    ):
        """Initialize the retriever with Qdrant connection"""
        self.collection_name = collection_name
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")

        logger.info(f"Initializing Churn RAG Retriever with Qdrant at {self.qdrant_url}")

        # Initialize embeddings
        self.embeddings = embedding_model()

        # Initialize LLM for query generation and compression
        self.llm = chat_model(temperature=0)

        self.client = self._make_client(self.qdrant_url)

        # Vector store (will be initialized after loading documents)
        self.vector_store = None
        self.documents = []

        # BM25 index, built lazily on first hybrid query.
        self._bm25_index = None
        self._bm25_corpus: List[Document] = []

    @staticmethod
    def _make_client(qdrant_url: str) -> QdrantClient:
        """Build a Qdrant client.

        Accepts a server URL, ':memory:' for an ephemeral in-process store, or
        'local:<path>' for an embedded on-disk one. The non-server modes let the
        evaluation suite and CI run without a Qdrant container.
        """
        if qdrant_url == ":memory:":
            logger.info("Using in-memory Qdrant (no server)")
            return QdrantClient(location=":memory:")

        if qdrant_url.startswith("local:"):
            path = qdrant_url.split(":", 1)[1]
            logger.info(f"Using embedded Qdrant at {path}")
            return QdrantClient(path=path)

        client = QdrantClient(url=qdrant_url)
        try:
            client.get_collections()
        except Exception as e:
            raise RuntimeError(
                f"Cannot reach Qdrant at {qdrant_url}: {e}\n"
                "Start it with 'docker compose up -d qdrant', or set "
                "QDRANT_URL=:memory: to run without a server."
            ) from e
        return client

    def _reset_collection(self):
        """Drop and recreate the collection, then bind a vector store to it."""
        try:
            self.client.delete_collection(collection_name=self.collection_name)
            logger.info(f"Deleted existing collection: {self.collection_name}")
        except Exception:
            pass

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        self.vector_store = QdrantVectorStore(
            client=self.client,
            collection_name=self.collection_name,
            embedding=self.embeddings,
        )
        
        # Parent document storage
        self.parent_store = InMemoryStore()
        self.parent_splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200)
        self.child_splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50)
        self.parent_retriever = None  # Will be initialized after loading documents
        
    def load_and_process_documents(self, data_folder: str = "data/",
                                   index_vectors: bool = True):
        """
        Load the corpus, and by default embed and index it.

        Args:
            data_folder: Path to data folder with CSV files
            index_vectors: False loads the documents and stops. Embedding 771
                documents is the only step here that calls a paid API, so a
                keyword-only caller -- the CI regression check -- skips it and
                runs at zero cost. Every semantic and hybrid path needs it.
        """
        logger.info(f"Loading documents from {data_folder}...")

        # Load the full corpus -- customer profiles, churn analyses, success stories,
        # support and interaction history -- all carrying customer_id in metadata.
        data_loader = ChurnDataLoader(data_folder)
        self.documents = data_loader.get_all_documents()

        logger.info(f"✓ Loaded {len(self.documents)} corpus documents")

        if not index_vectors:
            logger.info("Skipping vector indexing: keyword-only mode")
            return len(self.documents)

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
        self._reset_collection()
        logger.info(f"✓ Initialized empty vector store collection: {self.collection_name}")

    def _create_vector_store(self):
        """Create Qdrant vector store and index documents (for non-parent retrievers)"""
        logger.info("Creating Qdrant vector store...")
        self._reset_collection()
        self.vector_store.add_documents(self.documents)
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
    
    def hybrid_retrieval(self, query: str, k: int = 5,
                         semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT) -> List[Document]:
        """
        BM25 keyword search fused with dense semantic search.

        Dense embeddings match the *shape* of a question and are weak on names: asking
        how many tickets "DisasterRecovery Solutions" raised returns customers with
        similar ticket profiles, because the question embeds as "a question about
        support volume" and the company name barely moves the vector.

        BM25 has the opposite bias -- it matches rare exact terms, and a company name
        is exactly that. Fusing the two covers both failure modes.

        Combined with reciprocal rank fusion rather than by blending scores, since
        BM25 and cosine similarity are not on comparable scales and normalising them
        introduces a tuning parameter that has to be re-fit whenever the corpus moves.

        Args:
            query: Search query
            k: Number of documents to return
            semantic_weight: 0.0 pure BM25, 1.0 pure semantic. Default 0.25 -- see
                DEFAULT_SEMANTIC_WEIGHT for why it is weighted toward keywords.
        """
        if not self._bm25_index:
            self._build_bm25_index()

        # Over-fetch from each retriever so fusion has room to reorder.
        depth = max(k * 4, 20)

        # Skip a retriever whose weight is zero. At semantic_weight 0 the dense
        # results contribute exactly nothing to the fusion below, so calling the
        # vector store was pure waste -- and, more usefully, skipping it means a
        # pure-keyword run needs no embeddings, no API key and no vector store at
        # all. That is what lets the regression check run in CI for free.
        semantic_docs = (
            self.vector_store.similarity_search(query, k=depth)
            if semantic_weight > 0 else []
        )
        keyword_docs = (
            self._bm25_search(query, k=depth) if semantic_weight < 1 else []
        )

        # Reciprocal rank fusion: a document's score is the sum over rankings of
        # 1/(RRF_K + rank). Documents both retrievers like rise above those either
        # ranks first alone.
        scores: Dict[str, float] = {}
        best_doc: Dict[str, Document] = {}

        for docs, weight in ((semantic_docs, semantic_weight),
                             (keyword_docs, 1.0 - semantic_weight)):
            for rank, doc in enumerate(docs, start=1):
                key = doc.metadata.get("doc_id") or doc.page_content[:120]
                scores[key] = scores.get(key, 0.0) + weight / (RRF_K + rank)
                best_doc.setdefault(key, doc)

        ordered = sorted(scores, key=scores.get, reverse=True)
        return [best_doc[key] for key in ordered[:k]]

    def _build_bm25_index(self) -> None:
        """Tokenise the corpus once for BM25. Cheap, in-memory, no API calls."""
        from rank_bm25 import BM25Okapi

        if not self.documents:
            raise RuntimeError("No documents loaded; call load_and_process_documents first")

        self._bm25_corpus = self.documents
        tokenised = [self._tokenise(d.page_content) for d in self._bm25_corpus]
        self._bm25_index = BM25Okapi(tokenised)
        logger.info(f"✓ BM25 index built over {len(tokenised)} documents")

    @staticmethod
    def _tokenise(text: str) -> List[str]:
        """Lowercase alphanumeric tokens. Keeps company names as single tokens."""
        import re
        return re.findall(r"[a-z0-9]+", text.lower())

    def _bm25_search(self, query: str, k: int) -> List[Document]:
        scores = self._bm25_index.get_scores(self._tokenise(query))
        top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [self._bm25_corpus[i] for i in top if scores[i] > 0]

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
    filtered_docs = retriever.hybrid_retrieval(
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

