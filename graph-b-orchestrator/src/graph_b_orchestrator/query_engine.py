"""
Query Engine Module - Project Helix Graph B Orchestrator

The "Brain" that retrieves data from both Graph A (Structure) and Graph B (History).
Implements hybrid search combining vector similarity with graph traversal.

Key Features:
- Hybrid Search: Vector similarity + linked file retrieval
- Reality Check: Validate entity existence in Graph A
- Unified querying across both graphs
"""

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Optional, Sequence

import structlog
from neo4j import Driver, GraphDatabase

from .embeddings import generate_embedding
from .schemas import FileInfo, FileStructure, FunctionInfo

logger = structlog.get_logger()

# Query configuration constants (avoid magic numbers)
EPISODE_QUERY_LIMIT = 50  # Max episodes per search
SUMMARY_QUERY_LIMIT = 20  # Max summaries per search
ENTITY_CHECK_LIMIT = 5    # Max matches per entity validation
LRU_CACHE_SIZE = 128      # Embedding cache size


# Cache query embeddings to avoid regenerating for repeated queries
# Maxsize=128 provides good balance between memory and cache hits
@lru_cache(maxsize=LRU_CACHE_SIZE)
def _cached_query_embedding(query: str) -> tuple[float, ...]:
    """Generate and cache embedding for a query string.
    
    Returns tuple for hashability (LRU cache requirement).
    """
    embedding = generate_embedding(query)
    return tuple(embedding)


@dataclass
class SearchResult:
    """A single search result from hybrid search."""
    
    node_type: str  # "Episode" or "Summary"
    node_id: str
    text: str
    timestamp: str
    score: float
    linked_files: list[str]
    linked_functions: list[str]


@dataclass
class RealityCheckResult:
    """Result of validating an entity's existence."""
    
    entity_name: str
    status: str  # "EXISTS", "MISSING", "MULTIPLE"
    node_type: Optional[str] = None
    file_path: Optional[str] = None


class QueryEngine:
    """
    Unified query engine for Graph A (Structure) and Graph B (History).
    
    Provides hybrid search combining:
    - Vector similarity search on Episode/Summary embeddings
    - Graph traversal to linked File/Function nodes
    - Reality validation against current code structure
    """
    
    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "",
        password: str = "",
    ) -> None:
        self.uri = uri
        self.user = user
        self.password = password
        self._driver: Optional[Driver] = None
        
    def connect(self) -> None:
        """Establish connection to Memgraph with optimized pool settings."""
        logger.info("Query Engine connecting to Memgraph", uri=self.uri)
        
        auth = (self.user, self.password) if self.user else None
        
        # Configure connection pool for optimal performance
        self._driver = GraphDatabase.driver(
            self.uri,
            auth=auth,
            max_connection_pool_size=50,  # Max concurrent connections
            connection_acquisition_timeout=30.0,  # Wait time for connection
            connection_timeout=10.0,  # Connection establishment timeout
        )
        self._driver.verify_connectivity()
        
        logger.info(
            "Query Engine connected",
            pool_size=50,
        )
        
    def close(self) -> None:
        """Close the database connection."""
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Query Engine disconnected")
            
    @property
    def driver(self) -> Driver:
        """Get the database driver."""
        if not self._driver:
            raise RuntimeError("QueryEngine not connected. Call connect() first.")
        return self._driver
    
    def hybrid_search(
        self,
        query: str,
        limit: int = 5,
        include_summaries: bool = True,
    ) -> list[SearchResult]:
        """
        Perform hybrid search combining vector similarity with graph traversal.
        
        This searches both Episode and Summary nodes using the query embedding,
        then follows relationships to retrieve linked Files and Functions.
        
        Args:
            query: The search query (natural language)
            limit: Maximum number of results to return
            include_summaries: Whether to include Summary nodes
            
        Returns:
            List of SearchResult with linked code structure
        """
        logger.info("Performing hybrid search", query=query[:50], limit=limit)
        
        # Generate embedding for the query (cached for repeated queries)
        query_embedding = _cached_query_embedding(query)
        
        results: list[SearchResult] = []
        
        with self.driver.session() as session:
            # Search Episodes with vector similarity
            # Note: Memgraph doesn't have native vector search, so we fetch all and compute in Python
            # In production, use a vector index or Memgraph's MAGE algorithms
            
            episode_query = """
            MATCH (e:Episode)-[:AFFECTS]->(f:File)
            WHERE e.embedding IS NOT NULL
            OPTIONAL MATCH (f)-[:DEFINES]->(fn:Function)
            RETURN e.id AS episode_id,
                   e.diff_summary AS text,
                   e.timestamp AS timestamp,
                   e.embedding AS embedding,
                   collect(DISTINCT f.path) AS files,
                   collect(DISTINCT fn.name) AS functions
            LIMIT $episode_limit
            """
            
            episode_result = session.run(
                episode_query,
                episode_limit=EPISODE_QUERY_LIMIT,
            )
            
            for record in episode_result:
                embedding = record["embedding"]
                if embedding:
                    score = self._cosine_similarity(query_embedding, embedding)
                    
                    results.append(SearchResult(
                        node_type="Episode",
                        node_id=record["episode_id"],
                        text=record["text"] or "",
                        timestamp=record["timestamp"] or "",
                        score=score,
                        linked_files=record["files"] or [],
                        linked_functions=[f for f in (record["functions"] or []) if f],
                    ))
            
            # Search Summaries if enabled
            if include_summaries:
                summary_query = """
                MATCH (s:Summary)-[:AFFECTS]->(f:File)
                WHERE s.embedding IS NOT NULL
                OPTIONAL MATCH (f)-[:DEFINES]->(fn:Function)
                RETURN s.id AS summary_id,
                       s.text AS text,
                       s.created_at AS timestamp,
                       s.embedding AS embedding,
                       s.episode_count AS episode_count,
                       collect(DISTINCT f.path) AS files,
                       collect(DISTINCT fn.name) AS functions
                LIMIT $summary_limit
                """
                
                summary_result = session.run(
                    summary_query,
                    summary_limit=SUMMARY_QUERY_LIMIT,
                )
                
                for record in summary_result:
                    embedding = record["embedding"]
                    if embedding:
                        score = self._cosine_similarity(query_embedding, embedding)
                        
                        results.append(SearchResult(
                            node_type="Summary",
                            node_id=record["summary_id"],
                            text=record["text"] or "",
                            timestamp=str(record["timestamp"]) if record["timestamp"] else "",
                            score=score,
                            linked_files=record["files"] or [],
                            linked_functions=[f for f in (record["functions"] or []) if f],
                        ))
        
        # Sort by score descending and limit
        results.sort(key=lambda x: x.score, reverse=True)
        results = results[:limit]
        
        logger.info("Hybrid search complete", results=len(results))
        
        return results
    
    def validate_entities(self, entities: list[str]) -> dict[str, RealityCheckResult]:
        """
        The "Reality Check" - validate entity existence in Graph A.
        
        For each entity name, queries Graph A to check if it exists
        as a File, Function, or other code element.
        
        Args:
            entities: List of entity names to validate
            
        Returns:
            Dict mapping entity name to RealityCheckResult
        """
        logger.info("Validating entities", count=len(entities))
        
        results: dict[str, RealityCheckResult] = {}
        
        with self.driver.session() as session:
            for entity in entities:
                # Check for File nodes (by path or basename)
                file_query = """
                MATCH (f:File)
                WHERE f.path CONTAINS $entity OR f.path ENDS WITH $entity
                RETURN f.path AS path, 'File' AS type
                LIMIT 5
                """
                
                file_result = session.run(file_query, entity=entity)
                file_records = list(file_result)
                
                if file_records:
                    if len(file_records) == 1:
                        results[entity] = RealityCheckResult(
                            entity_name=entity,
                            status="EXISTS",
                            node_type="File",
                            file_path=file_records[0]["path"],
                        )
                    else:
                        results[entity] = RealityCheckResult(
                            entity_name=entity,
                            status="MULTIPLE",
                            node_type="File",
                            file_path=file_records[0]["path"],
                        )
                    continue
                
                # Check for Function nodes
                func_query = """
                MATCH (f:File)-[:DEFINES]->(fn:Function)
                WHERE fn.name = $entity
                RETURN fn.name AS name, f.path AS path, 'Function' AS type
                LIMIT 5
                """
                
                func_result = session.run(func_query, entity=entity)
                func_records = list(func_result)
                
                if func_records:
                    if len(func_records) == 1:
                        results[entity] = RealityCheckResult(
                            entity_name=entity,
                            status="EXISTS",
                            node_type="Function",
                            file_path=func_records[0]["path"],
                        )
                    else:
                        results[entity] = RealityCheckResult(
                            entity_name=entity,
                            status="MULTIPLE",
                            node_type="Function",
                            file_path=func_records[0]["path"],
                        )
                    continue
                
                # Entity not found
                results[entity] = RealityCheckResult(
                    entity_name=entity,
                    status="MISSING",
                )
        
        logger.info(
            "Entity validation complete",
            exists=sum(1 for r in results.values() if r.status == "EXISTS"),
            missing=sum(1 for r in results.values() if r.status == "MISSING"),
        )
        
        return results
    
    def get_active_files(self) -> list[FileInfo]:
        """
        Get all files currently tracked in Graph A.
        
        Returns:
            List of FileInfo objects with path, language, and function count
        """
        with self.driver.session() as session:
            # Memgraph requires proper grouping with WITH
            query = """
            MATCH (f:File)
            OPTIONAL MATCH (f)-[:DEFINES]->(fn:Function)
            WITH f, count(fn) AS func_count
            RETURN f.path AS path,
                   f.language AS language,
                   func_count AS function_count
            ORDER BY f.path
            """
            
            result = session.run(query)
            
            files: list[FileInfo] = []
            for record in result:
                files.append(FileInfo(
                    path=record["path"],
                    language=record["language"],
                    function_count=record["function_count"],
                ))
            
            return files
    
    def get_file_structure(self, file_path: str) -> Optional[FileStructure]:
        """
        Get the structure of a specific file from Graph A.
        
        Returns:
            FileStructure with file info and list of functions, or None if not found
        """
        with self.driver.session() as session:
            query = """
            MATCH (f:File {path: $path})
            OPTIONAL MATCH (f)-[:DEFINES]->(fn:Function)
            RETURN f.path AS path,
                   f.language AS language,
                   f.structural_hash AS hash,
                   collect({
                       name: fn.name,
                       start_line: fn.start_line,
                       end_line: fn.end_line,
                       signature: fn.signature
                   }) AS functions
            """
            
            result = session.run(query, path=file_path)
            record = result.single()
            
            if not record:
                return None
            
            functions: list[FunctionInfo] = []
            for f in record["functions"]:
                if f.get("name"):
                    functions.append(FunctionInfo(
                        name=f["name"],
                        start_line=f.get("start_line", 0),
                        end_line=f.get("end_line", 0),
                        signature=f.get("signature"),
                    ))
            
            return FileStructure(
                path=record["path"],
                language=record["language"],
                functions=functions,
            )
    
    def _cosine_similarity(
        self, vec1: Sequence[float], vec2: Sequence[float]
    ) -> float:
        """
        Calculate cosine similarity between two vectors.
        
        Returns a value between -1 and 1 (higher is more similar).
        """
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5
        
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        
        return float(dot_product / (magnitude1 * magnitude2))
