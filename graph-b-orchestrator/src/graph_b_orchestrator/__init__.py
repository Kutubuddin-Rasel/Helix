"""
Graph B Orchestrator - Project Helix

Phase 4: Complete Implementation with Constitution Compliance

This package implements the Python Orchestrator for Project Helix:
- Consumes events from Redis Streams (async, from Rust Observer)
- Sanitizes data through PII Shield
- Processes events through LangGraph Agent
- Generates vector embeddings for semantic search
- Writes Episodes to Graph B with hard edges to Graph A
- Compresses old episodes into Summary nodes
- Exposes functionality via MCP (Model Context Protocol)

Helix Constitution Compliance:
- Pillar 1.3: At-Least-Once Delivery (ACK semantics)
- Pillar 1.4: Graceful Shutdown (5-second timeout)
- Pillar 4.1: Structured JSON Logging
- Pillar 4.3: Metrics tracking

Sole-Writer Law: This is the ONLY process that writes to Graph B.
"""

from .agent import HelixAgent, create_agent
from .consumer import AsyncEventConsumer, EventConsumer
from .context_compiler import compile_context, compile_file_list, compile_reality_report
from .embeddings import EmbeddingEngine, generate_embedding, get_embedding_engine
from .graph_writer import EpisodeEvent, GraphWriter
from .logging_config import configure_logging, get_logger
from .main import main, run_orchestrator, run_squasher_once
from .mcp_server import HelixMCPServer
from .metrics import LatencyTimer, MetricsCollector, get_metrics, reset_metrics
from .query_engine import QueryEngine, RealityCheckResult, SearchResult
from .security import full_scrub, scrub_event, scrub_text
from .squasher import SemanticSquasher

__all__ = [
    # Main
    "main",
    "run_orchestrator",
    "run_squasher_once",
    # Logging & Metrics (Constitution Pillar 4)
    "configure_logging",
    "get_logger",
    "MetricsCollector",
    "LatencyTimer",
    "get_metrics",
    "reset_metrics",
    # Agent
    "HelixAgent",
    "create_agent",
    # Consumer
    "AsyncEventConsumer",
    "EventConsumer",
    # Graph Writer
    "GraphWriter",
    "EpisodeEvent",
    # Query Engine
    "QueryEngine",
    "SearchResult",
    "RealityCheckResult",
    # Context Compiler
    "compile_context",
    "compile_reality_report",
    "compile_file_list",
    # MCP Server
    "HelixMCPServer",
    # Embeddings
    "EmbeddingEngine",
    "get_embedding_engine",
    "generate_embedding",
    # Security
    "scrub_text",
    "scrub_event",
    "full_scrub",
    # Squasher
    "SemanticSquasher",
]

__version__ = "0.5.0"
