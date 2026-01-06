#!/usr/bin/env python3
"""
Graph B Orchestrator - Module Entry Point

Enables running the package with: python -m graph_b_orchestrator

Usage:
  python -m graph_b_orchestrator          # Run the consumer
  python -m graph_b_orchestrator --squash # Run one-time squash
"""

import sys
from .main import main

if __name__ == "__main__":
    sys.exit(main())
