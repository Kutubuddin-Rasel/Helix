"""
PII Shield Module - Project Helix Graph B Orchestrator

Implements PII detection and anonymization using pattern-based detection.
All text data MUST pass through this module before being written to Graph B.

Security First: No data touches the DB without PII scrubbing.

Note: This is a lightweight version that uses regex patterns instead of
the full Presidio NER pipeline (which requires 400MB spaCy model).
For production, use the full Presidio with spaCy NER.
"""

import re
from typing import Any, Union

import structlog

logger = structlog.get_logger()

# Replacement token
REDACTED_TOKEN = "<REDACTED_PII>"

# PII patterns for detection (raw patterns for documentation)
_PII_PATTERN_DEFINITIONS = [
    # Email addresses
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', "EMAIL"),
    
    # IP addresses (IPv4)
    (r'\b(?:\d{1,3}\.){3}\d{1,3}\b', "IP_ADDRESS"),
    
    # Phone numbers (various formats)
    (r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b', "PHONE"),
    
    # Credit card numbers (basic)
    (r'\b(?:\d{4}[-\s]?){4}\b', "CREDIT_CARD"),
    
    # SSN
    (r'\b\d{3}-\d{2}-\d{4}\b', "SSN"),
    
    # API Keys - OpenAI
    (r'sk-[a-zA-Z0-9]{20,}', "API_KEY"),
    
    # API Keys - GitHub PAT
    (r'ghp_[a-zA-Z0-9]{36,}', "API_KEY"),
    (r'gho_[a-zA-Z0-9]{36,}', "API_KEY"),
    
    # API Keys - Slack
    (r'xoxb-[a-zA-Z0-9\-]+', "API_KEY"),
    (r'xoxp-[a-zA-Z0-9\-]+', "API_KEY"),
    
    # API Keys - AWS Access Key
    (r'AKIA[A-Z0-9]{16}', "API_KEY"),
    
    # API Keys - Google
    (r'AIza[a-zA-Z0-9_-]{35}', "API_KEY"),
    
    # Bearer tokens
    (r'[Bb]earer\s+[a-zA-Z0-9\-_.]+', "TOKEN"),
    
    # Generic secrets pattern (key=value with sensitive names)
    (r'(?i)(?:password|secret|api_key|apikey|token|auth)\s*[=:]\s*["\']?[^\s"\']+["\']?', "SECRET"),
]

# Pre-compiled patterns for performance (compiled once at module load)
# This avoids recompiling regex on every scrub_text() call
PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), pii_type) for pattern, pii_type in _PII_PATTERN_DEFINITIONS
]


def scrub_text(text: str) -> str:
    """
    Scrub PII from the given text, replacing with <REDACTED_PII>.
    
    Uses pattern-based detection for lightweight operation.
    
    Args:
        text: The text to sanitize
        
    Returns:
        Sanitized text with PII replaced by <REDACTED_PII>
    """
    if not text or not text.strip():
        return text
    
    result = text
    detected_types = set()
    
    for compiled_pattern, pii_type in PII_PATTERNS:
        matches = compiled_pattern.findall(result)
        if matches:
            detected_types.add(pii_type)
            result = compiled_pattern.sub(REDACTED_TOKEN, result)
    
    if detected_types:
        logger.warning(
            "PII detected and scrubbed",
            pii_types=list(detected_types),
        )
    
    return result


def scrub_event(event: dict[str, Any]) -> dict[str, Any]:
    """
    Scrub PII from all string fields in an event dictionary.
    
    Args:
        event: The event dictionary to sanitize
        
    Returns:
        Sanitized event dictionary
    """
    sanitized: dict[str, Any] = {}
    
    for key, value in event.items():
        if isinstance(value, str):
            sanitized[key] = scrub_text(value)
        elif isinstance(value, dict):
            sanitized[key] = scrub_event(value)
        elif isinstance(value, list):
            scrubbed_list: list[Any] = [
                scrub_text(item) if isinstance(item, str) else item
                for item in value
            ]
            sanitized[key] = scrubbed_list
        else:
            sanitized[key] = value
    
    return sanitized


def full_scrub(text: str) -> str:
    """
    Complete PII scrubbing - alias for scrub_text.
    
    In production with spaCy, this would do multi-pass scrubbing.
    """
    return scrub_text(text)


# For compatibility with imports
def detect_pii(text: str) -> list[tuple[str, str]]:
    """
    Detect PII in text (lightweight version).
    
    Returns list of (matched_text, pii_type) tuples.
    """
    if not text:
        return []
    
    results = []
    for pattern, pii_type in PII_PATTERNS:
        matches = re.findall(pattern, text)
        for match in matches:
            results.append((match, pii_type))
    
    return results
