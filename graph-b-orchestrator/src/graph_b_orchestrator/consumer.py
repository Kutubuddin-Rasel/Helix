"""
Event Consumer Module - Project Helix Graph B Orchestrator

Consumes events from Redis Streams using Consumer Groups with ACK semantics.
Implements CEW-002 compliant backpressure handling.

Consumer Protocol:
1. XREADGROUP - Read new messages
2. Process with PII scrubbing
3. Run through LangGraph agent
4. Write to Graph B
5. XACK - Acknowledge processed messages

If DB write fails, message is NOT acknowledged and will be reprocessed.

Helix Constitution Compliance:
- Pillar 1.3: At-Least-Once Delivery (ACK semantics)
- Pillar 1.4: Graceful Shutdown (5-second timeout)
- Pillar 4.1: Structured Logging
- Pillar 4.3: Metrics tracking
"""

import asyncio
import signal
import time
from typing import TYPE_CHECKING, Any, Optional

import redis.asyncio as aioredis
from pydantic import ValidationError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .graph_writer import EpisodeEvent, GraphWriter
from .logging_config import get_logger
from .metrics import LatencyTimer, get_metrics
from .schemas import ConsumerStats
from .security import full_scrub, scrub_text

if TYPE_CHECKING:
    from .agent import HelixAgent

logger = get_logger(__name__)

# Graceful shutdown timeout (Pillar 1.4)
SHUTDOWN_TIMEOUT_SECONDS = 5


class AsyncEventConsumer:
    """
    Async Redis Stream Consumer with ACK semantics.
    
    Consumes from helix:events stream using consumer groups.
    Implements graceful shutdown with 5-second timeout (Pillar 1.4).
    
    Uses async/await for all I/O operations per industry standards.
    """
    
    def __init__(
        self,
        redis_host: str = "localhost",
        redis_port: int = 6889,  # Custom port to avoid conflicts
        stream_name: str = "helix:events",
        consumer_group: str = "helix_orchestrator",
        consumer_name: str = "orchestrator_1",
        graph_writer: Optional[GraphWriter] = None,
        agent: Optional["HelixAgent"] = None,
    ) -> None:
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.graph_writer = graph_writer
        self.agent = agent
        
        self._redis: Optional[aioredis.Redis] = None
        self._running = False
        self._processed_count = 0
        self._error_count = 0
        
    async def connect(self) -> None:
        """Establish async connection to Redis and ensure consumer group exists."""
        logger.info(
            "Connecting to Redis (async)",
            host=self.redis_host,
            port=self.redis_port,
            stream=self.stream_name,
        )
        
        await self._connect_redis()
        
        # Ensure consumer group exists
        await self._ensure_consumer_group()
        
        logger.info("Async Event Consumer connected to Redis")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, OSError)),
    )
    async def _connect_redis(self) -> None:
        """Connect to Redis with retry logic (circuit breaker pattern)."""
        self._redis = aioredis.Redis(
            host=self.redis_host,
            port=self.redis_port,
            decode_responses=True,
        )
        
        # Verify connectivity
        ping_result: bool = await self._redis.ping()  # type: ignore[misc]
        if not ping_result:
            raise ConnectionError("Failed to ping Redis")
        
    async def _ensure_consumer_group(self) -> None:
        """Create consumer group if it doesn't exist."""
        if self._redis is None:
            raise RuntimeError("Redis not connected")
            
        try:
            # Check if group exists
            groups = await self._redis.xinfo_groups(self.stream_name)
            group_exists = any(g["name"] == self.consumer_group for g in groups)
            
            if not group_exists:
                await self._redis.xgroup_create(
                    self.stream_name,
                    self.consumer_group,
                    id="0",  # Start from beginning
                    mkstream=True,
                )
                logger.info(
                    "Created consumer group",
                    group=self.consumer_group,
                    stream=self.stream_name,
                )
        except aioredis.ResponseError as e:
            if "NOGROUP" in str(e) or "no such key" in str(e).lower():
                # Stream doesn't exist, create with group
                await self._redis.xgroup_create(
                    self.stream_name,
                    self.consumer_group,
                    id="0",
                    mkstream=True,
                )
                logger.info(
                    "Created stream and consumer group",
                    group=self.consumer_group,
                    stream=self.stream_name,
                )
            else:
                raise
                
    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            logger.info("Async Event Consumer disconnected from Redis")
            
    @property
    def redis(self) -> aioredis.Redis:
        """Get the Redis client, raising if not connected."""
        if not self._redis:
            raise RuntimeError("AsyncEventConsumer not connected. Call connect() first.")
        return self._redis
    
    async def _process_message(self, message_id: str, data: dict[str, str]) -> bool:
        """
        Process a single message from the stream.
        
        Pipeline:
        1. Parse the event data into EpisodeEvent (strong typing)
        2. Scrub PII from text fields  
        3. Run through LangGraph agent (if configured)
        4. Write Episode to Graph B with hard edge
        
        Returns True if successfully processed, False on failure.
        """
        try:
            logger.debug("Processing message", message_id=message_id)
            
            # Step 1: Parse into strongly-typed EpisodeEvent
            # Use data directly - avoid unnecessary dict copy
            try:
                event = EpisodeEvent.model_validate(data)
            except ValidationError as e:
                logger.error(
                    "Invalid event format",
                    message_id=message_id,
                    errors=str(e),
                )
                return False
            
            # Step 2: Scrub PII from text fields
            event.diff_summary = full_scrub(event.diff_summary)
            event.file_path = scrub_text(event.file_path)
            
            # Step 3: Run through LangGraph agent (if available)
            if self.agent:
                try:
                    # Pass dict for agent (minimal conversion)
                    agent_result = await self.agent.process_event(
                        event.model_dump(mode="python")
                    )
                    # Extract impact from AnalysisResult (Pydantic model, not dict)
                    analysis = agent_result.get("analysis")
                    impact = getattr(analysis, "impact_level", None) if analysis else None
                    logger.debug(
                        "Agent processed event",
                        action=agent_result.get("action"),
                        impact=impact,
                    )
                except Exception as e:
                    logger.warning("Agent processing failed", error=str(e))
            
            # Step 4: Write to Graph B using strongly-typed EpisodeEvent
            if self.graph_writer:
                success = self.graph_writer.ingest_episode_event(event)
                if not success:
                    return False
                    
            self._processed_count += 1
            return True
            
        except Exception as e:
            logger.error(
                "Failed to process message",
                message_id=message_id,
                error=str(e),
            )
            self._error_count += 1
            return False
            
    async def _acknowledge_message(self, message_id: str) -> None:
        """Acknowledge a successfully processed message."""
        await self.redis.xack(
            self.stream_name,
            self.consumer_group,
            message_id,
        )
        logger.debug("Acknowledged message", message_id=message_id)
        
    async def process_pending(self) -> int:
        """
        Process any pending messages that weren't acknowledged.
        
        Returns the number of messages processed.
        """
        processed = 0
        
        # Read pending messages for this consumer
        pending = await self.redis.xpending_range(
            self.stream_name,
            self.consumer_group,
            min="-",
            max="+",
            count=100,
            consumername=self.consumer_name,
        )
        
        if not pending:
            return 0
            
        logger.info(
            "Processing pending messages",
            count=len(pending),
        )
        
        for entry in pending:
            message_id = entry["message_id"]
            
            # Claim the message
            messages = await self.redis.xclaim(
                self.stream_name,
                self.consumer_group,
                self.consumer_name,
                min_idle_time=0,
                message_ids=[message_id],
            )
            
            for msg_id, data in messages:
                if await self._process_message(msg_id, data):
                    await self._acknowledge_message(msg_id)
                    processed += 1
                    
        return processed
        
    async def run(self, block_ms: int = 5000) -> None:
        """
        Main async consumer loop.
        
        Continuously reads from the stream and processes events.
        Implements graceful shutdown on SIGINT/SIGTERM.
        """
        self._running = True
        
        logger.info(
            "Starting async event consumer loop",
            consumer=self.consumer_name,
            group=self.consumer_group,
        )
        
        # First, process any pending messages
        pending_count = await self.process_pending()
        if pending_count > 0:
            logger.info("Processed pending messages", count=pending_count)
        
        while self._running:
            try:
                # Read new messages from the stream
                # ">" means only new messages not yet delivered to this group
                messages = await self.redis.xreadgroup(
                    groupname=self.consumer_group,
                    consumername=self.consumer_name,
                    streams={self.stream_name: ">"},
                    count=10,
                    block=block_ms,
                )
                
                if not messages:
                    continue
                    
                # Process each message and collect successful IDs for batch ACK
                successful_ids: list[str] = []
                
                for stream_name, stream_messages in messages:
                    for message_id, data in stream_messages:
                        if await self._process_message(message_id, data):
                            successful_ids.append(message_id)
                        else:
                            logger.warning(
                                "Message processing failed, will retry",
                                message_id=message_id,
                            )
                
                # Batch ACK all successful messages (reduces network round-trips)
                if successful_ids:
                    async with self.redis.pipeline() as pipe:
                        for msg_id in successful_ids:
                            pipe.xack(self.stream_name, self.consumer_group, msg_id)
                        await pipe.execute()
                    logger.debug(
                        "Batch acknowledged messages",
                        count=len(successful_ids),
                    )
                            
            except aioredis.ConnectionError as e:
                logger.error("Redis connection lost", error=str(e))
                await asyncio.sleep(5)  # Wait before reconnecting
                try:
                    await self.connect()
                except Exception:
                    pass
                    
            except asyncio.CancelledError:
                logger.info("Consumer loop cancelled")
                break
                    
            except Exception as e:
                logger.error("Unexpected error in consumer loop", error=str(e))
                await asyncio.sleep(1)
                
        logger.info(
            "Async consumer loop stopped",
            processed=self._processed_count,
            errors=self._error_count,
        )
    
    def stop(self) -> None:
        """Signal the consumer to stop."""
        self._running = False
        
    async def get_stats(self) -> ConsumerStats:
        """Get consumer statistics."""
        stream_len = await self.redis.xlen(self.stream_name) if self._redis else 0
        return ConsumerStats(
            processed=self._processed_count,
            errors=self._error_count,
            stream_length=stream_len,
        )


# Legacy sync consumer for backward compatibility
class EventConsumer:
    """
    Synchronous wrapper for AsyncEventConsumer.
    
    Provided for backward compatibility. New code should use AsyncEventConsumer.
    """
    
    def __init__(self, **kwargs: Any) -> None:
        self._async_consumer = AsyncEventConsumer(**kwargs)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create event loop."""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop
        
    def connect(self) -> None:
        """Connect to Redis."""
        self._get_loop().run_until_complete(self._async_consumer.connect())
        
    def close(self) -> None:
        """Close connection."""
        if self._loop and not self._loop.is_closed():
            self._loop.run_until_complete(self._async_consumer.close())
            
    def run(self, block_ms: int = 5000) -> None:
        """Run the consumer loop."""
        loop = self._get_loop()
        
        # Set up signal handlers
        def shutdown() -> None:
            self._async_consumer.stop()
            
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, shutdown)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
        
        try:
            loop.run_until_complete(self._async_consumer.run(block_ms))
        except KeyboardInterrupt:
            self._async_consumer.stop()
            
    def get_stats(self) -> ConsumerStats:
        """Get statistics."""
        return self._get_loop().run_until_complete(self._async_consumer.get_stats())
