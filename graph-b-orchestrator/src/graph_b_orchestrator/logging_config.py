"""
Logging Configuration Module - Project Helix Graph B Orchestrator

Provides structured logging configuration with JSON/Console switch (Pillar 4.1).
Uses HELIX_LOG_FORMAT environment variable to select output format.

Environment Variables:
- HELIX_LOG_FORMAT: "json" or "console" (default: "console")
- HELIX_LOG_LEVEL: "DEBUG", "INFO", "WARNING", "ERROR" (default: "INFO")
"""

import os
import sys
from typing import Any, cast

import structlog
from structlog.stdlib import BoundLogger


def configure_logging() -> None:
    """
    Configure structlog with format based on environment.
    
    - JSON format for production (Pillar 4.1 compliance)
    - Console format for development
    """
    log_format = os.environ.get("HELIX_LOG_FORMAT", "console").lower()
    log_level = os.environ.get("HELIX_LOG_LEVEL", "INFO").upper()
    
    # Common processors
    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    if log_format == "json":
        # JSON format for production (Pillar 4.1)
        processors = [
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Console format for development
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    
    # Log the configuration
    logger = structlog.get_logger()
    logger.info(
        "Logging configured",
        format=log_format,
        level=log_level,
    )


def get_logger(name: str | None = None) -> BoundLogger:
    """Get a configured logger instance."""
    return cast(BoundLogger, structlog.get_logger(name))
