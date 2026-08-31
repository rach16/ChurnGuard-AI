"""
Data Loading Utilities
Load and preprocess customer churn data from multiple sources
"""

import os
from pathlib import Path
from typing import List, Dict, Optional
import pandas as pd
from langchain_core.documents import Document
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
    
    def load_corpus_documents(self) -> List[Document]:
        """
        Load the full RAG corpus from the current dataset.

        Every document carries `customer_id`, so retrieval can be scored against the
        golden dataset's expected_context and joined back to the customer dimension.

        Produces five document types:
          customer_profile   one per customer, holding the facts the eval questions ask about
          churn_analysis     the per-customer analysis write-ups
          success_story      retention case studies
          support_history    that customer's tickets, collapsed into one document
          interaction_history that customer's touchpoints, collapsed into one document

        Returns:
            List of LangChain Document objects ready for embedding
        """
        customers = self.load_csv_data("customers.csv")
        tickets = self.load_csv_data("support_tickets.csv")
        interactions = self.load_csv_data("customer_interactions.csv")
        snapshots = self.load_csv_data("engagement_snapshots.csv")
        analyses = self.load_csv_data("churn_analyses.csv")
        stories = self.load_csv_data("success_stories.csv")

        # Latest weekly snapshot per customer; the generator writes them chronologically.
        latest = snapshots.groupby("customer_id").last()

        ticket_stats = tickets.groupby("customer_id").agg(
            ticket_count=("ticket_id", "count"), mean_csat=("csat_score", "mean")
        )

        documents: List[Document] = []

        def base_meta(row, source_type: str, doc_id: str) -> Dict:
            return {
                "customer_id": row["customer_id"],
                "company_name": row["company_name"],
                "segment": row["segment"],
                "source_type": source_type,
                "doc_id": doc_id,
            }

        # 1. Customer profiles -------------------------------------------------------
        for _, c in customers.iterrows():
            cid = c["customer_id"]
            eng = latest.loc[cid] if cid in latest.index else None
            tix = ticket_stats.loc[cid] if cid in ticket_stats.index else None

            churn_line = (
                f"Status: CHURNED on {c['churn_date']} -- category {c['churn_category']}, "
                f"specifically \"{c['specific_reason']}\"."
                if c["is_churned"] == 1
                else f"Status: ACTIVE, contract runs to {c['contract_end_date']}."
            )
            competitor = c["competitor"] if isinstance(c["competitor"], str) and c["competitor"] else "None recorded"

            content = f"""Customer Profile: {c['company_name']} ({cid})
======================================================
Segment: {c['segment']}
Industry: {c['industry']}
Region: {c['region']}
ARR: ${int(c['arr']):,}
Seats: {c['seats']}
Tenure: {c['tenure_months']} months (started {c['contract_start_date']})
{churn_line}
Competitor named: {competitor}

Engagement
----------
Latest engagement score: {eng['engagement_score'] if eng is not None else 'n/a'}
Latest feature adoption rate: {eng['feature_adoption_rate'] if eng is not None else 'n/a'}
Active users in latest week: {eng['active_users'] if eng is not None else 'n/a'}

Support
-------
Total tickets raised: {int(tix['ticket_count']) if tix is not None else 0}
Average CSAT: {f"{tix['mean_csat']:.2f} / 5" if tix is not None else 'no tickets'}"""

            meta = base_meta(c, "customer_profile", f"PROFILE-{cid}")
            meta.update({
                "arr": int(c["arr"]),
                "is_churned": int(c["is_churned"]),
                "tenure_months": int(c["tenure_months"]),
                "industry": c["industry"],
            })
            documents.append(Document(page_content=content, metadata=meta))

        # 2. Churn analyses ----------------------------------------------------------
        for _, a in analyses.iterrows():
            meta = base_meta(a, "churn_analysis", a["doc_id"])
            meta.update({"churn_category": a["churn_category"], "risk_score": int(a["risk_score"])})
            documents.append(Document(page_content=a["document"], metadata=meta))

        # 3. Success stories ---------------------------------------------------------
        for _, s in stories.iterrows():
            meta = base_meta(s, "success_story", s["story_id"])
            meta.update({"challenge_category": s["challenge_category"]})
            documents.append(Document(page_content=s["full_story"], metadata=meta))

        # 4. Support history, one document per customer ------------------------------
        for cid, group in tickets.groupby("customer_id"):
            first = group.iloc[0]
            lines = [
                f"- [{r['created_date'][:10]}] {r['severity']} / {r['category']}: {r['issue_type']}. "
                f"Resolved in {r['resolution_hours']}h. CSAT {r['csat_score']}/5."
                for _, r in group.iterrows()
            ]
            content = (
                f"Support History: {first['company_name']} ({cid})\n"
                f"{len(group)} tickets, average CSAT {group['csat_score'].mean():.2f}/5\n\n"
                + "\n".join(lines)
            )
            documents.append(Document(
                page_content=content,
                metadata=base_meta(first, "support_history", f"SUPPORT-{cid}"),
            ))

        # 5. Interaction history, one document per customer ---------------------------
        for cid, group in interactions.groupby("customer_id"):
            first = group.iloc[0]
            lines = [
                f"- [{r['interaction_date']}] {r['interaction_type']}: {r['content']}"
                for _, r in group.sort_values("interaction_date", ascending=False).iterrows()
            ]
            content = (
                f"Interaction History: {first['company_name']} ({cid})\n"
                f"{len(group)} recorded touchpoints\n\n" + "\n".join(lines)
            )
            documents.append(Document(
                page_content=content,
                metadata=base_meta(first, "interaction_history", f"INTERACTIONS-{cid}"),
            ))

        by_type: Dict[str, int] = {}
        for d in documents:
            by_type[d.metadata["source_type"]] = by_type.get(d.metadata["source_type"], 0) + 1
        logger.info(f"✅ Loaded {len(documents)} corpus documents: {by_type}")

        return documents

    def get_all_documents(self) -> List[Document]:
        """
        Load all documents from data folder

        Prefers the current dataset; falls back to the legacy churned-customers export
        if customers.csv is not present.

        Returns:
            Combined list of all documents
        """
        try:
            return self.load_corpus_documents()
        except FileNotFoundError as e:
            logger.warning(f"Current dataset not found ({e}); falling back to legacy export")

        documents = []
        try:
            churned_docs = self.load_churned_customers_documents()
            documents.extend(churned_docs)
            logger.info(f"✓ Added {len(churned_docs)} churned customer documents")
        except Exception as e:
            logger.error(f"Error loading churned customers: {e}")

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

