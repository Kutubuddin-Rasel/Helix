#!/usr/bin/env bash
# ==============================================================================
# Project Helix - v1.0 Production Compliance Audit
# ==============================================================================
# The Gatekeeper: If this script exits with code 0, the project is
# certified as v1.0 Production Ready.
#
# Usage: ./scripts/audit_compliance.sh
# Exit: 0 = PASS (Ready for git tag), 1 = FAIL
# ==============================================================================

# Disable strict mode - we handle errors manually
set +e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# Counters
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
SKIP_COUNT=0

# Navigate to project root
cd "$(dirname "$0")/.." || exit 1

pass() {
    echo -e "  ${GREEN}✅ PASS${NC} $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    echo -e "  ${RED}❌ FAIL${NC} $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

warn() {
    echo -e "  ${YELLOW}⚠️  WARN${NC} $1"
    WARN_COUNT=$((WARN_COUNT + 1))
}

skip() {
    echo -e "  ${BLUE}⏭️  SKIP${NC} $1"
    SKIP_COUNT=$((SKIP_COUNT + 1))
}

header() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  $1${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
}

subheader() {
    echo ""
    echo -e "${BLUE}▶ $1${NC}"
}

# ==============================================================================
# BANNER
# ==============================================================================
echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     PROJECT HELIX - v1.0 PRODUCTION COMPLIANCE AUDIT          ║${NC}"
echo -e "${BOLD}║           The Gatekeeper Quality Assurance Suite              ║${NC}"
echo -e "${BOLD}╚═══════════════════════════════════════════════════════════════╝${NC}"

# ==============================================================================
# PILLAR 1: ARCHITECTURE & RELIABILITY
# ==============================================================================
header "PILLAR 1: Architecture & Reliability"

subheader "Lock Files (Reproducibility)"
if [ -f "graph-b-orchestrator/uv.lock" ] || [ -f "graph-b-orchestrator/poetry.lock" ] || [ -f "graph-b-orchestrator/requirements.lock.txt" ]; then
    pass "Python lock file exists"
else
    fail "Python lock file missing (uv.lock/poetry.lock/requirements.lock.txt)"
fi

if [ -f "graph-a-observer/Cargo.lock" ]; then
    pass "Rust Cargo.lock exists"
else
    fail "Cargo.lock missing"
fi

subheader "Docker Health Checks"
MEMGRAPH_HEALTH=$(grep -A5 "memgraph:" docker-compose.yml 2>/dev/null | grep -c "healthcheck:" || echo "0")
REDIS_HEALTH=$(grep -A5 "redis:" docker-compose.yml 2>/dev/null | grep -c "healthcheck:" || echo "0")
TOTAL_HEALTH=$(grep -c "healthcheck:" docker-compose.yml 2>/dev/null || echo "0")

if [ "$TOTAL_HEALTH" -ge 2 ]; then
    pass "Health checks defined (${TOTAL_HEALTH} services)"
else
    fail "Missing health checks (found: ${TOTAL_HEALTH})"
fi

subheader "Backpressure (MAXLEN)"
if grep -q "MAXLEN" graph-a-observer/src/publisher.rs 2>/dev/null; then
    MAXLEN=$(grep "STREAM_MAXLEN" graph-a-observer/src/publisher.rs | grep -o '[0-9]*' | head -1)
    pass "Redis MAXLEN policy: ${MAXLEN:-configured}"
else
    fail "MAXLEN policy not found in publisher.rs"
fi

subheader "ACK Semantics"
if grep -q "xack" graph-b-orchestrator/src/graph_b_orchestrator/consumer.py 2>/dev/null; then
    pass "XACK semantics implemented"
else
    fail "XACK not found in consumer"
fi

# ==============================================================================
# PILLAR 2: SECURITY (ZERO TRUST)
# ==============================================================================
header "PILLAR 2: Security (Zero Trust)"

subheader "Network Isolation (Port Binding)"
# Check docker-compose.yml for 127.0.0.1 bindings
PORTS_OK=true
if ! grep -q "127.0.0.1:7687" docker-compose.yml 2>/dev/null; then
    PORTS_OK=false
fi
if ! grep -q "127.0.0.1:6889" docker-compose.yml 2>/dev/null; then
    PORTS_OK=false  
fi

if [ "$PORTS_OK" = true ]; then
    pass "All ports bound to 127.0.0.1 (localhost only)"
else
    fail "Ports exposed on 0.0.0.0 - SECURITY RISK"
fi

# Live port check (if containers are running)
if docker ps 2>/dev/null | grep -q "helix"; then
    EXPOSED=$(docker port helix-memgraph 2>/dev/null | grep "0.0.0.0" || echo "")
    if [ -z "$EXPOSED" ]; then
        pass "Live container ports correctly isolated"
    else
        fail "Container exposes ports on 0.0.0.0: ${EXPOSED}"
    fi
else
    skip "Live port check (containers not running)"
fi

subheader "Secret Scan"
# Scan for hardcoded secrets
SECRET_FOUND=false
if grep -rq "sk-[a-zA-Z0-9]" graph-b-orchestrator/src/ 2>/dev/null; then
    SECRET_FOUND=true
    fail "Hardcoded API key pattern (sk-*) found"
fi
if grep -rq 'password\s*=\s*"[^"]\+"' graph-b-orchestrator/src/ 2>/dev/null; then
    SECRET_FOUND=true
    fail "Hardcoded password found"
fi
if grep -rq "sk-[a-zA-Z0-9]" graph-a-observer/src/ 2>/dev/null; then
    SECRET_FOUND=true
    fail "Hardcoded API key in Rust code"
fi

if [ "$SECRET_FOUND" = false ]; then
    pass "No hardcoded secrets detected"
fi

subheader "PII Scrubber"
if grep -q "scrub_event\|full_scrub" graph-b-orchestrator/src/graph_b_orchestrator/consumer.py 2>/dev/null; then
    pass "PII scrubber integrated in pipeline"
else
    fail "PII scrubber not in consumer pipeline"
fi

if grep -q "REDACTED" graph-b-orchestrator/src/graph_b_orchestrator/security.py 2>/dev/null; then
    pass "Redaction tokens used (not deletion)"
else
    fail "Redaction tokens not found"
fi

# ==============================================================================
# PILLAR 3: CODE QUALITY
# ==============================================================================
header "PILLAR 3: Code Quality"

subheader "Type Safety - Python"
if [ -f "graph-b-orchestrator/src/graph_b_orchestrator/py.typed" ]; then
    pass "PEP 561 py.typed marker exists"
else
    fail "py.typed marker missing"
fi

if grep -q "strict = true" graph-b-orchestrator/pyproject.toml 2>/dev/null; then
    pass "mypy strict mode configured"
else
    fail "mypy strict mode not configured"
fi

# Run mypy if available
if command -v mypy &>/dev/null; then
    echo -n "  Running mypy --strict... "
    cd graph-b-orchestrator
    if mypy --strict src/graph_b_orchestrator/ 2>&1 | tail -1 | grep -q "Success\|no issues"; then
        echo -e "${GREEN}✅ PASS${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        MYPY_ERRORS=$(mypy --strict src/graph_b_orchestrator/ 2>&1 | grep -c "error:" || echo "0")
        if [ "$MYPY_ERRORS" -eq 0 ]; then
            echo -e "${GREEN}✅ PASS${NC}"
            PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo -e "${YELLOW}⚠️  WARN${NC} ${MYPY_ERRORS} type errors"
            WARN_COUNT=$((WARN_COUNT + 1))
        fi
    fi
    cd - >/dev/null
else
    skip "mypy not installed"
fi

subheader "Type Safety - Rust"
if command -v cargo &>/dev/null; then
    echo -n "  Running cargo clippy... "
    cd graph-a-observer
    if cargo clippy -- -D warnings 2>&1 | tail -1 | grep -q "Finished\|warning: 0 warnings"; then
        echo -e "${GREEN}✅ PASS${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        CLIPPY_WARNS=$(cargo clippy 2>&1 | grep -c "^warning:" || echo "0")
        if [ "$CLIPPY_WARNS" -eq 0 ]; then
            echo -e "${GREEN}✅ PASS${NC}"
            PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo -e "${YELLOW}⚠️  WARN${NC} ${CLIPPY_WARNS} clippy warnings"
            WARN_COUNT=$((WARN_COUNT + 1))
        fi
    fi
    cd - >/dev/null
else
    skip "cargo not installed"
fi

# ==============================================================================
# PILLAR 4: DATA INTEGRITY
# ==============================================================================
header "PILLAR 4: Data Integrity"

subheader "Hard Edge Mandate"
if grep -q ":AFFECTS" graph-b-orchestrator/src/graph_b_orchestrator/graph_writer.py 2>/dev/null; then
    pass "Hard edges [:AFFECTS] to Graph A"
else
    fail "Hard edges not implemented"
fi

subheader "Schema Constraints"
if grep -q "ASSERT.*path IS UNIQUE" scripts/init-memgraph.cypher 2>/dev/null; then
    pass "File.path unique constraint defined"
else
    fail "File.path constraint missing"
fi

if grep -qi "embedding" scripts/init-memgraph.cypher 2>/dev/null; then
    pass "Embedding indexes defined"
else
    warn "Embedding indexes not in schema"
fi

# ==============================================================================
# PILLAR 5: OBSERVABILITY
# ==============================================================================
header "PILLAR 5: Observability"

subheader "Structured Logging"
if grep -q "JSONRenderer" graph-b-orchestrator/src/graph_b_orchestrator/logging_config.py 2>/dev/null; then
    pass "JSON logging configured (HELIX_LOG_FORMAT)"
else
    fail "JSON logging not configured"
fi

subheader "Metrics"
if [ -f "graph-b-orchestrator/src/graph_b_orchestrator/metrics.py" ]; then
    if grep -q "events_processed_total\|processing_latency" graph-b-orchestrator/src/graph_b_orchestrator/metrics.py 2>/dev/null; then
        pass "Metrics module with required counters"
    else
        warn "Metrics module exists but missing required counters"
    fi
else
    fail "Metrics module not found"
fi

subheader "Runtime Check"
# Check if Memgraph is responding
if command -v nc &>/dev/null && nc -z 127.0.0.1 7687 2>/dev/null; then
    pass "Memgraph responding on localhost:7687"
else
    if docker ps 2>/dev/null | grep -q "helix-memgraph"; then
        pass "Memgraph container running"
    else
        skip "Memgraph not running (start with: docker compose up -d)"
    fi
fi

# ==============================================================================
# SUMMARY
# ==============================================================================
header "AUDIT SUMMARY"

TOTAL=$((PASS_COUNT + FAIL_COUNT + WARN_COUNT + SKIP_COUNT))

echo ""
echo -e "  ${GREEN}PASS:${NC} ${PASS_COUNT}"
echo -e "  ${RED}FAIL:${NC} ${FAIL_COUNT}"
echo -e "  ${YELLOW}WARN:${NC} ${WARN_COUNT}"
echo -e "  ${BLUE}SKIP:${NC} ${SKIP_COUNT}"
echo -e "  ${BOLD}─────────────${NC}"
echo -e "  TOTAL: ${TOTAL}"
echo ""

if [ "$FAIL_COUNT" -eq 0 ]; then
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                                                               ║${NC}"
    echo -e "${GREEN}║   ✅  VERDICT: v1.0 PRODUCTION READY                          ║${NC}"
    echo -e "${GREEN}║                                                               ║${NC}"
    echo -e "${GREEN}║   You may now run: git tag -a v1.0.0 -m \"Production Release\"  ║${NC}"
    echo -e "${GREEN}║                                                               ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    exit 0
else
    echo -e "${RED}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║                                                               ║${NC}"
    echo -e "${RED}║   ❌  VERDICT: NOT READY FOR PRODUCTION                       ║${NC}"
    echo -e "${RED}║                                                               ║${NC}"
    echo -e "${RED}║   Fix ${FAIL_COUNT} failing check(s) before release.                      ║${NC}"
    echo -e "${RED}║                                                               ║${NC}"
    echo -e "${RED}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    exit 1
fi
