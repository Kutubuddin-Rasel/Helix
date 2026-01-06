"""
Semantic Squasher Module - Project Helix Graph B Orchestrator

Memory Maintenance: Compresses old, granular Episode nodes into 
high-level Summary nodes to prevent unbounded history growth.

Algorithm:
1. Find Episode candidates older than N minutes, not yet summarized
2. Group by File node
3. If > threshold episodes exist for a file, compress them
4. Create Summary node with combined embedding
5. Link: (Summary)-[:SUMMARIZES]->(Episode), (Summary)-[:AFFECTS]->(File)
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import structlog
from neo4j import Driver, GraphDatabase, ManagedTransaction

from .embeddings import generate_embedding
from .schemas import SquashCandidate, SquashResult, SquasherStats

logger = structlog.get_logger()

# Configuration
SQUASH_THRESHOLD_MINUTES = 10  # Episodes older than this are candidates
SQUASH_MIN_EPISODES = 5  # Minimum episodes per file to trigger squashing
SQUASH_INTERVAL_SECONDS = 300  # How often to run the squasher (5 minutes)


class SemanticSquasher:
    """
    Background job that compresses old Episodes into Summary nodes.
    
    Preserves semantic meaning through combined embeddings and maintains
    hard edges to File nodes for code traceability.
    """
    
    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "",
        password: str = "",
        threshold_minutes: int = SQUASH_THRESHOLD_MINUTES,
        min_episodes: int = SQUASH_MIN_EPISODES,
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self.threshold_minutes = threshold_minutes
        self.min_episodes = min_episodes
        
        self._driver: Optional[Driver] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # Statistics
        self._summaries_created = 0
        self._episodes_compressed = 0
        
    def connect(self) -> None:
        """Establish connection to Memgraph."""
        logger.info("Squasher connecting to Memgraph", uri=self.uri)
        
        auth = (self.user, self.password) if self.user else None
        self._driver = GraphDatabase.driver(self.uri, auth=auth)
        self._driver.verify_connectivity()
        
        logger.info("Squasher connected to Memgraph")
        
    def close(self) -> None:
        """Close the database connection."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Squasher disconnected")
            
    @property
    def driver(self) -> Driver:
        if not self._driver:
            raise RuntimeError("Squasher not connected. Call connect() first.")
        return self._driver
    
    def find_squash_candidates(self) -> list[SquashCandidate]:
        """
        Find groups of Episodes that are candidates for squashing.
        
        Returns list of SquashCandidate objects.
        """
        # Query for unsummarized episodes older than threshold, grouped by file
        # Note: Memgraph doesn't support duration.inSeconds, so we pass the threshold timestamp
        threshold_time = datetime.now(timezone.utc) - timedelta(minutes=self.threshold_minutes)
        threshold_iso = threshold_time.isoformat().replace("+00:00", "Z")
        
        query = """
        MATCH (e:Episode)-[:AFFECTS]->(f:File)
        WHERE e.summarized = false
          AND e.timestamp < $threshold_iso
        WITH f, collect(e) AS episodes
        WHERE size(episodes) >= $min_episodes
        RETURN f.path AS file_path, 
               f.id AS file_id,
               [ep IN episodes | ep.id] AS episode_ids,
               [ep IN episodes | ep.diff_summary] AS summaries
        ORDER BY size(episodes) DESC
        """
        
        with self.driver.session() as session:
            result = session.run(
                query,
                threshold_iso=threshold_iso,
                min_episodes=self.min_episodes,
            )
            
            candidates: list[SquashCandidate] = []
            for record in result:
                candidates.append(SquashCandidate(
                    file_path=record["file_path"],
                    file_id=record["file_id"],
                    episode_ids=record["episode_ids"],
                    summaries=record["summaries"],
                ))
            
            return candidates
    
    def _combine_summaries(self, summaries: list[str]) -> str:
        """
        Combine multiple diff summaries into a single summary text.
        
        For Phase 3, we use simple concatenation.
        In production, this could use an LLM for intelligent summarization.
        """
        # Filter empty summaries
        valid_summaries = [s for s in summaries if s and s.strip()]
        
        if not valid_summaries:
            return "Multiple code changes"
        
        # Deduplicate similar summaries
        unique_summaries = list(dict.fromkeys(valid_summaries))
        
        # Combine with separator
        combined = " | ".join(unique_summaries[:10])  # Limit to first 10
        
        # Truncate if too long
        if len(combined) > 500:
            combined = combined[:497] + "..."
        
        return combined
    
    def squash_episodes(
        self,
        file_path: str,
        file_id: str,
        episode_ids: list[str],
        summaries: list[str],
    ) -> Optional[str]:
        """
        Compress multiple Episodes into a single Summary node.
        
        Creates:
        1. Summary node with combined text and embedding
        2. [:SUMMARIZES] relationships to all source Episodes
        3. [:AFFECTS] relationship to the File node (preserving hard edge)
        
        Returns the Summary node ID if successful.
        """
        if not episode_ids:
            return None
        
        summary_id = f"sum_{uuid4().hex[:12]}"
        
        # Combine summaries
        combined_text = self._combine_summaries(summaries)
        
        # Generate embedding for the combined summary
        embedding = generate_embedding(combined_text)
        
        logger.info(
            "Squashing episodes",
            summary_id=summary_id,
            file_path=file_path,
            episode_count=len(episode_ids),
        )
        
        try:
            with self.driver.session() as session:
                session.execute_write(
                    self._create_summary_node,
                    summary_id,
                    file_path,
                    file_id,
                    episode_ids,
                    combined_text,
                    embedding,
                )
            
            self._summaries_created += 1
            self._episodes_compressed += len(episode_ids)
            
            logger.info(
                "Summary created",
                summary_id=summary_id,
                episodes_compressed=len(episode_ids),
            )
            
            return summary_id
            
        except Exception as e:
            logger.error(
                "Failed to create summary",
                error=str(e),
                file_path=file_path,
            )
            return None
    
    @staticmethod
    def _create_summary_node(
        tx: ManagedTransaction,
        summary_id: str,
        file_path: str,
        file_id: str,
        episode_ids: list[str],
        combined_text: str,
        embedding: list[float],
    ) -> None:
        """
        Transaction function to create Summary with relationships.
        
        Creates:
        1. Summary node
        2. [:SUMMARIZES] to each Episode
        3. [:AFFECTS] to the File (preserving hard edge for code traceability)
        4. Marks Episodes as summarized
        """
        # Create Summary node and link to File
        create_summary_query = """
        MATCH (f:File {path: $file_path})
        CREATE (s:Summary {
            id: $summary_id,
            text: $combined_text,
            embedding: $embedding,
            episode_count: $episode_count,
            created_at: datetime()
        })
        CREATE (s)-[:AFFECTS]->(f)
        RETURN s.id AS summary_id
        """
        
        tx.run(
            create_summary_query,
            summary_id=summary_id,
            file_path=file_path,
            combined_text=combined_text,
            embedding=embedding,
            episode_count=len(episode_ids),
        )
        
        # Link Summary to each Episode and mark as summarized
        link_episodes_query = """
        MATCH (s:Summary {id: $summary_id})
        MATCH (e:Episode) WHERE e.id IN $episode_ids
        CREATE (s)-[:SUMMARIZES]->(e)
        SET e.summarized = true
        """
        
        tx.run(
            link_episodes_query,
            summary_id=summary_id,
            episode_ids=episode_ids,
        )
    
    def run_squashing_job(self) -> SquashResult:
        """
        Execute one round of squashing.
        
        Returns SquashResult with statistics.
        """
        logger.info("Running squashing job")
        
        candidates = self.find_squash_candidates()
        
        if not candidates:
            logger.debug("No squash candidates found")
            return SquashResult(candidates=0, summaries_created=0, episodes_compressed=0)
        
        logger.info(
            "Found squash candidates",
            candidate_count=len(candidates),
            total_episodes=sum(len(c.episode_ids) for c in candidates),
        )
        
        summaries_created = 0
        episodes_compressed = 0
        
        for candidate in candidates:
            summary_id = self.squash_episodes(
                file_path=candidate.file_path,
                file_id=candidate.file_id,
                episode_ids=candidate.episode_ids,
                summaries=candidate.summaries,
            )
            
            if summary_id:
                summaries_created += 1
                episodes_compressed += len(candidate.episode_ids)
        
        return SquashResult(
            candidates=len(candidates),
            summaries_created=summaries_created,
            episodes_compressed=episodes_compressed,
        )
    
    def start_background_loop(self, interval_seconds: int = SQUASH_INTERVAL_SECONDS) -> None:
        """
        Start the squasher as a background thread.
        
        Runs the squashing job every interval_seconds.
        """
        if self._running:
            logger.warning("Squasher already running")
            return
        
        self._running = True
        
        def loop() -> None:
            logger.info(
                "Squasher background loop started",
                interval_seconds=interval_seconds,
            )
            
            while self._running:
                try:
                    self.run_squashing_job()
                except Exception as e:
                    logger.error("Squasher job failed", error=str(e))
                
                # Sleep in small increments to allow quick shutdown
                for _ in range(interval_seconds):
                    if not self._running:
                        break
                    time.sleep(1)
            
            logger.info("Squasher background loop stopped")
        
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
    
    def stop(self) -> None:
        """Stop the background squashing loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
    
    def get_stats(self) -> SquasherStats:
        """Get squasher statistics."""
        return SquasherStats(
            summaries_created=self._summaries_created,
            episodes_compressed=self._episodes_compressed,
            running=self._running,
        )
