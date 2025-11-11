"""
Data Loading Utilities
Load and preprocess customer churn data from multiple sources
"""

import os
from pathlib import Path
from typing import List, Dict, Optional
import pandas as pd
from langchain.schema import Document
import logging

logger = logging.getLogger(__name__)


class ChurnDataLoader:
    """
    Load and preprocess customer churn data
    
    Supported formats:
    - CSV (customer records, transaction data)
    - PDF (reports, policies, analysis documents)
    - TXT (feedback, notes, communications)
    """
    
    def __init__(self, data_folder: str = "data/"):
        """Initialize data loader with data folder path"""
        self.data_folder = Path(data_folder)
        
        if not self.data_folder.exists():
            raise ValueError(f"Data folder not found: {data_folder}")
    
    def load_csv_data(self, filename: str) -> pd.DataFrame:
        """
        Load CSV data file
        
        Args:
            filename: CSV filename in data folder
        
        Returns:
            Pandas DataFrame
        """
        filepath = self.data_folder / filename
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        
        return pd.read_csv(filepath)
    
    def load_pdf_documents(self) -> List[Dict]:
        """
        Load all PDF documents from data folder
        
        Returns:
            List of document dictionaries with content and metadata
        """
        # TODO: Implement PDF loading
        # Use PyMuPDF or similar to extract text
        pass
    
    def load_text_files(self) -> List[Dict]:
        """
        Load all text files from data folder
        
        Returns:
            List of document dictionaries
        """
        # TODO: Implement text file loading
        pass
    
    def preprocess_churned_customers(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Preprocess churned customer data for RAG
        
        Args:
            df: Raw churned customers DataFrame
        
        Returns:
            Preprocessed DataFrame with text representations
        """
        logger.info(f"Preprocessing {len(df)} churned customer records...")
        
        # Clean amount field (remove $ and commas)
        df['Amount_Clean'] = df['Amount'].str.replace('$', '').str.replace(',', '').astype(float)
        
        # Fill missing values
        df['Outcome Sub Reason'] = df['Outcome Sub Reason'].fillna('N/A')
        df['Competitor 1'] = df['Competitor 1'].fillna('None mentioned')
        df['Competitor 2'] = df['Competitor 2'].fillna('None mentioned')
        df['Lost Opportunity Details'] = df['Lost Opportunity Details'].fillna('No details provided')
        
        # Create rich text representation for RAG
        df['text_representation'] = df.apply(
            lambda row: f"""Customer Churn Profile
======================
Company: {row['Account Name']}
Segment: {row['Account Segment']}
Churn Date: {row['Close Date']}
Lost ARR: {row['Amount']}
Customer Tenure: {row['Tenure (years)']} years
First Win Date: {row['First Win Date']}

Churn Analysis
--------------
Primary Reason: {row['Primary Outcome Reason']}
Sub Reason: {row['Outcome Sub Reason']}

Competitive Intelligence
------------------------
Competitor 1: {row['Competitor 1']}
Competitor 2: {row['Competitor 2']}

Products Used
-------------
{row['Products (Rollup)']}

Detailed Churn Story
-------------------
{row['Lost Opportunity Details']}""",
            axis=1
        )
        
        logger.info(f"✓ Created text representations with avg length: {df['text_representation'].str.len().mean():.0f} chars")
        
        return df
    
    def preprocess_customer_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Legacy method - redirects to appropriate preprocessor
        """
        # Check which type of data this is
        if 'Lost Opportunity Details' in df.columns:
            return self.preprocess_churned_customers(df)
        else:
            # Generic preprocessing for other data types
            return df
    
    def convert_to_documents(self, df: pd.DataFrame) -> List[Document]:
        """
        Convert preprocessed DataFrame to LangChain Document objects
        
        Args:
            df: Preprocessed DataFrame with text_representation column
        
        Returns:
            List of LangChain Document objects
        """
        documents = []
        
        for idx, row in df.iterrows():
            # Create metadata for filtering and retrieval
            metadata = {
                "account_name": row['Account Name'],
                "segment": row['Account Segment'],
                "churn_reason": row['Primary Outcome Reason'],
                "churn_sub_reason": row['Outcome Sub Reason'],
                "churn_date": row['Close Date'],
                "tenure_years": float(row['Tenure (years)']),
                "arr_lost": float(row['Amount_Clean']),
                "competitor_1": row['Competitor 1'],
                "competitor_2": row['Competitor 2'],
                "first_win_date": row['First Win Date'],
                "source": "churned_customers_cleaned.csv",
                "record_id": idx
            }
            
            # Create Document object
            doc = Document(
                page_content=row['text_representation'],
                metadata=metadata
            )
            documents.append(doc)
        
        logger.info(f"✓ Converted {len(documents)} records to LangChain Document objects")
        return documents
    
    def load_churned_customers_documents(self, filename: str = 'churned_customers_cleaned.csv') -> List[Document]:
        """
        Complete pipeline: Load, preprocess, and convert churned customers to documents
        
        Args:
            filename: CSV filename in data folder
        
        Returns:
            List of LangChain Document objects ready for embedding
        """
        logger.info(f"Loading churned customers from {filename}...")
        
        # Load CSV
        df = self.load_csv_data(filename)
        logger.info(f"✓ Loaded {len(df)} records")
        
        # Preprocess
        df = self.preprocess_churned_customers(df)
        
        # Convert to documents
        documents = self.convert_to_documents(df)
        
        logger.info(f"✅ Successfully processed {len(documents)} churned customer documents")
        return documents
    
    def get_all_documents(self) -> List[Document]:
        """
        Load all documents from data folder
        
        Returns:
            Combined list of all documents
        """
        documents = []
        
        # Load churned customers (primary dataset)
        try:
            churned_docs = self.load_churned_customers_documents()
            documents.extend(churned_docs)
            logger.info(f"✓ Added {len(churned_docs)} churned customer documents")
        except Exception as e:
            logger.error(f"Error loading churned customers: {e}")
        
        # TODO: Add pulse history documents
        # TODO: Add PDF documents if available
        # TODO: Add text files if available
        
        logger.info(f"✅ Total documents loaded: {len(documents)}")
        return documents


def load_churn_data():
    """
    Convenience function to load all churn data
    
    Returns:
        DataLoader instance with all data loaded
    """
    loader = ChurnDataLoader()
    return loader

