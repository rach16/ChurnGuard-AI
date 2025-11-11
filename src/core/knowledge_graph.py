"""
Knowledge Graph Construction from Customer Churn Data

Extracts entities and relationships from SFDC churned customers data:
- Entities: Customers, Churn Reasons, Competitors, Products, Segments
- Relationships: Customer->Reason, Customer->Competitor, Customer->Product, Customer->Segment

Uses NetworkX for graph structure and enables hybrid retrieval (semantic + graph)
"""

import networkx as nx
import pandas as pd
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set
import logging

logger = logging.getLogger(__name__)


class ChurnKnowledgeGraph:
    """
    Build and query knowledge graph from customer churn data
    """
    
    def __init__(self):
        """Initialize empty knowledge graph"""
        self.graph = nx.DiGraph()  # Directed graph for relationships
        self.entity_types = {
            'Customer': set(),
            'Segment': set(),
            'ChurnReason': set(),
            'Competitor': set(),
            'Product': set()
        }
        
    def build_from_dataframe(self, df: pd.DataFrame) -> None:
        """
        Build knowledge graph from churned customers DataFrame
        
        Args:
            df: DataFrame with churned customer records
        """
        logger.info(f"Building knowledge graph from {len(df)} customer records...")
        
        # Extract and add all entities
        self._extract_customers(df)
        self._extract_segments(df)
        self._extract_churn_reasons(df)
        self._extract_competitors(df)
        self._extract_products(df)
        
        # Build relationships
        self._build_relationships(df)
        
        # Log statistics
        self._log_statistics()
        
    def _extract_customers(self, df: pd.DataFrame) -> None:
        """Extract customer entities with attributes"""
        logger.info("Extracting customer entities...")
        
        for idx, row in df.iterrows():
            customer_id = row['Account Name']
            
            # Clean Amount field (handle both string and float)
            amount = row['Amount']
            if isinstance(amount, str):
                arr_lost = float(amount.replace('$', '').replace(',', ''))
            else:
                arr_lost = float(amount) if pd.notna(amount) else 0.0
            
            # Add customer node with attributes
            self.graph.add_node(
                customer_id,
                type='Customer',
                segment=row['Account Segment'],
                tenure_years=float(row['Tenure (years)']) if pd.notna(row['Tenure (years)']) else 0.0,
                arr_lost=arr_lost,
                churn_date=row['Close Date'],
                first_win_date=row['First Win Date'],
                products=row['Products (Rollup)'] if pd.notna(row['Products (Rollup)']) else '',
                churn_narrative=row.get('Lost Opportunity Details', '') if pd.notna(row.get('Lost Opportunity Details', '')) else ''
            )
            
            self.entity_types['Customer'].add(customer_id)
        
        logger.info(f"✓ Added {len(self.entity_types['Customer'])} customers")
    
    def _extract_segments(self, df: pd.DataFrame) -> None:
        """Extract segment entities"""
        logger.info("Extracting segments...")
        
        segments = df['Account Segment'].unique()
        
        for segment in segments:
            if pd.notna(segment) and segment:
                self.graph.add_node(
                    f"SEGMENT:{segment}",
                    type='Segment',
                    name=segment
                )
                self.entity_types['Segment'].add(segment)
        
        logger.info(f"✓ Added {len(self.entity_types['Segment'])} segments")
    
    def _extract_churn_reasons(self, df: pd.DataFrame) -> None:
        """Extract churn reason entities"""
        logger.info("Extracting churn reasons...")
        
        # Primary reasons
        primary_reasons = df['Primary Outcome Reason'].unique()
        
        for reason in primary_reasons:
            if pd.notna(reason) and reason:
                self.graph.add_node(
                    f"REASON:{reason}",
                    type='ChurnReason',
                    name=reason,
                    reason_type='primary'
                )
                self.entity_types['ChurnReason'].add(reason)
        
        # Sub reasons
        sub_reasons = df['Outcome Sub Reason'].unique()
        
        for sub_reason in sub_reasons:
            if pd.notna(sub_reason) and sub_reason != 'N/A' and sub_reason:
                self.graph.add_node(
                    f"SUBREASON:{sub_reason}",
                    type='ChurnReason',
                    name=sub_reason,
                    reason_type='sub'
                )
                self.entity_types['ChurnReason'].add(sub_reason)
        
        logger.info(f"✓ Added {len(self.entity_types['ChurnReason'])} churn reasons")
    
    def _extract_competitors(self, df: pd.DataFrame) -> None:
        """Extract competitor entities"""
        logger.info("Extracting competitors...")
        
        # Competitor 1
        comp1 = df['Competitor 1'].unique()
        for comp in comp1:
            if pd.notna(comp) and comp and comp not in ['None mentioned', 'N/A', '']:
                self.graph.add_node(
                    f"COMPETITOR:{comp}",
                    type='Competitor',
                    name=comp
                )
                self.entity_types['Competitor'].add(comp)
        
        # Competitor 2
        comp2 = df['Competitor 2'].unique()
        for comp in comp2:
            if pd.notna(comp) and comp and comp not in ['None mentioned', 'N/A', '']:
                self.graph.add_node(
                    f"COMPETITOR:{comp}",
                    type='Competitor',
                    name=comp
                )
                self.entity_types['Competitor'].add(comp)
        
        logger.info(f"✓ Added {len(self.entity_types['Competitor'])} competitors")
    
    def _extract_products(self, df: pd.DataFrame) -> None:
        """Extract product entities from Products (Rollup) field"""
        logger.info("Extracting products...")
        
        for idx, row in df.iterrows():
            products_str = row['Products (Rollup)']
            if pd.notna(products_str) and products_str:
                # Split by common delimiters
                products = [p.strip() for p in products_str.replace(';', ',').split(',')]
                
                for product in products:
                    if product and product not in ['N/A', 'None', '']:
                        self.graph.add_node(
                            f"PRODUCT:{product}",
                            type='Product',
                            name=product
                        )
                        self.entity_types['Product'].add(product)
        
        logger.info(f"✓ Added {len(self.entity_types['Product'])} products")
    
    def _build_relationships(self, df: pd.DataFrame) -> None:
        """Build relationships between entities"""
        logger.info("Building relationships...")
        
        relationship_count = 0
        
        for idx, row in df.iterrows():
            customer_id = row['Account Name']
            
            # Customer -> Segment
            segment = row['Account Segment']
            if pd.notna(segment) and segment:
                self.graph.add_edge(
                    customer_id,
                    f"SEGMENT:{segment}",
                    relationship='BELONGS_TO',
                    type='customer_segment'
                )
                relationship_count += 1
            
            # Customer -> Churn Reason (Primary)
            primary_reason = row['Primary Outcome Reason']
            if pd.notna(primary_reason) and primary_reason:
                self.graph.add_edge(
                    customer_id,
                    f"REASON:{primary_reason}",
                    relationship='CHURNED_DUE_TO',
                    type='customer_reason',
                    churn_date=row['Close Date']
                )
                relationship_count += 1
            
            # Customer -> Churn Reason (Sub)
            sub_reason = row['Outcome Sub Reason']
            if pd.notna(sub_reason) and sub_reason != 'N/A' and sub_reason:
                self.graph.add_edge(
                    customer_id,
                    f"SUBREASON:{sub_reason}",
                    relationship='CHURNED_DUE_TO',
                    type='customer_subreason'
                )
                relationship_count += 1
            
            # Customer -> Competitor 1
            comp1 = row['Competitor 1']
            if pd.notna(comp1) and comp1 not in ['None mentioned', 'N/A', '']:
                self.graph.add_edge(
                    customer_id,
                    f"COMPETITOR:{comp1}",
                    relationship='SWITCHED_TO',
                    type='customer_competitor'
                )
                relationship_count += 1
            
            # Customer -> Competitor 2
            comp2 = row['Competitor 2']
            if pd.notna(comp2) and comp2 not in ['None mentioned', 'N/A', '']:
                self.graph.add_edge(
                    customer_id,
                    f"COMPETITOR:{comp2}",
                    relationship='SWITCHED_TO',
                    type='customer_competitor'
                )
                relationship_count += 1
            
            # Customer -> Products
            products_str = row['Products (Rollup)']
            if pd.notna(products_str) and products_str:
                products = [p.strip() for p in products_str.replace(';', ',').split(',')]
                for product in products:
                    if product and product not in ['N/A', 'None', '']:
                        self.graph.add_edge(
                            customer_id,
                            f"PRODUCT:{product}",
                            relationship='USED_PRODUCT',
                            type='customer_product'
                        )
                        relationship_count += 1
        
        logger.info(f"✓ Built {relationship_count} relationships")
    
    def _log_statistics(self) -> None:
        """Log knowledge graph statistics"""
        logger.info("\n" + "="*80)
        logger.info("📊 KNOWLEDGE GRAPH STATISTICS")
        logger.info("="*80)
        logger.info(f"\n🔹 Entities:")
        logger.info(f"   Total Nodes: {self.graph.number_of_nodes()}")
        for entity_type, entities in self.entity_types.items():
            logger.info(f"   {entity_type}: {len(entities)}")
        
        logger.info(f"\n🔹 Relationships:")
        logger.info(f"   Total Edges: {self.graph.number_of_edges()}")
        
        # Count relationship types
        rel_types = {}
        for _, _, data in self.graph.edges(data=True):
            rel_type = data.get('relationship', 'unknown')
            rel_types[rel_type] = rel_types.get(rel_type, 0) + 1
        
        for rel_type, count in rel_types.items():
            logger.info(f"   {rel_type}: {count}")
        
        logger.info("\n" + "="*80)
    
    def get_entity_by_type(self, entity_type: str) -> List[str]:
        """Get all entities of a specific type"""
        return list(self.entity_types.get(entity_type, set()))
    
    def get_neighbors(self, node_id: str, relationship_type: Optional[str] = None) -> List[Tuple[str, Dict]]:
        """
        Get neighbors of a node, optionally filtered by relationship type
        
        Args:
            node_id: Node identifier
            relationship_type: Optional relationship filter
            
        Returns:
            List of (neighbor_id, edge_data) tuples
        """
        if node_id not in self.graph:
            return []
        
        neighbors = []
        for neighbor in self.graph.neighbors(node_id):
            edge_data = self.graph.get_edge_data(node_id, neighbor)
            
            if relationship_type is None or edge_data.get('relationship') == relationship_type:
                neighbors.append((neighbor, edge_data))
        
        return neighbors
    
    def query_customers_by_reason(self, reason: str) -> List[str]:
        """Find all customers who churned for a specific reason"""
        reason_node = f"REASON:{reason}"
        
        if reason_node not in self.graph:
            return []
        
        # Find all customers pointing to this reason
        customers = [
            node for node in self.graph.predecessors(reason_node)
            if self.graph.nodes[node].get('type') == 'Customer'
        ]
        
        return customers
    
    def query_customers_by_competitor(self, competitor: str) -> List[str]:
        """Find all customers who switched to a specific competitor"""
        comp_node = f"COMPETITOR:{competitor}"
        
        if comp_node not in self.graph:
            return []
        
        customers = [
            node for node in self.graph.predecessors(comp_node)
            if self.graph.nodes[node].get('type') == 'Customer'
        ]
        
        return customers
    
    def query_customers_by_segment(self, segment: str) -> List[str]:
        """Find all customers in a specific segment"""
        segment_node = f"SEGMENT:{segment}"
        
        if segment_node not in self.graph:
            return []
        
        customers = [
            node for node in self.graph.predecessors(segment_node)
            if self.graph.nodes[node].get('type') == 'Customer'
        ]
        
        return customers
    
    def get_churn_patterns(self, segment: str) -> Dict:
        """
        Analyze churn patterns for a specific segment
        
        Returns:
            Dictionary with top reasons, competitors, avg tenure, avg ARR
        """
        customers = self.query_customers_by_segment(segment)
        
        if not customers:
            return {}
        
        reasons = []
        competitors = []
        tenures = []
        arr_values = []
        
        for customer in customers:
            customer_data = self.graph.nodes[customer]
            
            # Get tenure and ARR
            tenures.append(customer_data.get('tenure_years', 0))
            arr_values.append(customer_data.get('arr_lost', 0))
            
            # Get churn reasons
            for neighbor, edge_data in self.get_neighbors(customer, 'CHURNED_DUE_TO'):
                if neighbor.startswith('REASON:'):
                    reasons.append(neighbor.replace('REASON:', ''))
            
            # Get competitors
            for neighbor, edge_data in self.get_neighbors(customer, 'SWITCHED_TO'):
                if neighbor.startswith('COMPETITOR:'):
                    competitors.append(neighbor.replace('COMPETITOR:', ''))
        
        # Aggregate statistics
        from collections import Counter
        
        return {
            'segment': segment,
            'customer_count': len(customers),
            'top_reasons': dict(Counter(reasons).most_common(5)),
            'top_competitors': dict(Counter(competitors).most_common(5)),
            'avg_tenure': sum(tenures) / len(tenures) if tenures else 0,
            'avg_arr_lost': sum(arr_values) / len(arr_values) if arr_values else 0,
            'total_arr_lost': sum(arr_values)
        }
    
    def save_graph(self, filepath: str) -> None:
        """Save knowledge graph to file"""
        import pickle
        with open(filepath, 'wb') as f:
            pickle.dump(self.graph, f)
        logger.info(f"✅ Knowledge graph saved to {filepath}")
    
    def load_graph(self, filepath: str) -> None:
        """Load knowledge graph from file"""
        import pickle
        with open(filepath, 'rb') as f:
            self.graph = pickle.load(f)
        
        # Rebuild entity_types
        for node, data in self.graph.nodes(data=True):
            entity_type = data.get('type')
            if entity_type and entity_type in self.entity_types:
                if entity_type == 'Customer':
                    self.entity_types[entity_type].add(node)
                else:
                    self.entity_types[entity_type].add(data.get('name', node))
        
        logger.info(f"✅ Knowledge graph loaded from {filepath}")


def build_churn_knowledge_graph(data_folder: str = "data/") -> ChurnKnowledgeGraph:
    """
    Build knowledge graph from churned customers CSV
    
    Args:
        data_folder: Path to data folder
        
    Returns:
        ChurnKnowledgeGraph instance
    """
    logger.info("🔨 Building Churn Knowledge Graph...")
    
    # Load data
    data_path = Path(data_folder) / 'churned_customers_cleaned.csv'
    df = pd.read_csv(data_path)
    
    # Build graph
    kg = ChurnKnowledgeGraph()
    kg.build_from_dataframe(df)
    
    logger.info("✅ Knowledge Graph construction complete!")
    
    return kg


if __name__ == "__main__":
    # Test knowledge graph construction
    logging.basicConfig(level=logging.INFO)
    
    kg = build_churn_knowledge_graph()
    
    # Test queries
    print("\n🔍 Testing Knowledge Graph Queries:")
    print("="*80)
    
    # Query by segment
    commercial = kg.query_customers_by_segment("Commercial")
    print(f"\n📊 Commercial Segment: {len(commercial)} customers")
    
    # Analyze patterns
    patterns = kg.get_churn_patterns("Commercial")
    print(f"\n📈 Commercial Churn Patterns:")
    print(f"   Avg Tenure: {patterns['avg_tenure']:.2f} years")
    print(f"   Avg ARR Lost: ${patterns['avg_arr_lost']:,.2f}")
    print(f"   Top Reasons: {list(patterns['top_reasons'].items())[:3]}")
    print(f"   Top Competitors: {list(patterns['top_competitors'].items())[:3]}")
    
    # Save graph
    kg.save_graph("cache/churn_knowledge_graph.pkl")

