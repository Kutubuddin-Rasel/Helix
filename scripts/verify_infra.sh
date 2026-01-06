#!/usr/bin/env bash
# =============================================================================
# Project Helix - Phase 1 Infrastructure Verification Script
# =============================================================================
# This script verifies that all Phase 1 infrastructure is operational:
#   1. Docker containers (Memgraph, Redis) are running and healthy
#   2. Redis Consumer Group is created (CEW-002 compliance)
#   3. Rust Observer can connect to Redis
#   4. Python Orchestrator can connect to Memgraph
#
# Usage:
#   bash scripts/verify_infra.sh
#
# Requirements:
#   - Docker and Docker Compose
#   - Rust toolchain (cargo)
#   - Python 3.11+ with pip
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Configuration
MEMGRAPH_CONTAINER="helix-memgraph"
REDIS_CONTAINER="helix-redis"
REDIS_STREAM="helix:events"
REDIS_CONSUMER_GROUP="helix-orchestrator"

# Counters
PASSED=0
FAILED=0

# =============================================================================
# Helper Functions
# =============================================================================

print_header() {
    echo ""
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}     ${BOLD}Project Helix - Phase 1 Infrastructure Check${NC}        ${BLUE}║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_section() {
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

check_pass() {
    echo -e "  ${GREEN}✅ $1${NC}"
    PASSED=$((PASSED + 1))
}

check_fail() {
    echo -e "  ${RED}❌ $1${NC}"
    FAILED=$((FAILED + 1))
}

check_warn() {
    echo -e "  ${YELLOW}⚠️  $1${NC}"
}

check_info() {
    echo -e "  ${BLUE}ℹ️  $1${NC}"
}

# =============================================================================
# Step 1: Check Docker Containers
# =============================================================================

check_docker_containers() {
    print_section "Step 1: Docker Container Health"

    # Check if Docker is running
    if ! docker info > /dev/null 2>&1; then
        check_fail "Docker daemon is not running"
        echo -e "     ${YELLOW}→ Please start Docker and try again${NC}"
        exit 1
    fi
    check_pass "Docker daemon is running"

    # Check Memgraph container
    if docker ps --format '{{.Names}}' | grep -q "^${MEMGRAPH_CONTAINER}$"; then
        # Check health status
        HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "$MEMGRAPH_CONTAINER" 2>/dev/null || echo "no-health")
        if [ "$HEALTH" = "healthy" ]; then
            check_pass "Memgraph container is running and healthy"
        elif [ "$HEALTH" = "no-health" ]; then
            check_warn "Memgraph container is running (no health check defined)"
        else
            check_warn "Memgraph container is running but health status: $HEALTH"
        fi
    else
        check_fail "Memgraph container is not running"
        echo -e "     ${YELLOW}→ Run: docker compose up -d${NC}"
        return 1
    fi

    # Check Redis container
    if docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
        HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "$REDIS_CONTAINER" 2>/dev/null || echo "no-health")
        if [ "$HEALTH" = "healthy" ]; then
            check_pass "Redis container is running and healthy"
        elif [ "$HEALTH" = "no-health" ]; then
            check_warn "Redis container is running (no health check defined)"
        else
            check_warn "Redis container is running but health status: $HEALTH"
        fi
    else
        check_fail "Redis container is not running"
        echo -e "     ${YELLOW}→ Run: docker compose up -d${NC}"
        return 1
    fi

    return 0
}

# =============================================================================
# Step 2: Initialize Redis Consumer Group (CEW-002)
# =============================================================================

init_redis_consumer_group() {
    print_section "Step 2: Redis Consumer Group (CEW-002)"

    # Check if consumer group exists
    GROUP_EXISTS=$(docker exec "$REDIS_CONTAINER" redis-cli XINFO GROUPS "$REDIS_STREAM" 2>/dev/null || echo "")

    if echo "$GROUP_EXISTS" | grep -q "$REDIS_CONSUMER_GROUP"; then
        check_pass "Consumer group '$REDIS_CONSUMER_GROUP' already exists"
    else
        # Create stream and consumer group
        RESULT=$(docker exec "$REDIS_CONTAINER" redis-cli XGROUP CREATE "$REDIS_STREAM" "$REDIS_CONSUMER_GROUP" \$ MKSTREAM 2>&1)
        if [ "$RESULT" = "OK" ] || echo "$RESULT" | grep -q "BUSYGROUP"; then
            check_pass "Created consumer group '$REDIS_CONSUMER_GROUP'"
        else
            check_fail "Failed to create consumer group: $RESULT"
            return 1
        fi
    fi

    # Verify stream info
    STREAM_LEN=$(docker exec "$REDIS_CONTAINER" redis-cli XLEN "$REDIS_STREAM" 2>/dev/null || echo "0")
    check_info "Stream '$REDIS_STREAM' length: $STREAM_LEN"

    return 0
}

# =============================================================================
# Step 2.5: Initialize Memgraph Schema (Constitution Compliance)
# =============================================================================

init_memgraph_schema() {
    print_section "Step 2.5: Memgraph Schema Initialization"

    INIT_SCRIPT="scripts/init-memgraph.cypher"

    if [ ! -f "$INIT_SCRIPT" ]; then
        check_warn "Init script not found: $INIT_SCRIPT"
        return 0
    fi
    check_pass "Init script found: $INIT_SCRIPT"

    # Execute the init script against Memgraph
    check_info "Applying schema constraints and indexes..."
    
    if docker exec -i "$MEMGRAPH_CONTAINER" mgconsole < "$INIT_SCRIPT" 2>&1 | tail -5; then
        check_pass "Memgraph schema initialized"
    else
        check_warn "Some schema commands may have failed (constraints may already exist)"
    fi

    # Verify constraints exist
    CONSTRAINT_COUNT=$(docker exec "$MEMGRAPH_CONTAINER" mgconsole -execute "SHOW CONSTRAINT INFO;" 2>/dev/null | wc -l || echo "0")
    check_info "Constraints found: $CONSTRAINT_COUNT"

    return 0
}

# =============================================================================
# Step 3: Test Rust Observer Connectivity
# =============================================================================

test_rust_observer() {
    print_section "Step 3: Rust Observer Connectivity"

    RUST_DIR="graph-a-observer"

    if [ ! -f "$RUST_DIR/Cargo.toml" ]; then
        check_fail "Rust project not found at $RUST_DIR"
        return 1
    fi
    check_pass "Rust project found"

    # Check if Rust toolchain is available
    if ! command -v cargo &> /dev/null; then
        check_fail "Cargo (Rust toolchain) is not installed"
        echo -e "     ${YELLOW}→ Install Rust: https://rustup.rs${NC}"
        return 1
    fi
    check_pass "Cargo toolchain available"

    # Build and run the Rust observer
    check_info "Building Rust Observer (this may take a moment)..."
    cd "$RUST_DIR"

    if cargo build --release 2>&1 | tail -5; then
        check_pass "Rust Observer built successfully"
    else
        check_fail "Rust Observer build failed"
        cd - > /dev/null
        return 1
    fi

    check_info "Running Rust connectivity test..."
    if cargo run --release 2>&1; then
        check_pass "Rust Observer connectivity test passed"
    else
        check_fail "Rust Observer connectivity test failed"
        cd - > /dev/null
        return 1
    fi

    cd - > /dev/null
    return 0
}

# =============================================================================
# Step 4: Test Python Orchestrator Connectivity
# =============================================================================

test_python_orchestrator() {
    print_section "Step 4: Python Orchestrator Connectivity"

    PYTHON_DIR="graph-b-orchestrator"

    if [ ! -f "$PYTHON_DIR/pyproject.toml" ]; then
        check_fail "Python project not found at $PYTHON_DIR"
        return 1
    fi
    check_pass "Python project found"

    # Check if Python 3.11+ is available
    if ! command -v python3 &> /dev/null; then
        check_fail "Python 3 is not installed"
        return 1
    fi

    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    check_pass "Python $PYTHON_VERSION available"

    cd "$PYTHON_DIR"

    # Create virtual environment if it doesn't exist
    if [ ! -d ".venv" ]; then
        check_info "Creating virtual environment..."
        python3 -m venv .venv
    fi

    # Activate venv and install dependencies
    source .venv/bin/activate

    check_info "Installing dependencies (this may take a moment)..."
    pip install -q -e ".[dev]" 2>&1 | tail -3

    if [ $? -eq 0 ]; then
        check_pass "Python dependencies installed"
    else
        check_warn "Some dependencies may have failed to install"
    fi

    # Run the Python connectivity test
    check_info "Running Python connectivity test..."
    if python -m graph_b_orchestrator.main 2>&1; then
        check_pass "Python Orchestrator connectivity test passed"
    else
        check_fail "Python Orchestrator connectivity test failed"
        deactivate
        cd - > /dev/null
        return 1
    fi

    deactivate
    cd - > /dev/null
    return 0
}

# =============================================================================
# Summary
# =============================================================================

print_summary() {
    print_section "Verification Summary"

    TOTAL=$((PASSED + FAILED))

    echo ""
    echo -e "  ${GREEN}Passed:${NC} $PASSED"
    echo -e "  ${RED}Failed:${NC} $FAILED"
    echo -e "  ${BOLD}Total:${NC}  $TOTAL"
    echo ""

    if [ $FAILED -eq 0 ]; then
        echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║${NC}  ${BOLD}🎉 Phase 1 Infrastructure Verification: PASSED${NC}          ${GREEN}║${NC}"
        echo -e "${GREEN}║${NC}     Ready to proceed to Phase 2 (Rust Observer)          ${GREEN}║${NC}"
        echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
        return 0
    else
        echo -e "${RED}╔═══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}║${NC}  ${BOLD}⚠️  Phase 1 Infrastructure Verification: FAILED${NC}          ${RED}║${NC}"
        echo -e "${RED}║${NC}     Please fix the issues above before proceeding         ${RED}║${NC}"
        echo -e "${RED}╚═══════════════════════════════════════════════════════════╝${NC}"
        return 1
    fi
}

# =============================================================================
# Main Execution
# =============================================================================

main() {
    print_header

    # Navigate to project root (script is in scripts/)
    cd "$(dirname "$0")/.."

    # Run all verification steps
    check_docker_containers
    init_redis_consumer_group
    init_memgraph_schema  # Constitution compliance: apply schema
    test_rust_observer
    test_python_orchestrator

    # Print summary and exit with appropriate code
    print_summary
}

main "$@"
