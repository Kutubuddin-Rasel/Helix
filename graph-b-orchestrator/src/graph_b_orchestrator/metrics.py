"""
Metrics Module - Project Helix Graph B Orchestrator

Simple metrics tracking for observability (Pillar 4.3).
Tracks events_processed_total and processing_latency_seconds.

For v1.0, metrics are exposed via structured JSON logs.
Prometheus endpoint may be added in future versions.
"""

import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Optional

import structlog

logger = structlog.get_logger()


@dataclass
class MetricsCollector:
    """
    Lightweight metrics collector for Helix Orchestrator.
    
    Tracks:
    - events_processed_total: Count of successfully processed events
    - events_failed_total: Count of failed events
    - processing_latency_seconds: Time to process each event
    """
    
    events_processed_total: int = 0
    events_failed_total: int = 0
    
    # Latency tracking
    _latency_sum: float = 0.0
    _latency_count: int = 0
    _latency_max: float = 0.0
    _latency_min: float = field(default=float("inf"))
    
    # Squasher metrics
    summaries_created_total: int = 0
    episodes_compressed_total: int = 0
    
    # Start time for uptime tracking
    _start_time: float = field(default_factory=time.time)
    
    def record_event_processed(self, latency_seconds: float) -> None:
        """Record a successfully processed event with its latency."""
        self.events_processed_total += 1
        self._latency_sum += latency_seconds
        self._latency_count += 1
        self._latency_max = max(self._latency_max, latency_seconds)
        self._latency_min = min(self._latency_min, latency_seconds)
        
        # Log metric periodically (every 10 events)
        if self.events_processed_total % 10 == 0:
            self._emit_metrics_log()
    
    def record_event_failed(self) -> None:
        """Record a failed event."""
        self.events_failed_total += 1
    
    def record_squash(self, summaries: int, episodes: int) -> None:
        """Record squasher activity."""
        self.summaries_created_total += summaries
        self.episodes_compressed_total += episodes
    
    def get_latency_avg(self) -> float:
        """Get average processing latency."""
        if self._latency_count == 0:
            return 0.0
        return self._latency_sum / self._latency_count
    
    def get_uptime_seconds(self) -> float:
        """Get uptime in seconds."""
        return time.time() - self._start_time
    
    def get_metrics(self) -> dict[str, int | float]:
        """Get all metrics as a dictionary."""
        return {
            "events_processed_total": self.events_processed_total,
            "events_failed_total": self.events_failed_total,
            "processing_latency_avg_seconds": round(self.get_latency_avg(), 4),
            "processing_latency_max_seconds": round(self._latency_max, 4),
            "processing_latency_min_seconds": round(self._latency_min, 4) if self._latency_min != float("inf") else 0.0,
            "summaries_created_total": self.summaries_created_total,
            "episodes_compressed_total": self.episodes_compressed_total,
            "uptime_seconds": round(self.get_uptime_seconds(), 2),
        }
    
    def _emit_metrics_log(self) -> None:
        """Emit metrics as a structured log entry."""
        logger.info(
            "helix_metrics",
            metric_type="periodic",
            **self.get_metrics(),
        )
    
    def emit_final_metrics(self) -> None:
        """Emit final metrics on shutdown."""
        logger.info(
            "helix_metrics",
            metric_type="shutdown",
            **self.get_metrics(),
        )


class LatencyTimer:
    """Context manager for measuring operation latency."""
    
    def __init__(self, metrics: Optional[MetricsCollector] = None) -> None:
        self.metrics = metrics
        self.start_time: float = 0.0
        self.elapsed: float = 0.0
    
    def __enter__(self) -> "LatencyTimer":
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.elapsed = time.perf_counter() - self.start_time
        
        if self.metrics:
            if exc_type is None:
                self.metrics.record_event_processed(self.elapsed)
            else:
                self.metrics.record_event_failed()


# Global metrics instance
_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


def reset_metrics() -> None:
    """Reset the global metrics collector."""
    global _metrics
    _metrics = MetricsCollector()
