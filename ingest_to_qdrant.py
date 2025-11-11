#!/usr/bin/env python3
"""
Quick RAG Data Ingestion Script
Loads text documents into Qdrant
"""

import os
import sys
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
import logging

# Load environment
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
COLLECTION_NAME = "churnguard_knowledge"
# Use qdrant service name in Docker, or localhost for host machine
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
DATA_DIR = Path("data/churn_analysis_docs")
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

def main():
    """Main ingestion function"""
    
    # Check API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or "your" in api_key.lower():
        print("❌ Error: Valid OPENAI_API_KEY not set in .env file")
        return 1
    
    print("🚀 Starting RAG data ingestion...")
    print(f"   Target: {QDRANT_URL}")
    print(f"   Collection: {COLLECTION_NAME}")
    
    # Initialize clients
    try:
        embeddings = OpenAIEmbeddings(openai_api_key=api_key)
        client = QdrantClient(url=QDRANT_URL)
        print("✅ Connected to Qdrant")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        return 1
    
    # Check if collection exists
    collections = client.get_collections().collections
    collection_names = [c.name for c in collections]
    
    if COLLECTION_NAME in collection_names:
        print(f"⚠️  Collection '{COLLECTION_NAME}' already exists. Deleting...")
        client.delete_collection(COLLECTION_NAME)
    
    # Create collection
    print("📦 Creating collection...")
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=1536, distance=Distance.COSINE)
    )
    print("✅ Collection created")
    
    # Load and process documents
    if not DATA_DIR.exists():
        print(f"❌ Data directory not found: {DATA_DIR}")
        return 1
    
    txt_files = list(DATA_DIR.glob("*.txt"))
    if not txt_files:
        print(f"❌ No .txt files found in {DATA_DIR}")
        return 1
    
    print(f"📄 Found {len(txt_files)} text documents")
    
    # Initialize text splitter
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len
    )
    
    # Process documents
    all_chunks = []
    for txt_file in txt_files:
        try:
            with open(txt_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            chunks = text_splitter.split_text(content)
            for chunk in chunks:
                all_chunks.append({
                    "text": chunk,
                    "source": txt_file.name,
                    "doc_type": "churn_analysis"
                })
        except Exception as e:
            print(f"⚠️  Error processing {txt_file.name}: {e}")
    
    print(f"📊 Created {len(all_chunks)} chunks from documents")
    
    # Generate embeddings and upload
    print("🔄 Generating embeddings (this may take a minute)...")
    
    batch_size = 50
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i:i+batch_size]
        texts = [chunk["text"] for chunk in batch]
        
        try:
            # Generate embeddings
            vectors = embeddings.embed_documents(texts)
            
            # Create points
            points = []
            for j, (chunk, vector) in enumerate(zip(batch, vectors)):
                point = PointStruct(
                    id=i + j,
                    vector=vector,
                    payload={
                        "text": chunk["text"],
                        "source": chunk["source"],
                        "doc_type": chunk["doc_type"]
                    }
                )
                points.append(point)
            
            # Upload to Qdrant
            client.upsert(
                collection_name=COLLECTION_NAME,
                points=points
            )
            
            print(f"   ✅ Uploaded batch {i//batch_size + 1}/{(len(all_chunks)-1)//batch_size + 1}")
        
        except Exception as e:
            print(f"   ⚠️  Error in batch {i//batch_size + 1}: {e}")
    
    # Verify
    collection_info = client.get_collection(COLLECTION_NAME)
    print(f"\n🎉 Ingestion complete!")
    print(f"   Collection: {COLLECTION_NAME}")
    print(f"   Total vectors: {collection_info.points_count}")
    print(f"   Vector size: {collection_info.config.params.vectors.size}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

