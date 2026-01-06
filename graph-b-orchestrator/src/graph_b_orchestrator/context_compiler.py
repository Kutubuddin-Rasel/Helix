"""
Context Compiler Module - Project Helix Graph B Orchestrator

The "Translator" that converts raw database nodes into structured
Markdown that LLMs can understand and reason about.

This is the bridge between complex Graph/Vector data and clean prompts.
"""

from datetime import datetime
from typing import Optional

import structlog

from .query_engine import RealityCheckResult, SearchResult
from .schemas import FileInfo

logger = structlog.get_logger()


def compile_context(
    search_results: list[SearchResult],
    reality_checks: Optional[dict[str, RealityCheckResult]] = None,
    include_structure: bool = True,
) -> str:
    """
    Compile search results into structured Markdown for LLM consumption.
    
    Formats Graph A (structure) and Graph B (history) data into a
    clean, compressed context section that LLMs can understand.
    
    Args:
        search_results: List of SearchResult from hybrid search
        reality_checks: Optional dict of entity validation results
        include_structure: Whether to include code structure section
        
    Returns:
        Markdown formatted context string
    """
    logger.debug("Compiling context", results=len(search_results))
    
    sections = []
    
    # Header
    sections.append("## Helix Context")
    sections.append("")
    
    # Relevant History section
    if search_results:
        sections.append("### Relevant History")
        sections.append("")
        
        for result in search_results:
            time_ago = _format_time_ago(result.timestamp)
            node_label = "Summarized" if result.node_type == "Summary" else "Recent"
            
            # Format linked files
            files_str = ", ".join(f"`{_basename(f)}`" for f in result.linked_files[:3])
            if len(result.linked_files) > 3:
                files_str += f" +{len(result.linked_files) - 3} more"
            
            # Format the entry
            text_preview = _truncate(result.text, 100)
            line = f"- **{node_label}** ({time_ago}): {text_preview}"
            if files_str:
                line += f" *(Linked to: {files_str})*"
            
            sections.append(line)
        
        sections.append("")
    
    # Active Reality section (code structure)
    if include_structure and search_results:
        sections.append("### Active Reality (Graph A)")
        sections.append("")
        
        # Collect unique files and their functions
        file_functions: dict[str, list[str]] = {}
        for result in search_results:
            for file_path in result.linked_files:
                if file_path not in file_functions:
                    file_functions[file_path] = []
                for func in result.linked_functions:
                    if func and func not in file_functions[file_path]:
                        file_functions[file_path].append(func)
        
        for file_path, functions in file_functions.items():
            basename = _basename(file_path)
            if functions:
                funcs_str = ", ".join(f"`{f}`" for f in functions[:5])
                if len(functions) > 5:
                    funcs_str += f" +{len(functions) - 5} more"
                sections.append(f"- `{basename}` contains: {funcs_str}")
            else:
                sections.append(f"- `{basename}` (no functions extracted)")
        
        sections.append("")
    
    # Reality Check section (entity validation)
    if reality_checks:
        missing_results = [r for r in reality_checks.values() if r.status == "MISSING"]
        
        if missing_results:
            sections.append("### Reality Check ⚠️")
            sections.append("")
            
            for check_result in missing_results:
                sections.append(f"- `{check_result.entity_name}` is **MISSING** from codebase")
            
            sections.append("")
    
    # Compile final string
    context = "\n".join(sections).strip()
    
    logger.debug("Context compiled", length=len(context))
    
    return context


def compile_reality_report(
    entity_results: dict[str, RealityCheckResult],
) -> str:
    """
    Compile a reality check report for entity validation.
    
    Args:
        entity_results: Dict mapping entity name to validation result
        
    Returns:
        Markdown formatted reality report
    """
    sections = []
    
    sections.append("## Reality Check Report")
    sections.append("")
    
    # Group by status
    exists = [r for r in entity_results.values() if r.status == "EXISTS"]
    missing = [r for r in entity_results.values() if r.status == "MISSING"]
    multiple = [r for r in entity_results.values() if r.status == "MULTIPLE"]
    
    if exists:
        sections.append("### Verified Entities ✅")
        sections.append("")
        for result in exists:
            location = f" in `{_basename(result.file_path)}`" if result.file_path else ""
            sections.append(f"- `{result.entity_name}` ({result.node_type}){location}")
        sections.append("")
    
    if missing:
        sections.append("### Missing Entities ❌")
        sections.append("")
        for result in missing:
            sections.append(f"- `{result.entity_name}` - NOT FOUND in codebase")
        sections.append("")
    
    if multiple:
        sections.append("### Ambiguous Entities ⚠️")
        sections.append("")
        for result in multiple:
            sections.append(f"- `{result.entity_name}` - Multiple matches found")
        sections.append("")
    
    # Summary
    sections.append("### Summary")
    sections.append(f"- Total checked: {len(entity_results)}")
    sections.append(f"- Verified: {len(exists)}")
    sections.append(f"- Missing: {len(missing)}")
    sections.append(f"- Ambiguous: {len(multiple)}")
    
    return "\n".join(sections).strip()


def compile_file_list(files: list[FileInfo]) -> str:
    """
    Compile a list of active files into Markdown.
    
    Args:
        files: List of FileInfo objects from query_engine.get_active_files()
        
    Returns:
        Markdown formatted file list
    """
    sections: list[str] = []
    
    sections.append("## Active Files (Graph A)")
    sections.append("")
    sections.append(f"Total: {len(files)} files")
    sections.append("")
    
    # Group by language
    by_language: dict[str, list[FileInfo]] = {}
    for f in files:
        lang = f.language or "Unknown"
        if lang not in by_language:
            by_language[lang] = []
        by_language[lang].append(f)
    
    for lang, lang_files in sorted(by_language.items()):
        sections.append(f"### {lang}")
        sections.append("")
        for f in lang_files[:10]:
            basename = _basename(f.path)
            func_count = f.function_count
            sections.append(f"- `{basename}` ({func_count} functions)")
        if len(lang_files) > 10:
            sections.append(f"- ... and {len(lang_files) - 10} more")
        sections.append("")
    
    return "\n".join(sections).strip()


def _format_time_ago(timestamp: str) -> str:
    """Format a timestamp as relative time (e.g., '2 mins ago')."""
    if not timestamp:
        return "unknown time"
    
    try:
        # Try parsing ISO format
        if "T" in timestamp:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(timestamp)
        
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        delta = now - dt
        
        seconds = delta.total_seconds()
        
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            mins = int(seconds / 60)
            return f"{mins} min{'s' if mins != 1 else ''} ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = int(seconds / 86400)
            return f"{days} day{'s' if days != 1 else ''} ago"
    except Exception:
        return "unknown time"


def _truncate(text: str, max_length: int = 100) -> str:
    """Truncate text to max length with ellipsis."""
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def _basename(path: str) -> str:
    """Get the basename of a file path."""
    if not path:
        return ""
    return path.split("/")[-1]
