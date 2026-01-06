# Contributing to Project Helix

Welcome to Project Helix - the Dual-Graph Cognitive Engine. This document outlines the strict standards required for all contributions.

## The 5 Commandments

All code must adhere to these non-negotiable rules:

### 1. 🔒 No Ports on 0.0.0.0
All services must bind to `127.0.0.1` only. Never expose ports to the network.
```yaml
# ✅ CORRECT
ports:
  - "127.0.0.1:7687:7687"

# ❌ FORBIDDEN
ports:
  - "7687:7687"
```

### 2. 📋 No Text Logs (JSON Only)
All logs must be structured JSON for observability tooling.
```bash
export HELIX_LOG_FORMAT=json
```

### 3. 🛡️ No PII in Database
All text entering Graph B must pass through the PII Scrubber. No exceptions.
```python
from graph_b_orchestrator.security import scrub_event
sanitized = scrub_event(raw_data)
```

### 4. ✍️ Single-Writer Pattern Only
**Rust Observer** writes to Graph A only. **Python Orchestrator** writes to Graph B only.
Communication is exclusively via Redis Streams.

### 5. 🔐 Strict Types Always
- Python: `mypy --strict` must pass
- Rust: `cargo clippy -- -D warnings` must pass

---

## Tooling

| Component | Tool | Lock File |
|-----------|------|-----------|
| Python | `uv` (or pip-tools) | `requirements.lock.txt` |
| Rust | `cargo` | `Cargo.lock` |
| Infrastructure | `docker compose` | N/A |

### Setup

```bash
# 1. Start infrastructure
docker compose up -d

# 2. Initialize database schema
docker exec -i helix-memgraph mgconsole < scripts/init-memgraph.cypher

# 3. Python dependencies
cd graph-b-orchestrator && pip install -e ".[dev]"

# 4. Rust build
cd graph-a-observer && cargo build --release
```

---

## Pre-Commit Checklist

Before submitting a PR, run:

```bash
# Full compliance audit
./scripts/audit_compliance.sh

# Must exit with code 0
```

---

## Architecture Rules

### Graph A (The Map) - Rust Observer
- Source: File System (tree-sitter parsing)
- Nodes: `File`, `Function`, `Class`, `Import`
- Written by: Rust Observer only

### Graph B (The Story) - Python Orchestrator  
- Source: Redis Streams (from Rust)
- Nodes: `Episode`, `Summary`, `Session`
- Written by: Python Orchestrator only
- Must have `[:AFFECTS]` edge to Graph A nodes

---

## Release Process

1. Run `./scripts/audit_compliance.sh`
2. All checks must PASS (0 failures)
3. Tag: `git tag -a v1.x.x -m "Release notes"`
4. Push: `git push origin v1.x.x`
