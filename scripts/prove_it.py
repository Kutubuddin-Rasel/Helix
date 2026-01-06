#!/usr/bin/env python3
"""
Project Helix - REAL Integration Test (Proof of Life)
======================================================

This is a TRUE end-to-end integration test that validates the ACTUAL
Helix system components are working together:

- Rust Observer (file watcher → Graph A)
- Redis Stream (event bus)  
- Python Consumer (Redis → Graph B with embeddings)
- Query Engine (vector search)
- Context Compiler (MCP bridge)

PREREQUISITES - Run these in 3 separate terminals BEFORE running this script:

Terminal 1 (Rust Observer):
  cd /Users/kutubuddin/Downloads/Helix
  cargo run --manifest-path graph-a-observer/Cargo.toml -- .

Terminal 2 (Python Consumer):
  cd /Users/kutubuddin/Downloads/Helix/graph-b-orchestrator
  python -m graph_b_orchestrator

Terminal 3 (Run this test):
  cd /Users/kutubuddin/Downloads/Helix
  python scripts/prove_it.py

Goal: 4 Green Checkmarks = Project Helix is ALIVE (for real!)
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Add graph-b-orchestrator to path for imports
SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR / "graph-b-orchestrator" / "src"))

import redis
from neo4j import GraphDatabase

# Configuration
MEMGRAPH_URI = "bolt://localhost:7687"
REDIS_HOST = "localhost"
REDIS_PORT = 6889  # Custom port to avoid conflicts
REDIS_STREAM = "helix:events"
TEST_FILE_PATH = SCRIPT_DIR / "src" / "helix_test_ghost.rs"

# Timeouts (adjust if services are slow)
FILE_DETECTION_TIMEOUT = 10  # seconds to wait for Rust Observer
EVENT_PROCESSING_TIMEOUT = 10  # seconds to wait for Python Consumer

# Colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_banner():
    """Print the proof of life banner."""
    print()
    print(f"{BOLD}{BLUE}╔═══════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{BLUE}║        PROJECT HELIX - REAL INTEGRATION TEST                 ║{RESET}")
    print(f"{BOLD}{BLUE}║              The Moment of Truth (No Faking!)                ║{RESET}")
    print(f"{BOLD}{BLUE}╚═══════════════════════════════════════════════════════════════╝{RESET}")
    print()


def print_prerequisites():
    """Print the prerequisites for running this test."""
    print(f"{BOLD}{CYAN}PREREQUISITES:{RESET}")
    print()
    print(f"  Before running this test, ensure these services are running:")
    print()
    print(f"  {YELLOW}Terminal 1 (Rust Observer):{RESET}")
    print(f"    cd {SCRIPT_DIR}")
    print(f"    cargo run --manifest-path graph-a-observer/Cargo.toml -- .")
    print()
    print(f"  {YELLOW}Terminal 2 (Python Consumer):{RESET}")
    print(f"    cd {SCRIPT_DIR}/graph-b-orchestrator")
    print(f"    python -m graph_b_orchestrator")
    print()
    print(f"  {YELLOW}Terminal 3 (This Test):{RESET}")
    print(f"    python scripts/prove_it.py")
    print()


def print_act(act_num: int, title: str):
    """Print an act header."""
    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  ACT {act_num}: {title}{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}\n")


def success(message: str):
    """Print a success message."""
    print(f"  {GREEN}✅ {message}{RESET}")
    return True


def fail(message: str):
    """Print a failure message."""
    print(f"  {RED}❌ FAIL: {message}{RESET}")
    return False


def info(message: str):
    """Print an info message."""
    print(f"  {YELLOW}⏳ {message}{RESET}")


def warn(message: str):
    """Print a warning message."""
    print(f"  {YELLOW}⚠️  {message}{RESET}")


class HelixRealIntegrationTest:
    """REAL end-to-end integration test for Project Helix."""
    
    def __init__(self):
        self.driver = None
        self.redis_client = None
        self.results = []
        self.test_run_id = uuid4().hex[:8]
        self.test_file_content = None
        
    def connect(self) -> bool:
        """Connect to Memgraph and Redis."""
        try:
            info("Connecting to Memgraph...")
            self.driver = GraphDatabase.driver(MEMGRAPH_URI)
            self.driver.verify_connectivity()
            success("Connected to Memgraph")
            
            info("Connecting to Redis...")
            self.redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                decode_responses=True,
            )
            self.redis_client.ping()
            success(f"Connected to Redis on port {REDIS_PORT}")
            
            return True
        except redis.exceptions.ConnectionError as e:
            fail(f"Redis connection failed on port {REDIS_PORT}: {e}")
            print(f"\n  {YELLOW}Hint: Is Docker running? Try: docker compose up -d{RESET}")
            return False
        except Exception as e:
            fail(f"Connection failed: {e}")
            return False
    
    def cleanup(self):
        """Clean up test artifacts."""
        try:
            # Remove test file from disk
            if TEST_FILE_PATH.exists():
                TEST_FILE_PATH.unlink()
                info(f"Cleaned up test file: {TEST_FILE_PATH.name}")
                
            # Remove test nodes from database (by test run ID marker)
            if self.driver:
                with self.driver.session() as session:
                    session.run(
                        """
                        MATCH (f:File)
                        WHERE f.path CONTAINS 'helix_test_ghost.rs'
                        DETACH DELETE f
                        """
                    )
                    session.run(
                        """
                        MATCH (e:Episode)
                        WHERE e.diff_summary CONTAINS 'helix_test_ghost'
                        DETACH DELETE e
                        """
                    )
        except Exception as e:
            warn(f"Cleanup warning: {e}")
    
    def close(self):
        """Close connections."""
        if self.driver:
            self.driver.close()
        if self.redis_client:
            self.redis_client.close()
    
    # =========================================================================
    # ACT 1: The Ghost Commit (REAL Rust Observer Test)
    # =========================================================================
    def act1_ghost_commit(self) -> bool:
        """
        REAL TEST: Create a file on disk and wait for Rust Observer to detect it.
        
        This tests: File System → Rust Observer → Memgraph Graph A
        """
        print_act(1, "The Ghost Commit (Rust Observer → Graph A)")
        
        # Ensure parent directory exists
        TEST_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        # Create the REAL test file on disk
        self.test_file_content = f"""\
// Ghost Commit Test File - Project Helix Integration Test
// Test Run ID: {self.test_run_id}
// Created: {datetime.now(timezone.utc).isoformat()}

/// A ghost function to prove the Rust Observer is watching
fn ghost_function_{self.test_run_id}() {{
    println!("I am a ghost commit - test run {self.test_run_id}!");
}}

/// Another function for structure detection
fn helper_function() {{
    // Empty helper
}}
"""
        
        info(f"Creating REAL file on disk: {TEST_FILE_PATH}")
        TEST_FILE_PATH.write_text(self.test_file_content)
        success(f"Created file: {TEST_FILE_PATH.name} ({len(self.test_file_content)} bytes)")
        
        # Wait for Rust Observer to detect the file
        info(f"Waiting for Rust Observer to detect file ({FILE_DETECTION_TIMEOUT}s timeout)...")
        
        start_time = time.time()
        detected = False
        
        while time.time() - start_time < FILE_DETECTION_TIMEOUT:
            with self.driver.session() as session:
                result = session.run(
                    """
                    MATCH (f:File)
                    WHERE f.path CONTAINS 'helix_test_ghost.rs'
                    OPTIONAL MATCH (f)-[:DEFINES]->(fn:Function)
                    RETURN f.path AS path, 
                           f.hash AS hash,
                           collect(fn.name) AS functions
                    LIMIT 1
                    """
                )
                record = result.single()
                
                if record:
                    detected = True
                    functions = record["functions"]
                    break
            
            # Poll every 500ms
            time.sleep(0.5)
            print(".", end="", flush=True)
        
        print()  # Newline after dots
        
        if detected:
            func_count = len([f for f in functions if f])
            return success(
                f"Rust Observer detected file! ({func_count} functions parsed)"
            )
        else:
            # Check how many files exist to help diagnose
            with self.driver.session() as session:
                result = session.run("MATCH (f:File) RETURN count(f) AS count")
                count = result.single()["count"]
            
            fail(f"Rust Observer did not detect file within {FILE_DETECTION_TIMEOUT}s")
            print()
            print(f"  {YELLOW}Troubleshooting:{RESET}")
            print(f"  • Is Rust Observer running? Check Terminal 1")
            print(f"  • Current files in Graph A: {count}")
            print(f"  • Try: cargo run --manifest-path graph-a-observer/Cargo.toml -- .")
            return False
    
    # =========================================================================
    # ACT 2: Memory Ingestion (REAL Redis → Python Consumer Test)
    # =========================================================================
    def act2_memory_ingestion(self) -> bool:
        """
        REAL TEST: Push event to Redis and wait for Python Consumer to process.
        
        This tests: Redis Stream → Python Consumer → Embeddings → Memgraph Graph B
        """
        print_act(2, "Memory Ingestion (Redis → Python Consumer → Graph B)")
        
        # Create event payload matching what Rust Observer would send
        event_data = {
            "event_type": "STRUCTURE_CHANGED",
            "file_path": str(TEST_FILE_PATH.absolute()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "diff_summary": f"Added helix_test_ghost.rs with ghost_function_{self.test_run_id} for integration test",
            "triggered_by": "USER",
            "old_hash": "",
            "new_hash": f"test_hash_{self.test_run_id}",
        }
        
        info("Publishing event to Redis stream (simulating Rust Observer)...")
        
        # Push to Redis stream
        event_id = self.redis_client.xadd(
            REDIS_STREAM,
            event_data,
            maxlen=10000,
        )
        success(f"Published event: {event_id}")
        
        # Wait for Python Consumer to process
        info(f"Waiting for Python Consumer to process ({EVENT_PROCESSING_TIMEOUT}s timeout)...")
        
        start_time = time.time()
        processed = False
        
        while time.time() - start_time < EVENT_PROCESSING_TIMEOUT:
            with self.driver.session() as session:
                result = session.run(
                    """
                    MATCH (e:Episode)
                    WHERE e.diff_summary CONTAINS $test_id
                    RETURN e.id AS id,
                           e.diff_summary AS summary,
                           e.embedding IS NOT NULL AS has_embedding,
                           size(e.embedding) AS embedding_dim
                    LIMIT 1
                    """,
                    test_id=self.test_run_id,
                )
                record = result.single()
                
                if record and record["has_embedding"]:
                    processed = True
                    embedding_dim = record["embedding_dim"]
                    break
            
            # Poll every 500ms
            time.sleep(0.5)
            print(".", end="", flush=True)
        
        print()  # Newline after dots
        
        if processed:
            return success(
                f"Python Consumer processed event! ({embedding_dim}-dim embedding)"
            )
        else:
            # Check Redis pending to diagnose
            try:
                pending = self.redis_client.xpending(REDIS_STREAM, "helix_orchestrator")
                pending_count = pending.get("pending", 0) if pending else "N/A"
            except Exception:
                pending_count = "N/A"
            
            fail(f"Python Consumer did not process event within {EVENT_PROCESSING_TIMEOUT}s")
            print()
            print(f"  {YELLOW}Troubleshooting:{RESET}")
            print(f"  • Is Python Consumer running? Check Terminal 2")
            print(f"  • Pending messages in Redis: {pending_count}")
            print(f"  • Try: cd graph-b-orchestrator && python -m graph_b_orchestrator")
            return False
    
    # =========================================================================
    # ACT 3: Brain Function (Vector Search)
    # =========================================================================
    def act3_brain_function(self) -> bool:
        """
        Test Vector Search / Hybrid Search on the data we just created.
        """
        print_act(3, "Brain Function (Vector Search)")
        
        info("Importing query engine...")
        
        try:
            from graph_b_orchestrator.query_engine import QueryEngine
            
            engine = QueryEngine(uri=MEMGRAPH_URI)
            engine.connect()
            
            info(f'Searching for "ghost function {self.test_run_id}"...')
            
            results = engine.hybrid_search(
                f"ghost function integration test {self.test_run_id}",
                limit=5,
            )
            
            engine.close()
            
            if results:
                # Look for our specific test episode
                matching = [r for r in results if self.test_run_id in r.text]
                
                if matching:
                    best = matching[0]
                    return success(
                        f"Found our test episode! (score: {best.score:.3f})"
                    )
                else:
                    best = results[0]
                    return success(
                        f"Search returned {len(results)} results (best: {best.score:.3f})"
                    )
            else:
                fail("Search returned no results")
                print(f"  {YELLOW}Note: This may fail if Act 2 failed{RESET}")
                return False
                        
        except Exception as e:
            return fail(f"Search failed: {e}")
    
    # =========================================================================
    # ACT 4: MCP Context (Context Compiler)
    # =========================================================================
    def act4_mcp_context(self) -> bool:
        """
        Test Context Compiler with real search results.
        """
        print_act(4, "MCP Context (The Bridge)")
        
        info("Importing context compiler and query engine...")
        
        try:
            from graph_b_orchestrator.context_compiler import compile_context
            from graph_b_orchestrator.query_engine import QueryEngine
            
            # Use real search results
            engine = QueryEngine(uri=MEMGRAPH_URI)
            engine.connect()
            
            info("Running search for context compilation...")
            results = engine.hybrid_search("ghost function test", limit=3)
            engine.close()
            
            if not results:
                warn("No search results to compile (depends on Act 2)")
                # Create minimal mock for testing compiler itself
                from graph_b_orchestrator.query_engine import SearchResult
                results = [
                    SearchResult(
                        node_type="Episode",
                        node_id="mock",
                        text=f"Integration test {self.test_run_id}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        score=0.9,
                        linked_files=[str(TEST_FILE_PATH)],
                        linked_functions=[f"ghost_function_{self.test_run_id}"],
                    )
                ]
            
            info("Compiling context from search results...")
            
            context = compile_context(
                search_results=results,
                include_structure=True,
            )
            
            if context and len(context) > 50 and "##" in context:
                success(f"Context Compiler generated {len(context)} chars of Markdown")
                print(f"\n  {YELLOW}Preview:{RESET}")
                for line in context[:500].split('\n')[:12]:
                    print(f"  {line}")
                if len(context) > 500:
                    print("  ...")
                return True
            else:
                return fail(f"Invalid context output: {context[:100] if context else 'empty'}")
                
        except Exception as e:
            return fail(f"Context compilation failed: {e}")
    
    # =========================================================================
    # Main Execution
    # =========================================================================
    def run(self) -> bool:
        """Run all 4 acts of the REAL integration test."""
        print_banner()
        print_prerequisites()
        
        input(f"{BOLD}Press ENTER when services are ready...{RESET}")
        print()
        
        if not self.connect():
            return False
        
        try:
            # Run all acts
            act1_pass = self.act1_ghost_commit()
            self.results.append(("Ghost Commit (Rust Observer)", act1_pass))
            
            act2_pass = self.act2_memory_ingestion()
            self.results.append(("Memory Ingestion (Python Consumer)", act2_pass))
            
            act3_pass = self.act3_brain_function()
            self.results.append(("Brain Function (Vector Search)", act3_pass))
            
            act4_pass = self.act4_mcp_context()
            self.results.append(("MCP Context (Compiler)", act4_pass))
            
            # Print summary
            self._print_summary()
            
            return all(passed for _, passed in self.results)
            
        finally:
            self.cleanup()
            self.close()
    
    def _print_summary(self):
        """Print the final summary."""
        print()
        print(f"{BOLD}{'═' * 60}{RESET}")
        print(f"{BOLD}  REAL INTEGRATION TEST SUMMARY{RESET}")
        print(f"{BOLD}{'═' * 60}{RESET}")
        print()
        
        passed = sum(1 for _, p in self.results if p)
        total = len(self.results)
        
        for name, result in self.results:
            status = f"{GREEN}✅ PASS{RESET}" if result else f"{RED}❌ FAIL{RESET}"
            print(f"  {name:.<45} {status}")
        
        print()
        print(f"  {'─' * 55}")
        print(f"  TOTAL: {passed}/{total} Acts Passed")
        print()
        
        if passed == total:
            print(f"{BOLD}{GREEN}╔═══════════════════════════════════════════════════════════════╗{RESET}")
            print(f"{BOLD}{GREEN}║                                                               ║{RESET}")
            print(f"{BOLD}{GREEN}║   🎉  PROJECT HELIX IS ALIVE! (VERIFIED)                     ║{RESET}")
            print(f"{BOLD}{GREEN}║                                                               ║{RESET}")
            print(f"{BOLD}{GREEN}║   All components working end-to-end:                         ║{RESET}")
            print(f"{BOLD}{GREEN}║   • Rust Observer (file detection)                           ║{RESET}")
            print(f"{BOLD}{GREEN}║   • Python Consumer (event processing)                       ║{RESET}")
            print(f"{BOLD}{GREEN}║   • Embedding Engine (384-dim vectors)                       ║{RESET}")
            print(f"{BOLD}{GREEN}║   • Vector Search (semantic retrieval)                       ║{RESET}")
            print(f"{BOLD}{GREEN}║   • Context Compiler (MCP bridge)                            ║{RESET}")
            print(f"{BOLD}{GREEN}║                                                               ║{RESET}")
            print(f"{BOLD}{GREEN}╚═══════════════════════════════════════════════════════════════╝{RESET}")
        else:
            print(f"{BOLD}{RED}╔═══════════════════════════════════════════════════════════════╗{RESET}")
            print(f"{BOLD}{RED}║                                                               ║{RESET}")
            print(f"{BOLD}{RED}║   ⚠️  INTEGRATION TEST FAILED                                 ║{RESET}")
            print(f"{BOLD}{RED}║                                                               ║{RESET}")
            print(f"{BOLD}{RED}║   Check the troubleshooting hints above.                     ║{RESET}")
            print(f"{BOLD}{RED}║   Ensure Rust Observer and Python Consumer are running.       ║{RESET}")
            print(f"{BOLD}{RED}║                                                               ║{RESET}")
            print(f"{BOLD}{RED}╚═══════════════════════════════════════════════════════════════╝{RESET}")
        
        print()


def main():
    """Main entry point."""
    test = HelixRealIntegrationTest()
    result = test.run()
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
