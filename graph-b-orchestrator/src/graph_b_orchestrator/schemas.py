"""
Core Schema Models - Project Helix Graph B Orchestrator

Strongly-typed Pydantic models for all data structures.
Replaces dict[str, Any] with proper type safety.

Following the Constitution: "Strict Types Always"
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ============================================================================
# File & Code Structure Models (Graph A)
# ============================================================================

class FileInfo(BaseModel):
    """File information from Graph A queries."""
    
    path: str
    language: Optional[str] = None
    function_count: int = 0


class FunctionInfo(BaseModel):
    """Function information from Graph A."""
    
    name: str
    start_line: int
    end_line: int
    signature: Optional[str] = None


class FileStructure(BaseModel):
    """Complete file structure from Graph A."""
    
    path: str
    language: Optional[str] = None
    functions: list[FunctionInfo] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)


# ============================================================================
# Squasher Models
# ============================================================================

class SquashCandidate(BaseModel):
    """A group of Episodes that are candidates for squashing."""
    
    file_path: str
    file_id: str
    episode_ids: list[str]
    summaries: list[str]


class SquashResult(BaseModel):
    """Result of a squashing operation."""
    
    candidates: int
    summaries_created: int
    episodes_compressed: int


class SquasherStats(BaseModel):
    """Squasher statistics."""
    
    summaries_created: int
    episodes_compressed: int
    running: bool


# ============================================================================
# Agent Models (LangGraph)
# ============================================================================

class AgentMessage(BaseModel):
    """A message in the agent conversation."""
    
    role: Literal["system", "user", "assistant"]
    content: str


class AnalysisResult(BaseModel):
    """Result of analyzing a code change event."""
    
    event_type: str
    file_path: str
    impact_level: Literal["low", "medium", "high"]
    affected_components: list[str]
    summary: str
    suggested_actions: list[str] = Field(default_factory=list)


class AgentNodeResult(BaseModel):
    """Result returned by agent graph nodes (partial state update)."""
    
    messages: list[AgentMessage] = Field(default_factory=list)
    analysis: Optional[AnalysisResult] = None
    action: Optional[str] = None
    complete: bool = False
    error: Optional[str] = None


# ============================================================================
# Consumer Stats
# ============================================================================

class ConsumerStats(BaseModel):
    """Consumer processing statistics."""
    
    processed: int
    errors: int
    stream_length: int


# ============================================================================
# Security Models
# ============================================================================

class ScrubResult(BaseModel):
    """Result of PII scrubbing operation."""
    
    text: str
    pii_detected: bool
    pii_types: list[str] = Field(default_factory=list)
