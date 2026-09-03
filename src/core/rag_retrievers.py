"""
RAG Retrieval Implementations
Multiple retrieval strategies for customer churn analysis
"""

import hashlib
import os
import sys
import time
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

# The metadata field ParentDocumentRetriever uses to point a child chunk at its
# parent. Hard-coded in LangChain as "doc_id"; named here so the hand-rolled
# indexer and the retriever cannot silently drift apart.
PARENT_ID_KEY = "doc_id"

# Initialize logger
logger = logging.getLogger(__name__)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")

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

        # A hosted cluster needs a key; a local container does not, and passing
        # None to a local one is harmless. Without this, pointing QDRANT_URL at
        # Qdrant Cloud failed authentication with no obvious cause.
        api_key = os.getenv("QDRANT_API_KEY") or None
        # The client default is 5 seconds, which is generous for a query and not
        # enough for a bulk upsert: rebuilding the index writes 2,682 vectors in
        # batches and a free-tier cluster regularly takes longer than that per
        # batch. The symptom is a write timeout partway through indexing, which
        # leaves the collection half-populated -- worse than either outcome.
        client = QdrantClient(url=qdrant_url, api_key=api_key, timeout=120)
        try:
            client.get_collections()
        except Exception as e:
            hint = (
                "Start it with 'docker compose up -d qdrant', or set "
                "QDRANT_URL=:memory: to run without a server."
            )
            if not api_key and "cloud.qdrant.io" in qdrant_url:
                hint = "This looks like Qdrant Cloud; QDRANT_API_KEY is not set."
            raise RuntimeError(f"Cannot reach Qdrant at {qdrant_url}: {e}\n{hint}") from e
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

        # Reuse a populated collection rather than rebuilding it. Only meaningful
        # for a hosted or on-disk store -- ':memory:' always starts empty, so this
        # is a no-op there and needs no special case.
        #
        # The check is a count, not a checksum: if the corpus changes, the index
        # is stale and REINDEX_ON_START=true forces a rebuild. A silent partial
        # rebuild would be worse than either.
        if not _env_truthy("REINDEX_ON_START"):
            existing = self._existing_point_count()
            if existing > 0:
                self._bind_existing_collection()
                logger.info(
                    f"✓ Reusing existing index: {existing} vectors in "
                    f"'{self.collection_name}' (set REINDEX_ON_START=true to rebuild)"
                )
                return len(self.documents)

        # Initialize vector store (empty initially - parent retriever will populate it)
        self._init_empty_vector_store()
        
        # Initialize parent document retriever once. The parents are split here
        # rather than by the retriever so their ids can be derived from their own
        # text -- see _split_parents for why that matters.
        logger.info("Setting up parent document retriever...")
        parents, parent_ids = self._split_parents()
        self.parent_retriever = ParentDocumentRetriever(
            vectorstore=self.vector_store,
            docstore=self.parent_store,
            child_splitter=self.child_splitter,
            id_key=PARENT_ID_KEY,
        )

        # Add documents to parent retriever (creates parent-child relationships)
        logger.info("Adding documents to parent retriever (creating parent-child chunks)...")
        self._index_children(parents, parent_ids)
        self.parent_store.mset(list(zip(parent_ids, parents)))
        logger.info(
            f"✓ Parent document retriever initialized with {len(parents)} parents "
            f"from {len(self.documents)} documents"
        )
        
        logger.info("✅ Vector store created and documents indexed")
        
        return len(self.documents)
    
    def readiness_problem(self, sample: int = 8) -> Optional[str]:
        """Why parent_document retrieval could not serve, or None if it can.

        /ready used to report this component as fine whenever the retriever
        object existed. It existed all through the outage of 2026-09-02: the
        object was constructed, the vector store was bound, and parent_retriever
        inside it was None, so every /ask raised while every health check stayed
        green. Checking for existence proved only that startup ran.

        So this checks the join that actually has to hold: take a few child
        chunks from the collection and confirm the parent id each one carries
        resolves to a document in the docstore. That is the invariant a restart
        breaks, and it catches both failures in that family -- an unpopulated
        docstore, and ids that have drifted from the ones already indexed.

        Costs nothing: one Qdrant scroll, no embedding and no LLM call, so it is
        safe to call on every probe.
        """
        if self.vector_store is None:
            return "vector store not bound"
        if self.parent_retriever is None:
            return "parent retriever not initialized"

        try:
            points, _ = self.client.scroll(
                collection_name=self.collection_name,
                limit=sample,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as e:
            return f"cannot read collection '{self.collection_name}': {e}"

        if not points:
            return f"collection '{self.collection_name}' is empty"

        ids = []
        for point in points:
            payload = point.payload or {}
            metadata = payload.get("metadata") or {}
            parent_id = metadata.get(PARENT_ID_KEY)
            if parent_id is None:
                return f"indexed chunk carries no {PARENT_ID_KEY}"
            ids.append(parent_id)

        resolved = self.parent_store.mget(ids)
        missing = sum(1 for doc in resolved if doc is None)
        if missing:
            return (
                f"{missing} of {len(ids)} sampled chunks point at a parent that "
                f"is not in the docstore -- the index and the docstore disagree"
            )
        return None

    def _index_children(self, parents, parent_ids, batch_size: int = 32,
                        attempts: int = 4) -> int:
        """Embed and upsert the child chunks, in batches, with retries.

        This does by hand what ParentDocumentRetriever.add_documents does
        internally, for one reason: that method exposes no batch size and no
        retry, and the hosted free-tier cluster times out partway through a
        2,682-vector rebuild at its default batch of 64. A half-written
        collection is the worst outcome available here -- it looks populated to
        the reuse check, so the next boot skips the rebuild and serves from a
        fraction of the corpus without ever reporting a problem.
        """
        children = []
        for parent, parent_id in zip(parents, parent_ids):
            for child in self.child_splitter.split_documents([parent]):
                child.metadata[PARENT_ID_KEY] = parent_id
                children.append(child)

        logger.info(f"Indexing {len(children)} child chunks in batches of {batch_size}...")
        for start in range(0, len(children), batch_size):
            batch = children[start:start + batch_size]
            for attempt in range(1, attempts + 1):
                try:
                    self.vector_store.add_documents(batch, batch_size=batch_size)
                    break
                except Exception as e:
                    if attempt == attempts:
                        raise RuntimeError(
                            f"Failed to index children {start}-{start + len(batch)} "
                            f"after {attempts} attempts; collection "
                            f"'{self.collection_name}' is now partially built and "
                            f"must be rebuilt with REINDEX_ON_START=true"
                        ) from e
                    wait = 2 ** attempt
                    logger.warning(
                        f"Batch at {start} failed ({type(e).__name__}), "
                        f"retry {attempt}/{attempts - 1} in {wait}s"
                    )
                    time.sleep(wait)
        logger.info(f"✓ Indexed {len(children)} child chunks")
        return len(children)

    def _split_parents(self):
        """Split the corpus into parent chunks whose ids survive a restart.

        ParentDocumentRetriever normally mints a random UUID per parent. That is
        fine while the docstore and the index live in the same process, and it
        breaks the moment the index outlives the process -- which is exactly what
        moving to a hosted Qdrant did. The child vectors persist, still carrying
        the UUID of their parent, but the parent store is memory and comes back
        empty, so every one of those ids now resolves to nothing.

        Deriving the id from the parent's own text makes the mapping
        reproducible: the same corpus always yields the same ids, so a fresh
        process can rebuild the docstore locally and match what is already
        indexed, without embedding anything.

        The ordinal is part of the hash so two parents with identical text (a
        boilerplate paragraph repeated across accounts) still get distinct ids
        instead of silently collapsing into one.
        """
        parents = self.parent_splitter.split_documents(self.documents)
        ids = []
        for ordinal, parent in enumerate(parents):
            basis = "|".join([
                str(ordinal),
                str(parent.metadata.get("source_type", "")),
                str(parent.metadata.get("customer_id", "")),
                parent.page_content,
            ])
            ids.append(hashlib.sha256(basis.encode("utf-8")).hexdigest())
        return parents, ids

    def _existing_point_count(self) -> int:
        """How many vectors the collection already holds, 0 if it does not exist.

        Startup used to drop and rebuild the collection unconditionally, which is
        correct for an ephemeral in-process store and wasteful for a hosted one:
        the index survives between restarts, and re-embedding 771 documents on
        every wake was most of a measured 66-second cold start, plus about a cent
        of embeddings each time.
        """
        try:
            info = self.client.get_collection(self.collection_name)
            return int(info.points_count or 0)
        except Exception:
            return 0

    def _bind_existing_collection(self) -> None:
        """Attach to a populated collection, embedding nothing.

        The parent docstore is rebuilt here rather than left empty. Splitting
        text is local and costs nothing; only the child vectors cost anything to
        produce, and those are precisely what the collection already holds. An
        earlier version of this method left parent_retriever as None, which made
        the saved startup time useless: /ask defaults to parent_document
        retrieval, so every question raised instead of answering.
        """
        self.vector_store = QdrantVectorStore(
            client=self.client,
            collection_name=self.collection_name,
            embedding=self.embeddings,
        )
        self.parent_store = InMemoryStore()
        self.parent_splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200)
        self.child_splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50)

        parents, parent_ids = self._split_parents()
        self.parent_store.mset(list(zip(parent_ids, parents)))
        self.parent_retriever = ParentDocumentRetriever(
            vectorstore=self.vector_store,
            docstore=self.parent_store,
            child_splitter=self.child_splitter,
            id_key=PARENT_ID_KEY,
        )
        logger.info(f"✓ Rebuilt parent docstore: {len(parents)} parents, 0 embeddings")

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

