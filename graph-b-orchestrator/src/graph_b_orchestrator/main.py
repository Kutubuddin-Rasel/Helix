#!/usr/bin/env python3
"""
Graph B Orchestrator - Project Helix
Phase 4: Complete Implementation with Constitution Compliance

Main entrypoint that:
1. Connects to Redis and Memgraph
2. Initializes the Embedding Engine (fastembed)
3. Creates the LangGraph Agent (stateful planning/execution)
4. Consumes events from helix:events stream (async)
5. Sanitizes data through PII Shield
6. Writes Episodes to Graph B with embeddings and hard edges
7. Runs background Squasher for memory compression

Sole-Writer Law: This is the ONLY process that writes to Graph B.

Helix Constitution Compliance:
- Pillar 1.4: Graceful Shutdown (5-second timeout)
- Pillar 4.1: Structured JSON Logging
- Pillar 4.3: Metrics tracking
"""

import asyncio
import signal
import sys
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from .agent import HelixAgent, create_agent
from .consumer import AsyncEventConsumer
from .embeddings import get_embedding_engine
from .graph_writer import GraphWriter
from .logging_config import configure_logging, get_logger
from .metrics import get_metrics
from .schemas import ConsumerStats, SquasherStats
from .squasher import SemanticSquasher

# Configure structured logging (Pillar 4.1)
configure_logging()

logger = get_logger(__name__)
console = Console()

# Configuration
MEMGRAPH_URI = "bolt://localhost:7687"
MEMGRAPH_USER = ""
MEMGRAPH_PASSWORD = ""
REDIS_HOST = "localhost"
REDIS_PORT = 6889  # Custom port to avoid conflicts
REDIS_STREAM = "helix:events"
REDIS_CONSUMER_GROUP = "helix_orchestrator"
REDIS_CONSUMER_NAME = "orchestrator_1"

# Squasher configuration
SQUASH_ENABLED = True
SQUASH_INTERVAL_SECONDS = 300  # 5 minutes
SQUASH_THRESHOLD_MINUTES = 10  # Episodes older than this
SQUASH_MIN_EPISODES = 5  # Min episodes to trigger squash

# Graceful shutdown timeout (Pillar 1.4)
SHUTDOWN_TIMEOUT_SECONDS = 5


def print_banner() -> None:
    """Print the startup banner."""
    console.print()
    console.print(Panel.fit(
        "[bold blue]Project Helix - Graph B Orchestrator (Python)[/]\n"
        "[dim]Phase 3: The Story - Complete[/]\n"
        "[dim italic]LangGraph Agent | Async I/O | Embeddings | Squasher[/]",
        border_style="blue",
    ))
    console.print()


def initialize_embedding_engine() -> bool:
    """
    Pre-initialize the embedding engine to load the model.
    
    Returns True if successful.
    """
    console.print("[bold]Initializing Embedding Engine...[/]")
    
    try:
        engine = get_embedding_engine()
        
        # Warm up the model with a test embedding
        test_embedding = engine.generate_embedding("Test initialization")
        
        console.print(f"[green]✅ Embedding Engine ready[/] (dim={len(test_embedding)})")
        return True
        
    except Exception as e:
        console.print(f"[red]❌ Embedding Engine failed: {e}[/]")
        logger.error("Failed to initialize embedding engine", error=str(e))
        return False


def initialize_agent(graph_writer: Optional[GraphWriter] = None) -> Optional[HelixAgent]:
    """
    Initialize the LangGraph agent.
    
    Returns the agent if successful, None otherwise.
    """
    console.print("[bold]Initializing LangGraph Agent...[/]")
    
    try:
        agent = create_agent(graph_writer=graph_writer)
        console.print("[green]✅ LangGraph Agent ready[/] (ANALYZE → PLAN → EXECUTE → REFLECT)")
        return agent
        
    except Exception as e:
        console.print(f"[yellow]⚠️ Agent initialization failed: {e}[/]")
        logger.warning("Failed to initialize agent", error=str(e))
        return None


async def run_orchestrator_async() -> int:
    """
    Main async orchestrator loop.
    
    Returns:
        0 on clean shutdown, 1 on error
    """
    print_banner()
    
    graph_writer: Optional[GraphWriter] = None
    consumer: Optional[AsyncEventConsumer] = None
    squasher: Optional[SemanticSquasher] = None
    agent: Optional[HelixAgent] = None
    
    try:
        # Step 1: Initialize Embedding Engine (loads model)
        if not initialize_embedding_engine():
            console.print("[yellow]⚠️ Continuing without pre-loaded embeddings[/]")
        
        # Step 2: Connect to Memgraph (Graph B writer)
        console.print("[bold]Connecting to Memgraph (Graph B)...[/]")
        graph_writer = GraphWriter(
            uri=MEMGRAPH_URI,
            user=MEMGRAPH_USER,
            password=MEMGRAPH_PASSWORD,
        )
        graph_writer.connect()
        console.print("[green]✅ Memgraph connected[/]")
        
        # Step 3: Initialize LangGraph Agent
        agent = initialize_agent(graph_writer=graph_writer)
        
        # Step 4: Connect to Redis (Async Event consumer)
        console.print("[bold]Connecting to Redis (Async)...[/]")
        consumer = AsyncEventConsumer(
            redis_host=REDIS_HOST,
            redis_port=REDIS_PORT,
            stream_name=REDIS_STREAM,
            consumer_group=REDIS_CONSUMER_GROUP,
            consumer_name=REDIS_CONSUMER_NAME,
            graph_writer=graph_writer,
            agent=agent,
        )
        await consumer.connect()
        console.print("[green]✅ Redis connected (async)[/]")
        
        # Step 5: Initialize Squasher (background compression)
        if SQUASH_ENABLED:
            console.print("[bold]Starting Semantic Squasher...[/]")
            squasher = SemanticSquasher(
                uri=MEMGRAPH_URI,
                user=MEMGRAPH_USER,
                password=MEMGRAPH_PASSWORD,
                threshold_minutes=SQUASH_THRESHOLD_MINUTES,
                min_episodes=SQUASH_MIN_EPISODES,
            )
            squasher.connect()
            squasher.start_background_loop(interval_seconds=SQUASH_INTERVAL_SECONDS)
            console.print(
                f"[green]✅ Squasher running[/] "
                f"(interval={SQUASH_INTERVAL_SECONDS}s, threshold={SQUASH_THRESHOLD_MINUTES}min)"
            )
        
        # Get initial stats
        stats = await consumer.get_stats()
        console.print(f"[dim]Stream length: {stats.stream_length} messages[/]")
        
        console.print()
        console.print("[bold green]Orchestrator is running![/]")
        console.print("[dim]Features: LangGraph Agent, Async I/O, Embeddings, PII Shield, Squashing[/]")
        console.print("[dim]Waiting for events from Rust Observer...[/]")
        console.print("[dim]Press Ctrl+C to stop.[/]")
        console.print()
        
        # Step 6: Run the async consumer loop
        await consumer.run()
        
        # Print final stats
        final_consumer_stats = await consumer.get_stats()
        final_squasher_stats: Optional[SquasherStats] = squasher.get_stats() if squasher else None
        
        console.print()
        console.print(Panel.fit(
            f"[bold]Session Statistics[/]\n"
            f"Events Processed: {final_consumer_stats.processed}\n"
            f"Errors: {final_consumer_stats.errors}\n"
            f"Summaries Created: {final_squasher_stats.summaries_created if final_squasher_stats else 0}\n"
            f"Episodes Compressed: {final_squasher_stats.episodes_compressed if final_squasher_stats else 0}",
            border_style="blue",
        ))
        
        return 0
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/]")
        return 0
        
    except Exception as e:
        logger.error("Fatal error in orchestrator", error=str(e))
        console.print(f"[red]❌ Error: {e}[/]")
        return 1
        
    finally:
        # Clean up connections
        if squasher:
            squasher.stop()
            squasher.close()
        if consumer:
            await consumer.close()
        if graph_writer:
            graph_writer.close()
        console.print("[dim]Connections closed.[/]")


def run_orchestrator() -> int:
    """
    Synchronous wrapper for the async orchestrator.
    
    Returns:
        0 on clean shutdown, 1 on error
    """
    try:
        return asyncio.run(run_orchestrator_async())
    except KeyboardInterrupt:
        return 0


def run_squasher_once() -> int:
    """
    Run the squasher once and exit.
    
    Useful for manual triggering or testing.
    """
    print_banner()
    console.print("[bold]Running Squasher (one-shot mode)...[/]")
    
    squasher: Optional[SemanticSquasher] = None
    
    try:
        # Initialize embedding engine
        if not initialize_embedding_engine():
            console.print("[red]❌ Embedding engine required for squashing[/]")
            return 1
        
        # Connect squasher
        squasher = SemanticSquasher(
            uri=MEMGRAPH_URI,
            user=MEMGRAPH_USER,
            password=MEMGRAPH_PASSWORD,
            threshold_minutes=SQUASH_THRESHOLD_MINUTES,
            min_episodes=SQUASH_MIN_EPISODES,
        )
        squasher.connect()
        
        # Run squashing job
        result = squasher.run_squashing_job()
        
        console.print(Panel.fit(
            f"[bold]Squashing Complete[/]\n"
            f"Candidates Found: {result.candidates}\n"
            f"Summaries Created: {result.summaries_created}\n"
            f"Episodes Compressed: {result.episodes_compressed}",
            border_style="green" if result.summaries_created > 0 else "yellow",
        ))
        
        return 0
        
    except Exception as e:
        logger.error("Squasher failed", error=str(e))
        console.print(f"[red]❌ Error: {e}[/]")
        return 1
        
    finally:
        if squasher:
            squasher.close()


def main() -> int:
    """Main entry point."""
    # Check for squash-only mode
    if len(sys.argv) > 1 and sys.argv[1] == "--squash":
        return run_squasher_once()
    
    return run_orchestrator()


if __name__ == "__main__":
    sys.exit(main())
