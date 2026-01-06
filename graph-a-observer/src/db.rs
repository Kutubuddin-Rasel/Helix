//! Database Client Module - Project Helix Graph A Observer
//!
//! Provides Memgraph (Graph A) connectivity using the neo4rs Bolt driver.
//! Implements upsert operations for File and Function nodes.
//!
//! **Write Authority:** This module has WRITE access to Graph A only.
//! It must NEVER write to Graph B (that's Python Orchestrator's job).\

use neo4rs::{query, Graph};
use std::sync::Arc;
use thiserror::Error;
use tracing::{debug, info, instrument};

/// Errors that can occur during database operations
#[derive(Error, Debug)]
pub enum DbError {
    #[error("Failed to connect to Memgraph: {0}")]
    ConnectionError(String),

    #[error("Query execution failed: {0}")]
    QueryError(String),
}

/// Configuration for the database connection
#[derive(Debug, Clone)]
pub struct DbConfig {
    /// Memgraph URI (bolt://host:port)
    pub uri: String,
    /// Database user (optional for Memgraph)
    pub user: String,
    /// Database password (optional for Memgraph)
    pub password: String,
}

impl Default for DbConfig {
    fn default() -> Self {
        Self {
            uri: "bolt://localhost:7687".to_string(),
            user: String::new(),
            password: String::new(),
        }
    }
}

/// A function definition extracted from the AST
#[derive(Debug, Clone)]
pub struct FunctionInfo {
    /// Function name
    pub name: String,
    /// Start line number
    pub start_line: u32,
    /// End line number
    pub end_line: u32,
    /// Function signature (if available)
    pub signature: Option<String>,
}

/// Result of upserting a file structure
#[derive(Debug)]
pub struct UpsertResult {
    /// Whether the file node was created (true) or updated (false)
    pub file_created: bool,
    /// Number of function nodes created/updated
    pub functions_updated: usize,
}

/// Memgraph database client for Graph A operations
pub struct GraphAClient {
    /// The neo4rs graph connection
    graph: Arc<Graph>,
}

impl GraphAClient {
    /// Create a new database client and establish connection
    #[instrument(skip_all, fields(uri = %config.uri))]
    pub async fn connect(config: DbConfig) -> Result<Self, DbError> {
        info!("Connecting to Memgraph at {}", config.uri);

        // Use ConfigBuilder to disable default database selection (Memgraph doesn't support it)
        let graph_config = neo4rs::ConfigBuilder::default()
            .uri(&config.uri)
            .user(&config.user)
            .password(&config.password)
            .db("memgraph") // Memgraph uses "memgraph" as default
            .build()
            .map_err(|e| DbError::ConnectionError(e.to_string()))?;

        let graph = Graph::connect(graph_config)
            .await
            .map_err(|e| DbError::ConnectionError(e.to_string()))?;

        info!("Connected to Memgraph successfully");

        Ok(Self {
            graph: Arc::new(graph),
        })
    }

    /// Upsert a file node and its function children
    ///
    /// This creates or updates:
    /// 1. A File node with the given properties
    /// 2. Function nodes for each function in the file
    /// 3. [:DEFINES] relationships from File to Functions
    #[instrument(skip(self, functions), fields(path = %file_path))]
    pub async fn upsert_file_structure(
        &self,
        file_path: &str,
        language: &str,
        structural_hash: &str,
        functions: Vec<FunctionInfo>,
    ) -> Result<UpsertResult, DbError> {
        debug!(
            "Upserting file structure: {} ({} functions)",
            file_path,
            functions.len()
        );

        // Generate a unique file ID based on path (cache for reuse)
        let file_path_hash = sha256_short(file_path);
        let file_id = format!("file_{}", file_path_hash);

        // 1. Upsert the File node using run() instead of transaction
        let file_query = query(
            r#"
            MERGE (f:File {id: $file_id})
            ON CREATE SET 
                f.path = $path,
                f.language = $language,
                f.hash = $hash,
                f.created_at = timestamp(),
                f.updated_at = timestamp()
            ON MATCH SET 
                f.hash = $hash,
                f.updated_at = timestamp()
            "#,
        )
        .param("file_id", file_id.clone())
        .param("path", file_path)
        .param("language", language)
        .param("hash", structural_hash);

        self.graph
            .run(file_query)
            .await
            .map_err(|e| DbError::QueryError(format!("File upsert failed: {}", e)))?;

        // 2. Delete old function nodes for this file (to handle removals)
        let delete_query = query(
            r#"
            MATCH (f:File {id: $file_id})-[:DEFINES]->(fn:Function)
            DETACH DELETE fn
            "#,
        )
        .param("file_id", file_id.clone());

        self.graph
            .run(delete_query)
            .await
            .map_err(|e| DbError::QueryError(format!("Function cleanup failed: {}", e)))?;

        // 3. Create new function nodes (using cached file_path_hash)
        let mut functions_updated = 0;
        for func in &functions {
            let func_id = format!(
                "fn_{}_{}", 
                file_path_hash,  // Reuse cached hash
                sha256_short(&format!("{}_{}", func.name, func.start_line)),
            );

            let func_query = query(
                r#"
                MATCH (f:File {id: $file_id})
                MERGE (fn:Function {id: $func_id})
                ON CREATE SET
                    fn.name = $name,
                    fn.start_line = $start_line,
                    fn.end_line = $end_line,
                    fn.signature = $signature
                ON MATCH SET
                    fn.name = $name,
                    fn.start_line = $start_line,
                    fn.end_line = $end_line,
                    fn.signature = $signature
                MERGE (f)-[:DEFINES]->(fn)
                "#,
            )
            .param("file_id", file_id.clone())
            .param("func_id", func_id)
            .param("name", func.name.clone())
            .param("start_line", func.start_line as i64)
            .param("end_line", func.end_line as i64)
            .param("signature", func.signature.clone().unwrap_or_default());

            self.graph
                .run(func_query)
                .await
                .map_err(|e| DbError::QueryError(format!("Function upsert failed: {}", e)))?;

            functions_updated += 1;
        }

        info!(
            "Upserted file {} with {} functions",
            file_path, functions_updated
        );

        Ok(UpsertResult {
            file_created: true,
            functions_updated,
        })
    }

    /// Delete a file node and all its children
    #[instrument(skip(self), fields(path = %file_path))]
    pub async fn delete_file(&self, file_path: &str) -> Result<(), DbError> {
        let file_id = format!("file_{}", sha256_short(file_path));

        let delete_query = query(
            r#"
            MATCH (f:File {id: $file_id})
            OPTIONAL MATCH (f)-[:DEFINES]->(fn:Function)
            DETACH DELETE f, fn
            "#,
        )
        .param("file_id", file_id);

        self.graph
            .run(delete_query)
            .await
            .map_err(|e| DbError::QueryError(format!("Delete failed: {}", e)))?;

        info!("Deleted file node: {}", file_path);
        Ok(())
    }
}

/// Generate a short SHA-256 hash (first 12 chars) for IDs
fn sha256_short(input: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(input.as_bytes());
    let result = hasher.finalize();
    format!("{:x}", result)[..12].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sha256_short() {
        let hash = sha256_short("/src/main.rs");
        assert_eq!(hash.len(), 12);
        assert_eq!(hash, sha256_short("/src/main.rs"));
        assert_ne!(hash, sha256_short("/src/lib.rs"));
    }
}
