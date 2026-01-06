"""
LangGraph Agent Module - Project Helix Graph B Orchestrator

Implements a stateful planning/execution loop using LangGraph.
This is the "brain" of the orchestrator that decides how to respond
to code changes and manages the reasoning process.

Architecture:
- State: Maintains context across multiple events
- Nodes: Planning, Execution, Reflection
- Edges: Conditional routing based on state
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal, Optional, TypedDict

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel

from .embeddings import generate_embedding
from .graph_writer import EpisodeEvent, GraphWriter
from .schemas import AnalysisResult

logger = structlog.get_logger()


class AgentState(TypedDict):
    """State that flows through the agent graph.
    
    Note: TypedDict is required by LangGraph. Internal data uses Pydantic models
    but must be serialized for state transfer.
    """
    
    # Current event being processed (EpisodeEvent serialized)
    current_event: Optional[EpisodeEvent]
    
    # Accumulated messages/context (LangChain BaseMessage for compatibility)
    messages: Annotated[list[BaseMessage], add_messages]
    
    # Analysis results (AnalysisResult serialized)
    analysis: Optional[AnalysisResult]
    
    # Decision on what action to take
    action: Optional[str]
    
    # Whether processing is complete
    complete: bool
    
    # Error state
    error: Optional[str]


class EventAnalysis(BaseModel):
    """Structured analysis of a code change event."""
    
    event_type: str
    file_path: str
    impact_level: Literal["low", "medium", "high"]
    affected_components: list[str]
    suggested_actions: list[str]
    summary: str


class HelixAgent:
    """
    Stateful LangGraph agent for code change analysis.
    
    Implements a planning/execution loop:
    1. ANALYZE: Understand the code change
    2. PLAN: Decide what actions to take
    3. EXECUTE: Perform the actions
    4. REFLECT: Update state for future events
    
    The compiled graph is cached at class level to avoid expensive recompilation.
    """
    
    # Class-level cache for compiled graph (expensive to build)
    _compiled_graph_cache: ClassVar[Optional[Any]] = None
    
    def __init__(self, graph_writer: Optional[GraphWriter] = None) -> None:
        self.graph_writer = graph_writer
        self._graph: Optional[StateGraph[AgentState]] = None
        self._compiled: Any = None  # CompiledStateGraph doesn't expose proper type
        self._build_graph()
        
    def _build_graph(self) -> None:
        """Build the LangGraph state machine, using cached compiled graph if available."""
        
        # Use cached compiled graph if available (avoids expensive recompilation)
        if HelixAgent._compiled_graph_cache is not None:
            self._compiled = HelixAgent._compiled_graph_cache
            logger.debug("Using cached LangGraph agent")
            return
        
        # Create the graph with our state type
        graph = StateGraph(AgentState)
        
        # Add nodes
        graph.add_node("analyze", self._analyze_node)
        graph.add_node("plan", self._plan_node)
        graph.add_node("execute", self._execute_node)
        graph.add_node("reflect", self._reflect_node)
        
        # Set entry point
        graph.set_entry_point("analyze")
        
        # Add edges
        graph.add_edge("analyze", "plan")
        graph.add_conditional_edges(
            "plan",
            self._should_execute,
            {
                "execute": "execute",
                "skip": "reflect",
            }
        )
        graph.add_edge("execute", "reflect")
        graph.add_edge("reflect", END)
        
        self._graph = graph
        self._compiled = graph.compile()
        
        # Cache the compiled graph for future instances
        HelixAgent._compiled_graph_cache = self._compiled
        
        logger.info("LangGraph agent built and cached successfully")
    
    def _analyze_node(self, state: AgentState) -> dict[str, AnalysisResult | list[BaseMessage] | bool | str | None]:
        """
        ANALYZE: Understand the code change event.
        
        Extracts key information and determines impact level.
        """
        event = state.get("current_event")
        
        if not event:
            return {
                "error": "No event to analyze",
                "complete": True,
            }
        
        logger.debug("Analyzing event", event_type=event.event_type)
        
        # Determine impact level based on event characteristics
        file_path = event.file_path
        event_type = event.event_type
        diff_summary = event.diff_summary
        
        # Simple heuristics for impact level
        impact_level = self._assess_impact(file_path, event_type, diff_summary)
        
        # Identify affected components
        components = self._identify_components(file_path)
        
        analysis = AnalysisResult(
            event_type=event_type,
            file_path=file_path,
            impact_level=impact_level,  # type: ignore[arg-type]
            affected_components=components,
            summary=diff_summary,
        )
        
        return {
            "analysis": analysis,
            "messages": [SystemMessage(content=f"Analyzed: {file_path} ({impact_level} impact)")],
        }
    
    def _plan_node(self, state: AgentState) -> dict[str, AnalysisResult | list[BaseMessage] | str | None]:
        """
        PLAN: Decide what actions to take based on analysis.
        
        Determines whether to create additional context, trigger alerts, etc.
        """
        analysis = state.get("analysis")
        
        if not analysis:
            return {"action": "skip"}
        
        impact = analysis.impact_level
        event_type = analysis.event_type
        
        logger.debug("Planning action", impact=impact, event_type=event_type)
        
        # Planning logic
        if impact == "high":
            action = "full_analysis"
        elif impact == "medium":
            action = "standard_processing"
        else:
            action = "minimal_logging"
        
        # Add suggested actions based on file type
        suggested: list[str] = []
        file_path = analysis.file_path
        
        if file_path.endswith(".py"):
            suggested.append("check_type_hints")
        elif file_path.endswith(".rs"):
            suggested.append("check_error_handling")
        elif file_path.endswith((".js", ".ts")):
            suggested.append("check_api_changes")
        
        # Update analysis with suggested actions
        updated_analysis = AnalysisResult(
            event_type=analysis.event_type,
            file_path=analysis.file_path,
            impact_level=analysis.impact_level,
            affected_components=analysis.affected_components,
            summary=analysis.summary,
            suggested_actions=suggested,
        )
        
        return {
            "action": action,
            "messages": [AIMessage(content=f"Plan: {action} for {impact} impact change")],
            "analysis": updated_analysis,
        }
    
    def _should_execute(self, state: AgentState) -> Literal["execute", "skip"]:
        """
        Conditional edge: Decide whether to execute or skip.
        """
        action = state.get("action", "skip")
        
        if action in ["full_analysis", "standard_processing"]:
            return "execute"
        return "skip"
    
    def _execute_node(self, state: AgentState) -> dict[str, list[BaseMessage]]:
        """
        EXECUTE: Perform the planned actions.
        
        This is where we could integrate with LLMs for deeper analysis.
        For Phase 3, we do structured processing.
        """
        action = state.get("action", "")
        analysis = state.get("analysis")
        
        logger.info("Executing action", action=action)
        
        results: list[str] = []
        
        if action == "full_analysis" and analysis:
            # Full analysis: Generate context for LLM consumption
            context = self._generate_context(analysis)
            results.append(f"Generated context: {len(context)} chars")
            
        elif action == "standard_processing":
            # Standard: Just record the event
            results.append("Standard processing complete")
        
        return {
            "messages": [AIMessage(content=f"Executed: {', '.join(results)}")],
        }
    
    def _reflect_node(self, state: AgentState) -> dict[str, bool | list[BaseMessage]]:
        """
        REFLECT: Update state and complete processing.
        
        Records metrics and prepares for next event.
        """
        analysis = state.get("analysis")
        action = state.get("action") or "none"
        
        logger.info(
            "Agent reflection complete",
            file_path=analysis.file_path if analysis else None,
            impact=analysis.impact_level if analysis else None,
            action=action,
        )
        
        return {
            "complete": True,
            "messages": [SystemMessage(content="Processing complete")],
        }
    
    def _assess_impact(self, file_path: str, event_type: str, diff_summary: str) -> str:
        """Assess the impact level of a change."""
        
        # High impact patterns
        high_impact_patterns = [
            "main.py", "main.rs", "__init__.py",
            "config", "security", "auth", "database",
            "schema", "migration",
        ]
        
        # Check for high impact
        file_lower = file_path.lower()
        for pattern in high_impact_patterns:
            if pattern in file_lower:
                return "high"
        
        # Medium if it's a structural change
        if event_type == "STRUCTURE_CHANGED":
            # Check diff summary for significant changes
            if any(word in diff_summary.lower() for word in ["breaking", "major", "refactor"]):
                return "high"
            return "medium"
        
        return "low"
    
    def _identify_components(self, file_path: str) -> list[str]:
        """Identify which components are affected by this change."""
        components = []
        
        path_lower = file_path.lower()
        
        if "/src/" in path_lower:
            components.append("source")
        if "/test" in path_lower:
            components.append("tests")
        if "api" in path_lower:
            components.append("api")
        if "db" in path_lower or "database" in path_lower:
            components.append("database")
        if "config" in path_lower:
            components.append("config")
        if "security" in path_lower or "auth" in path_lower:
            components.append("security")
        
        return components or ["general"]
    
    def _generate_context(self, analysis: AnalysisResult) -> str:
        """Generate context string for LLM consumption."""
        return f"""
Code Change Analysis:
- File: {analysis.file_path}
- Event: {analysis.event_type}
- Impact: {analysis.impact_level}
- Components: {', '.join(analysis.affected_components)}
- Summary: {analysis.summary}
- Suggested: {', '.join(analysis.suggested_actions)}
""".strip()
    
    async def process_event(self, event: dict[str, str]) -> dict[str, Any]:
        """
        Process a code change event through the agent.
        
        Args:
            event: The event dictionary from Redis (will be converted to EpisodeEvent)
            
        Returns:
            Final agent state after processing
        """
        # Convert dict to strongly-typed EpisodeEvent
        episode = EpisodeEvent.model_validate(event)
        
        initial_state: AgentState = {
            "current_event": episode,
            "messages": [],
            "analysis": None,
            "action": None,
            "complete": False,
            "error": None,
        }
        
        logger.info(
            "Agent processing event",
            file_path=episode.file_path,
            event_type=episode.event_type,
        )
        
        try:
            # Run the graph
            if self._compiled is None:
                raise RuntimeError("Agent graph not compiled")
                
            final_state = await self._compiled.ainvoke(initial_state)
            
            logger.info(
                "Agent completed",
                action=final_state.get("action"),
                complete=final_state.get("complete"),
            )
            
            result: dict[str, Any] = dict(final_state)
            return result
            
        except Exception as e:
            logger.error("Agent processing failed", error=str(e))
            return {
                **initial_state,
                "error": str(e),
                "complete": True,
            }
    
    def process_event_sync(self, event: dict[str, Any]) -> dict[str, Any]:
        """
        Synchronous wrapper for process_event.
        
        For use in non-async contexts.
        """
        import asyncio
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.process_event(event))


def create_agent(graph_writer: Optional[GraphWriter] = None) -> HelixAgent:
    """Factory function to create a configured agent."""
    return HelixAgent(graph_writer=graph_writer)
