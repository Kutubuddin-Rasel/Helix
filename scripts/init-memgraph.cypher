// Project Helix - Memgraph Initialization Script
// Version: 1.1
// Last Updated: 2026-01-04
//
// This script initializes the graph schema with:
//   - Node constraints (uniqueness)
//   - Indexes (performance-critical for CEW-001)
//   - Edge type definitions
//
// Run on first startup or schema migration.

// ==============================================================================
// GRAPH A: CODE STRUCTURE (The Map)
// ==============================================================================

// --- File Nodes ---
CREATE CONSTRAINT ON (f:File) ASSERT f.id IS UNIQUE;
CREATE CONSTRAINT ON (f:File) ASSERT f.path IS UNIQUE;  // Pillar 3.3: Unique file paths
CREATE INDEX ON :File(path);
CREATE INDEX ON :File(hash);

// --- Function Nodes ---
CREATE CONSTRAINT ON (fn:Function) ASSERT fn.id IS UNIQUE;
CREATE INDEX ON :Function(name);
CREATE INDEX ON :Function(signature);

// --- Class/Struct Nodes ---
CREATE CONSTRAINT ON (c:Class) ASSERT c.id IS UNIQUE;
CREATE INDEX ON :Class(name);

// --- Module Nodes ---
CREATE CONSTRAINT ON (m:Module) ASSERT m.id IS UNIQUE;
CREATE INDEX ON :Module(name);

// --- Import Nodes ---
CREATE CONSTRAINT ON (i:Import) ASSERT i.id IS UNIQUE;
CREATE INDEX ON :Import(source);

// ==============================================================================
// GRAPH B: EPISODIC HISTORY (The Story)
// ==============================================================================

// --- Episode Nodes (AI Actions) ---
CREATE CONSTRAINT ON (e:Episode) ASSERT e.id IS UNIQUE;
CREATE INDEX ON :Episode(timestamp);
CREATE INDEX ON :Episode(actor);
CREATE INDEX ON :Episode(action);

// --- Ghost Commit Nodes (User Manual Edits) ---
CREATE CONSTRAINT ON (g:GhostCommit) ASSERT g.id IS UNIQUE;
CREATE INDEX ON :GhostCommit(timestamp);
CREATE INDEX ON :GhostCommit(triggered_by);

// --- Summary Nodes (Squashed History) ---
CREATE CONSTRAINT ON (s:Summary) ASSERT s.id IS UNIQUE;
CREATE INDEX ON :Summary(timestamp);

// --- Vector Indexes for Semantic Search (Pillar 3.2) ---
// Note: Memgraph MAGE provides vector similarity via mage.vector_search
// Episode embeddings (384-dim from fastembed)
CREATE INDEX ON :Episode(embedding);
// Summary embeddings (combined from squashed episodes)  
CREATE INDEX ON :Summary(embedding);

// --- Session Nodes (Conversation Boundaries) ---
CREATE CONSTRAINT ON (sess:Session) ASSERT sess.id IS UNIQUE;
CREATE INDEX ON :Session(started_at);

// ==============================================================================
// CROSS-GRAPH RELATIONSHIPS (CEW-001: Hard Relationships)
// ==============================================================================
// These edge types connect Graph B nodes to Graph A nodes.
// Queries MUST traverse edges, not string-match properties.
//
// Edge Types:
//   (Episode)-[:AFFECTS]->(Function|Class|File)
//   (Episode)-[:IN_FILE]->(File)
//   (GhostCommit)-[:AFFECTS]->(Function|Class|File)
//   (GhostCommit)-[:IN_FILE]->(File)
//   (Summary)-[:AFFECTS]->(File)
//   (Summary)-[:SUMMARIZES]->(Episode|GhostCommit)
//
// Note: Edge indexes are created on-demand by Memgraph.

// ==============================================================================
// UTILITY: Clear all data (USE WITH CAUTION)
// ==============================================================================
// MATCH (n) DETACH DELETE n;

// ==============================================================================
// VERIFICATION QUERIES
// ==============================================================================
// Run these to verify schema is correctly applied:
//
// Show all constraints:
//   SHOW CONSTRAINT INFO;
//
// Show all indexes:
//   SHOW INDEX INFO;
//
// Count nodes by label:
//   MATCH (n) RETURN labels(n) AS label, count(*) AS count;
