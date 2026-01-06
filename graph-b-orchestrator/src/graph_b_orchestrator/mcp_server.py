"""
MCP Server Module - Project Helix Graph B Orchestrator

Model Context Protocol (MCP) server that exposes Helix functionality to IDEs.
Provides tools for context retrieval and reality checking.

Transport: Stdio (Standard Input/Output)

Exposed Tools:
- get_helix_context: Hybrid search + context compilation
- check_reality: Entity validation against Graph A

Exposed Resources:
- helix://structure/active_files: List of tracked files
"""

import asyncio
import re
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool
from pydantic import AnyUrl

from .context_compiler import compile_context, compile_file_list, compile_reality_report
from .query_engine import QueryEngine

logger = structlog.get_logger()

# Configuration
MEMGRAPH_URI = "bolt://localhost:7687"
MEMGRAPH_USER = ""
MEMGRAPH_PASSWORD = ""


class HelixMCPServer:
    """
    MCP Server for Project Helix.
    
    Exposes the Dual-Graph cognitive engine to IDEs via the
    Model Context Protocol.
    """
    
    def __init__(self) -> None:
        self.server = Server("helix-context")
        self.query_engine: QueryEngine | None = None
        
        self._setup_handlers()
        
    def _setup_handlers(self) -> None:
        """Set up MCP protocol handlers."""
        
        @self.server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_tools() -> list[Tool]:
            """List available MCP tools."""
            return [
                Tool(
                    name="get_helix_context",
                    description=(
                        "Search the Helix knowledge graph for context related to a query. "
                        "Returns relevant code change history and current code structure. "
                        "Use this when you need to understand why code changed or what happened."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Natural language query about the codebase (e.g., 'Why is authentication broken?')",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results (default: 5)",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="check_reality",
                    description=(
                        "Validate that code entities (functions, classes, files) actually exist in the codebase. "
                        "Use this before generating code that references other entities. "
                        "The 'Reality Check' prevents hallucinated references."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "code_snippet": {
                                "type": "string",
                                "description": "Code snippet or text containing entity names to validate",
                            },
                            "entities": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Explicit list of entity names to check (optional)",
                            },
                        },
                        "required": [],
                    },
                ),
            ]
        
        @self.server.call_tool()  # type: ignore[untyped-decorator]
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Handle tool calls."""
            try:
                if name == "get_helix_context":
                    return await self._handle_get_context(arguments)
                elif name == "check_reality":
                    return await self._handle_check_reality(arguments)
                else:
                    return [TextContent(type="text", text=f"Unknown tool: {name}")]
            except Exception as e:
                logger.error("Tool call failed", tool=name, error=str(e))
                return [TextContent(type="text", text=f"Error: {str(e)}")]
        
        @self.server.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_resources() -> list[Resource]:
            """List available MCP resources."""
            return [
                Resource(
                    uri=AnyUrl("helix://structure/active_files"),
                    name="Active Files",
                    description="List of all files currently tracked in Graph A",
                    mimeType="text/markdown",
                ),
            ]
        
        @self.server.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
        async def read_resource(uri: str) -> str:
            """Read a resource."""
            if uri == "helix://structure/active_files":
                return await self._handle_active_files()
            else:
                return f"Unknown resource: {uri}"
    
    async def _handle_get_context(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle get_helix_context tool call."""
        query = arguments.get("query", "")
        limit = arguments.get("limit", 5)
        
        if not query:
            return [TextContent(type="text", text="Error: query is required")]
        
        logger.info("MCP get_helix_context", query=query[:50], limit=limit)
        
        # Ensure connected
        self._ensure_connected()
        
        # Perform hybrid search
        if self.query_engine is None:
            return [TextContent(type="text", text="Error: Not connected")]
            
        results = self.query_engine.hybrid_search(query, limit=limit)
        
        # Compile context
        context = compile_context(results)
        
        return [TextContent(type="text", text=context)]
    
    async def _handle_check_reality(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle check_reality tool call."""
        code_snippet = arguments.get("code_snippet", "")
        explicit_entities = arguments.get("entities", [])
        
        # Extract entity names from code snippet
        entities = list(explicit_entities)
        
        if code_snippet:
            # Extract potential entity names (identifiers)
            extracted = self._extract_entities(code_snippet)
            entities.extend(extracted)
        
        if not entities:
            return [TextContent(
                type="text",
                text="No entities to check. Provide code_snippet or entities list."
            )]
        
        # Deduplicate
        entities = list(dict.fromkeys(entities))
        
        logger.info("MCP check_reality", entities=len(entities))
        
        # Ensure connected
        self._ensure_connected()
        
        # Validate entities
        if self.query_engine is None:
            return [TextContent(type="text", text="Error: Not connected")]
            
        results = self.query_engine.validate_entities(entities)
        
        # Compile report
        report = compile_reality_report(results)
        
        return [TextContent(type="text", text=report)]
    
    async def _handle_active_files(self) -> str:
        """Handle active_files resource read."""
        self._ensure_connected()
        
        if self.query_engine is None:
            return "Error: Not connected"
            
        files = self.query_engine.get_active_files()
        return compile_file_list(files)
    
    def _ensure_connected(self) -> None:
        """Ensure query engine is connected."""
        if not self.query_engine:
            self.query_engine = QueryEngine(
                uri=MEMGRAPH_URI,
                user=MEMGRAPH_USER,
                password=MEMGRAPH_PASSWORD,
            )
            self.query_engine.connect()
    
    def _extract_entities(self, code: str) -> list[str]:
        """
        Extract potential entity names from code snippet.
        
        Looks for:
        - CamelCase identifiers (class names)
        - snake_case identifiers (function names)
        - Import statements
        """
        entities = []
        
        # CamelCase (class names like AuthService, UserManager)
        camel_pattern = r'\b[A-Z][a-zA-Z0-9]+\b'
        entities.extend(re.findall(camel_pattern, code))
        
        # Function calls like func_name()
        func_pattern = r'\b([a-z_][a-z0-9_]+)\s*\('
        entities.extend(re.findall(func_pattern, code))
        
        # Import statements
        import_pattern = r'(?:from|import)\s+([a-zA-Z0-9_.]+)'
        for match in re.findall(import_pattern, code):
            # Get the last part of the import
            parts = match.split(".")
            if parts:
                entities.append(parts[-1])
        
        # File references (with extensions)
        file_pattern = r'["\']([a-zA-Z0-9_/-]+\.[a-z]+)["\']'
        entities.extend(re.findall(file_pattern, code))
        
        # Filter out common keywords and short names
        keywords = {"if", "else", "for", "while", "def", "class", "return", "import", "from", "True", "False", "None"}
        entities = [e for e in entities if e not in keywords and len(e) > 2]
        
        return entities
    
    async def run(self) -> None:
        """Run the MCP server."""
        logger.info("Starting Helix MCP Server")
        
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )


async def main() -> None:
    """Main entry point for the MCP server."""
    server = HelixMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
