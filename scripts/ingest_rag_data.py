"""
RAG Data Ingestion Script
Loads synthetic data into vector database (Qdrant) with proper chunking and embeddings
"""

import pandas as pd
from pathlib import Path
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, TextLoader, CSVLoader
from langchain.schema import Document
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Qdrant
from qdrant_client import QdrantClient
from qdrant_client.http import models
import os
from typing import List
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DATA_DIR = Path("data")
CHURN_DOCS_DIR = DATA_DIR / "churn_analysis_docs"
COLLECTION_NAME = "churnguard_knowledge"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

class RAGDataIngestion:
    """Handles ingestion of all synthetic data into RAG system"""

    def __init__(self, use_local_qdrant=True):
        """Initialize ingestion with Qdrant client"""
        self.use_local_qdrant = use_local_qdrant

        # Initialize embeddings
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "your_key_here":
            raise ValueError(
                "OPENAI_API_KEY environment variable not set or is placeholder.\n"
                "Please set your actual OpenAI API key in .env file or as environment variable."
            )

        self.embeddings = OpenAIEmbeddings(openai_api_key=api_key)

        # Store path/url for later use
        if use_local_qdrant:
            self.qdrant_path = "./qdrant_storage"
            self.qdrant_url = None
            logger.info("Using local Qdrant storage")
        else:
            # For cloud Qdrant
            self.qdrant_url = os.getenv("QDRANT_URL")
            qdrant_api_key = os.getenv("QDRANT_API_KEY")
            if not self.qdrant_url or not qdrant_api_key:
                raise ValueError("QDRANT_URL and QDRANT_API_KEY required for cloud mode")
            self.qdrant_path = None
            logger.info(f"Using cloud Qdrant at {self.qdrant_url}")

        # Text splitter for chunking
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""]
        )

    def load_churn_analysis_documents(self) -> List[Document]:
        """Load individual churn analysis documents"""
        logger.info(f"Loading churn analysis documents from {CHURN_DOCS_DIR}")

        loader = DirectoryLoader(
            str(CHURN_DOCS_DIR),
            glob="*.txt",
            loader_cls=TextLoader,
            show_progress=True
        )

        documents = loader.load()
        logger.info(f"Loaded {len(documents)} churn analysis documents")

        # Add metadata
        for doc in documents:
            doc.metadata["source_type"] = "churn_analysis"
            doc.metadata["collection"] = "churn_analyses"

        return documents

    def load_success_stories(self) -> List[Document]:
        """Load success stories from CSV"""
        logger.info("Loading success stories")

        df = pd.read_csv(DATA_DIR / "success_stories.csv")
        documents = []

        for _, row in df.iterrows():
            content = row['full_story']

            metadata = {
                "source_type": "success_story",
                "story_id": str(row['story_id']),
                "company_name": str(row['company_name']),
                "segment": str(row['segment']),
                "challenge_category": str(row['challenge_category']),
                "solution": str(row['solution']),
                "arr": float(row['arr']),
                "adoption_improvement": float(row['adoption_after'] - row['adoption_before'])
            }

            doc = Document(page_content=content, metadata=metadata)
            documents.append(doc)

        logger.info(f"Loaded {len(documents)} success stories")
        return documents

    def load_support_tickets(self) -> List[Document]:
        """Load support ticket data"""
        logger.info("Loading support tickets")

        df = pd.read_csv(DATA_DIR / "support_tickets.csv")
        documents = []

        for _, row in df.iterrows():
            # Combine description and resolution notes
            content = f"""
Support Ticket: {row['ticket_id']}
Company: {row['company_name']} ({row['segment']})
Category: {row['category']} - {row['issue_type']}
Severity: {row['severity']}

Description:
{row['description']}

Resolution:
{row['resolution_notes']}

Resolution Time: {row['resolution_hours']} hours
CSAT Score: {row['csat_score']}/5
            """.strip()

            metadata = {
                "source_type": "support_ticket",
                "ticket_id": str(row['ticket_id']),
                "company_name": str(row['company_name']),
                "segment": str(row['segment']),
                "category": str(row['category']),
                "severity": str(row['severity']),
                "resolution_hours": float(row['resolution_hours']),
                "csat_score": float(row['csat_score'])
            }

            doc = Document(page_content=content, metadata=metadata)
            documents.append(doc)

        logger.info(f"Loaded {len(documents)} support tickets")
        return documents

    def load_customer_interactions(self) -> List[Document]:
        """Load customer interaction history"""
        logger.info("Loading customer interactions")

        df = pd.read_csv(DATA_DIR / "customer_interactions.csv")

        # Group interactions by company for better context
        grouped = df.groupby('company_name')
        documents = []

        for company, interactions in grouped:
            # Create summary document for each company
            interaction_list = []
            for _, row in interactions.iterrows():
                interaction_list.append(
                    f"[{row['interaction_date']}] {row['interaction_type']}: {row['content']}"
                )

            content = f"""
Customer Interaction History: {company}
Segment: {interactions.iloc[0]['segment']}
Tenure: {interactions.iloc[0]['customer_tenure_months']} months

Recent Interactions ({len(interaction_list)} total):
{chr(10).join(interaction_list[:10])}  # Show up to 10 most recent
            """.strip()

            metadata = {
                "source_type": "interaction_history",
                "company_name": str(company),
                "segment": str(interactions.iloc[0]['segment']),
                "total_interactions": int(len(interactions)),
                "tenure_months": int(interactions.iloc[0]['customer_tenure_months'])
            }

            doc = Document(page_content=content, metadata=metadata)
            documents.append(doc)

        logger.info(f"Loaded interaction history for {len(documents)} companies")
        return documents

    def chunk_documents(self, documents: List[Document]) -> List[Document]:
        """Split documents into chunks"""
        logger.info(f"Chunking {len(documents)} documents...")

        chunked_docs = self.text_splitter.split_documents(documents)

        logger.info(f"Created {len(chunked_docs)} chunks from {len(documents)} documents")
        return chunked_docs

    def create_vectorstore(self, documents: List[Document]):
        """Create or update Qdrant vectorstore with documents"""
        logger.info(f"Creating vectorstore with {len(documents)} documents...")

        # Create vectorstore - this will handle collection creation
        vectorstore = Qdrant.from_documents(
            documents=documents,
            embedding=self.embeddings,
            path=self.qdrant_path if self.use_local_qdrant else None,
            url=self.qdrant_url if not self.use_local_qdrant else None,
            collection_name=COLLECTION_NAME,
            force_recreate=True  # This will delete and recreate the collection
        )

        logger.info(f"✅ Vectorstore created successfully with collection '{COLLECTION_NAME}'")
        return vectorstore

    def ingest_all(self):
        """Main ingestion pipeline"""
        logger.info("=" * 60)
        logger.info("Starting RAG data ingestion pipeline")
        logger.info("=" * 60)

        all_documents = []

        # Load all data sources
        logger.info("\n📥 Loading documents from all sources...")
        churn_docs = self.load_churn_analysis_documents()
        success_stories = self.load_success_stories()
        support_tickets = self.load_support_tickets()
        interactions = self.load_customer_interactions()

        all_documents.extend(churn_docs)
        all_documents.extend(success_stories)
        all_documents.extend(support_tickets)
        all_documents.extend(interactions)

        logger.info(f"\n📊 Total documents loaded: {len(all_documents)}")
        logger.info(f"  - Churn analyses: {len(churn_docs)}")
        logger.info(f"  - Success stories: {len(success_stories)}")
        logger.info(f"  - Support tickets: {len(support_tickets)}")
        logger.info(f"  - Interaction histories: {len(interactions)}")

        # Chunk documents
        logger.info(f"\n✂️  Chunking documents (size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})...")
        chunked_docs = self.chunk_documents(all_documents)

        # Create vectorstore
        logger.info("\n🔮 Creating embeddings and loading into Qdrant...")
        vectorstore = self.create_vectorstore(chunked_docs)

        logger.info("\n" + "=" * 60)
        logger.info("✨ RAG data ingestion complete!")
        logger.info("=" * 60)
        logger.info(f"\n📁 Vector database location: ./qdrant_storage")
        logger.info(f"🏷️  Collection name: {COLLECTION_NAME}")
        logger.info(f"📄 Total chunks: {len(chunked_docs)}")
        logger.info(f"\n🎯 Next steps:")
        logger.info(f"  1. Test retrieval with sample queries")
        logger.info(f"  2. Integrate with multi-agent system")
        logger.info(f"  3. Create fine-tuning dataset")

        return vectorstore

def main():
    """Run ingestion pipeline"""
    ingestion = RAGDataIngestion(use_local_qdrant=True)
    vectorstore = ingestion.ingest_all()

    # Test retrieval
    logger.info("\n🧪 Testing retrieval...")
    test_query = "How to handle Enterprise customers with pricing concerns?"
    results = vectorstore.similarity_search(test_query, k=3)

    logger.info(f"\nTest Query: '{test_query}'")
    logger.info(f"Retrieved {len(results)} documents:")
    for i, doc in enumerate(results, 1):
        logger.info(f"\n{i}. Source: {doc.metadata.get('source_type', 'unknown')}")
        logger.info(f"   Preview: {doc.page_content[:150]}...")

if __name__ == "__main__":
    main()
