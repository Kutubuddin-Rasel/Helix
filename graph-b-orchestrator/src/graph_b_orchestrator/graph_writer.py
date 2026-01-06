"""
Graph Writer Module - Project Helix Graph B Orchestrator

Sole-Writer Law: This module is the ONLY writer to Graph B (Episodes).
It creates Episode nodes with:
- Hard relationships [:AFFECTS] to Graph A (File) nodes
- Vector embeddings for semantic search

Architectural Constraints:
- MATCH existing File nodes (created by Rust Observer)
- CREATE Episode nodes with [:AFFECTS] relationships
- Store embedding vectors for semantic similarity search
"""

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

import structlog
from neo4j import Driver, GraphDatabase, ManagedTransaction
from pydantic import BaseModel, Field

from .embeddings import generate_embedding

logger = structlog.get_logger()


class EpisodeEvent(BaseModel):
    """Validated structure for history events from Redis."""
    
    event_type: str
    file_path: str
    timestamp: str
    diff_summary: str
    triggered_by: str = "UNKNOWN"
    old_hash: Optional[str] = None
    new_hash: Optional[str] = None
    
    # Generated fields
    episode_id: str = Field(default_factory=lambda: f"ep_{uuid4().hex[:12]}")


class GraphWriter:
    """
    Graph B Writer with Hard Edge Semantics and Vector Embeddings.
    
    Implements the Sole-Writer mandate for Graph B (Episodes/History).
    All Episode nodes MUST have:
    - Hard [:AFFECTS] relationships to File nodes
    - Vector embeddings for semantic search
    """
    
    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "",
        password: str = "",
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self._driver: Optional[Driver] = None
        
    def connect(self) -> None:
        """Establish connection to Memgraph."""
        logger.info("Connecting to Memgraph for Graph B writes", uri=self.uri)
        
        auth = (self.user, self.password) if self.user else None
        self._driver = GraphDatabase.driver(self.uri, auth=auth)
        self._driver.verify_connectivity()
        
        logger.info("Graph B Writer connected to Memgraph")
        
    def close(self) -> None:
        """Close the database connection."""
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Graph B Writer disconnected")
            
    @property
    def driver(self) -> Driver:
        if not self._driver:
            raise RuntimeError("GraphWriter not connected. Call connect() first.")
        return self._driver
    
    def ingest_episode_event(self, episode: EpisodeEvent) -> bool:
        """
        Ingest a strongly-typed EpisodeEvent and write to Graph B.
        
        This creates:
        1. Vector embedding from the diff_summary
        2. An Episode node with event properties and embedding
        3. A hard [:AFFECTS] relationship to the existing File node
        
        Args:
            episode: The validated EpisodeEvent from consumer
            
        Returns:
            True if successfully written, False on failure
        """
        try:
            logger.info(
                "Ingesting episode event",
                episode_id=episode.episode_id,
                event_type=episode.event_type,
                file_path=episode.file_path,
            )
            
            # Generate embedding from the diff summary
            embedding_text = f"{episode.event_type}: {episode.diff_summary}"
            embedding = generate_embedding(embedding_text)
            
            logger.debug(
                "Generated embedding for episode",
                episode_id=episode.episode_id,
                embedding_dim=len(embedding),
            )
            
            # Execute the write in a transaction
            with self.driver.session() as session:
                result = session.execute_write(
                    self._create_episode_with_embedding,
                    episode,
                    embedding,
                )
                
            if result["success"]:
                logger.info(
                    "Episode created with embedding and hard edge",
                    episode_id=episode.episode_id,
                    file_matched=result["file_matched"],
                )
            else:
                logger.warning(
                    "Episode created as dangling (File not found)",
                    episode_id=episode.episode_id,
                    file_path=episode.file_path,
                )
                
            return True
            
        except Exception as e:
            logger.error(
                "Failed to ingest episode event",
                error=str(e),
                episode_id=episode.episode_id,
            )
            return False
    
    def ingest_history_event(self, event: dict[str, Any]) -> bool:
        """
        Ingest a history event from Redis and write to Graph B.
        
        DEPRECATED: Use ingest_episode_event with strongly-typed EpisodeEvent.
        This method is kept for backward compatibility.
        
        Args:
            event: The sanitized event dictionary from Redis
            
        Returns:
            True if successfully written, False on failure
        """
        try:
            # Validate and parse the event into strongly-typed model
            episode = EpisodeEvent(**event)
            return self.ingest_episode_event(episode)
            
        except Exception as e:
            logger.error(
                "Failed to ingest history event",
                error=str(e),
                event_keys=list(event.keys()) if isinstance(event, dict) else None,
            )
            return False
    
    @staticmethod
    def _create_episode_with_embedding(
        tx: ManagedTransaction,
        episode: EpisodeEvent,
        embedding: list[float],
    ) -> dict[str, bool | str]:
        """
        Transaction function to create Episode with embedding and hard edge to File.
        
        Optimized: Single query using OPTIONAL MATCH + FOREACH pattern
        instead of N+1 queries (check file, then create episode).
        """
        # Single query: OPTIONAL MATCH file, CREATE episode, conditionally CREATE edge
        create_query = """
        OPTIONAL MATCH (f:File {path: $file_path})
        CREATE (e:Episode {
            id: $episode_id,
            event_type: $event_type,
            file_path: $file_path,
            timestamp: $timestamp,
            diff_summary: $diff_summary,
            triggered_by: $triggered_by,
            old_hash: $old_hash,
            new_hash: $new_hash,
            embedding: $embedding,
            created_at: datetime(),
            summarized: false,
            dangling: CASE WHEN f IS NULL THEN true ELSE false END
        })
        FOREACH (_ IN CASE WHEN f IS NOT NULL THEN [1] ELSE [] END |
            CREATE (e)-[:AFFECTS]->(f)
        )
        RETURN e.id AS episode_id, f IS NOT NULL AS file_matched
        """
        
        result = tx.run(
            create_query,
            episode_id=episode.episode_id,
            event_type=episode.event_type,
            file_path=episode.file_path,
            timestamp=episode.timestamp,
            diff_summary=episode.diff_summary,
            triggered_by=episode.triggered_by,
            old_hash=episode.old_hash,
            new_hash=episode.new_hash,
            embedding=embedding,
        )
        
        record = result.single()
        file_matched = record["file_matched"] if record else False
        
        # Only warn if not a FILE_DELETED event (dangling is expected for deletes)
        if not file_matched and episode.event_type != "FILE_DELETED":
            logger.warning(
                "Created dangling Episode - File not found in Graph A",
                file_path=episode.file_path,
                episode_id=episode.episode_id,
            )
        
        return {
            "success": True,
            "file_matched": file_matched,
        }
    
    def get_episode_count(self) -> int:
        """Get the count of Episode nodes in Graph B."""
        with self.driver.session() as session:
            result = session.run("MATCH (e:Episode) RETURN count(e) AS count")
            record = result.single()
            return record["count"] if record else 0
    
    def get_connected_episodes(self) -> int:
        """Get count of Episodes with hard edges to Files."""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (e:Episode)-[:AFFECTS]->(f:File) RETURN count(e) AS count"
            )
            record = result.single()
            return record["count"] if record else 0
    
    def get_episodes_with_embeddings(self) -> int:
        """Get count of Episodes that have embeddings."""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (e:Episode) WHERE e.embedding IS NOT NULL RETURN count(e) AS count"
            )
            record = result.single()
            return record["count"] if record else 0
