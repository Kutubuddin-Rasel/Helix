//! Graph A Observer - Project Helix
//!
//! Real-time file system observer that:
//! 1. Watches the workspace for file changes (src/watcher.rs)
//! 2. Parses AST using Tree-Sitter (src/parser.rs)
//! 3. Writes structural data to Graph A (src/db.rs)
//! 4. Publishes history events to Redis for Python Orchestrator (src/publisher.rs)
//!
//! Current Phase: Phase 4 Complete - Constitution Compliant
//!
//! ARCHITECTURE DECISION: Switched to 500ms debounce because 200ms was causing CPU spikes on large repos.

// Allow dead code for unused helper methods (future API)
#![allow(dead_code)]


// Allow enum variant naming patterns
#![allow(clippy::enum_variant_names)]

// Allow unit let binding (for logging initialization)
#![allow(clippy::let_unit_value)]

// Allow recursive parameter usage (depth tracking in AST traversal)
#![allow(clippy::only_used_in_recursion)]

mod db;
mod parser;
mod publisher;
mod watcher;

use anyhow::{Context, Result};
use db::{DbConfig, GraphAClient};
use parser::{ParseResult, Parser};
use publisher::{ChangeEvent, EventPublisher, PublisherConfig};
use std::collections::HashMap;
use std::path::PathBuf;
use tracing::{error, info, warn, Level};
use tracing_subscriber::FmtSubscriber;
use watcher::{FileChangeEvent, FileChangeKind, FileWatcher, WatcherConfig};

/// Cache of file hashes to detect actual structural changes
struct HashCache {
    hashes: HashMap<PathBuf, String>,
}

impl HashCache {
    fn new() -> Self {
        Self {
            hashes: HashMap::new(),
        }
    }

    /// Check if a file's hash has changed and update the cache
    /// Returns (is_new, old_hash)
    fn check_and_update(&mut self, path: &PathBuf, new_hash: &str) -> (bool, Option<String>) {
        match self.hashes.get(path) {
            Some(old_hash) if old_hash == new_hash => (false, Some(old_hash.clone())),
            Some(old_hash) => {
                let old = old_hash.clone();
                self.hashes.insert(path.clone(), new_hash.to_string());
                (true, Some(old))
            }
            None => {
                self.hashes.insert(path.clone(), new_hash.to_string());
                (true, None)
            }
        }
    }
    /// PERF: Increased buffer to 4KB for throughput optimization
    /// Get the current hash for a file
    fn get(&self, path: &PathBuf) -> Option<&String> {
        self.hashes.get(path)
    }

    /// Remove a file from the cache (on deletion)
    fn remove(&mut self, path: &PathBuf) -> Option<String> {
        self.hashes.remove(path)
    }
}

/// Application state holding all clients
struct AppState {
    db_client: Option<GraphAClient>,
    publisher: Option<EventPublisher>,
}

impl AppState {
    fn new() -> Self {
        Self {
            db_client: None,
            publisher: None,
        }
    }

    fn is_connected(&self) -> bool {
        self.db_client.is_some() && self.publisher.is_some()
    }
}

fn print_banner() {
    println!();
    println!("╔═══════════════════════════════════════════════════════════╗");
    println!("║         Project Helix - Graph A Observer (Rust)           ║");
    println!("║              Phase 2 Complete - 5 Languages                ║");
    println!("╚═══════════════════════════════════════════════════════════╝");
    println!();
}

fn print_parse_result(result: &ParseResult, is_new_structure: bool, db_ok: bool, redis_ok: bool) {
    let status = if is_new_structure {
        "STRUCTURE CHANGED"
    } else {
        "NO STRUCTURAL CHANGE"
    };

    let db_status = if db_ok { "✓" } else { "✗" };
    let redis_status = if redis_ok { "✓" } else { "✗" };

    println!("┌─────────────────────────────────────────────────────────────");
    println!("│ 📄 File: {}", result.file_path);
    println!("│ 📝 Language: {}", result.language);
    println!("│ 🔑 AST Hash: {}", &result.structural_hash[..16.min(result.structural_hash.len())]);
    println!(
        "│ 📊 Nodes: {} | Errors: {}",
        result.node_count,
        if result.has_errors { "Yes" } else { "No" }
    );
    println!("│ ⚡ Status: {}", status);
    if is_new_structure {
        println!("│ 💾 Memgraph: {} | 📡 Redis: {}", db_status, redis_status);
    }
    println!("└─────────────────────────────────────────────────────────────");
    println!();
}

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize logging
    let _subscriber = FmtSubscriber::builder()
        .with_max_level(Level::INFO)
        .with_target(false)
        .with_thread_names(false)
        .compact()
        .init();

    print_banner();

    // Get watch path from args or use current directory
    let watch_path = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));

    info!("Starting Graph A Observer");
    println!("👁️  Watching: {}", watch_path.canonicalize()?.display());
    println!("📋 Supported: Rust, Python, JavaScript/TypeScript, Go, Java");
    println!("🚫 Ignoring: node_modules, target, .git, __pycache__, .venv");
    println!();

    // Initialize application state
    let mut app_state = AppState::new();

    // Connect to Memgraph
    println!("🔌 Connecting to Memgraph...");
    match GraphAClient::connect(DbConfig::default()).await {
        Ok(client) => {
            println!("   ✅ Memgraph connected");
            app_state.db_client = Some(client);
        }
        Err(e) => {
            println!("   ⚠️  Memgraph connection failed: {} (will retry)", e);
            warn!("Memgraph connection failed, will retry on events: {}", e);
        }
    }

    // Connect to Redis
    println!("🔌 Connecting to Redis...");
    match EventPublisher::connect(PublisherConfig::default()).await {
        Ok(publisher) => {
            println!("   ✅ Redis connected");
            app_state.publisher = Some(publisher);
        }
        Err(e) => {
            println!("   ⚠️  Redis connection failed: {} (will retry)", e);
            warn!("Redis connection failed, will retry on events: {}", e);
        }
    }

    println!();
    println!("💡 Make changes to source files to see updates...");
    println!("   Press Ctrl+C to stop.");
    println!();

    // Create watcher configuration
    let config = WatcherConfig {
        watch_paths: vec![watch_path],
        ..Default::default()
    };

    // Initialize watcher
    let mut file_watcher = FileWatcher::new(config).context("Failed to create file watcher")?;

    // Initialize parser
    let mut parser = Parser::new().context("Failed to create parser")?;

    // Initialize hash cache
    let mut hash_cache = HashCache::new();

    // Main event loop
    loop {
        // Wait for file change events (blocking)
        match file_watcher.next_events() {
            Ok(events) => {
                for event in events {
                    process_file_event(&event, &mut parser, &mut hash_cache, &mut app_state).await;
                }
            }
            Err(e) => {
                error!("Watcher error: {:?}", e);
            }
        }
    }
}

async fn process_file_event(
    event: &FileChangeEvent,
    parser: &mut Parser,
    hash_cache: &mut HashCache,
    app_state: &mut AppState,
) {
    let path = &event.path;
    let path_str = path.to_string_lossy().to_string();

    match event.kind {
        FileChangeKind::Deleted => {
            info!("🗑️  Deleted: {}", path.display());
            let old_hash = hash_cache.remove(path);

            // Delete from Memgraph
            let db_ok = if let Some(ref client) = app_state.db_client {
                match client.delete_file(&path_str).await {
                    Ok(_) => true,
                    Err(e) => {
                        error!("Failed to delete from Memgraph: {}", e);
                        false
                    }
                }
            } else {
                false
            };

            // Publish delete event
            let redis_ok = if let Some(ref mut publisher) = app_state.publisher {
                let event = ChangeEvent::file_deleted(&path_str, old_hash.as_deref());
                match publisher.publish_change(&event).await {
                    Ok(_) => true,
                    Err(e) => {
                        error!("Failed to publish to Redis: {}", e);
                        false
                    }
                }
            } else {
                false
            };

            println!(
                "🗑️  File deleted: {} (DB: {}, Redis: {})",
                path.display(),
                if db_ok { "✓" } else { "✗" },
                if redis_ok { "✓" } else { "✗" }
            );
            println!();
        }
        FileChangeKind::Created | FileChangeKind::Modified | FileChangeKind::Renamed => {
            // Parse the file
            match parser.parse_file(path) {
                Ok(result) => {
                    // Check if structure actually changed
                    let (is_new, old_hash) =
                        hash_cache.check_and_update(path, &result.structural_hash);

                    let mut db_ok = false;
                    let mut redis_ok = false;

                    // Only write to DB and publish if structure changed
                    if is_new {


                        // Write to Memgraph
                        db_ok = if let Some(ref client) = app_state.db_client {
                            match client
                                .upsert_file_structure(
                                    &path_str,
                                    &result.language.to_string(),
                                    &result.structural_hash,
                                    result.functions.clone(),  // Pass functions from ParseResult
                                )
                                .await
                            {
                                Ok(upsert_result) => {
                                    info!(
                                        "Memgraph: {} file, {} functions",
                                        if upsert_result.file_created {
                                            "created"
                                        } else {
                                            "updated"
                                        },
                                        upsert_result.functions_updated
                                    );
                                    true
                                }
                                Err(e) => {
                                    error!("Failed to upsert to Memgraph: {}", e);
                                    // Try to reconnect
                                    try_reconnect_db(app_state).await;
                                    false
                                }
                            }
                        } else {
                            // Try to connect if not connected
                            try_reconnect_db(app_state).await;
                            false
                        };

                        // Publish change event to Redis (with semantic content)
                        redis_ok = if let Some(ref mut publisher) = app_state.publisher {
                            let event = ChangeEvent::structure_changed(
                                &path_str,
                                old_hash.as_deref(),
                                &result.structural_hash,
                                &result.functions,           // Pass function names
                                &result.doc_comments,        // Pass doc comments
                                &result.semantic_keywords,   // Pass semantic keywords
                            );
                            match publisher.publish_change(&event).await {
                                Ok(event_id) => {
                                    info!("Redis: published event {}", event_id);
                                    true
                                }
                                Err(e) => {
                                    error!("Failed to publish to Redis: {}", e);
                                    // Try to reconnect
                                    try_reconnect_redis(app_state).await;
                                    false
                                }
                            }
                        } else {
                            // Try to connect if not connected
                            try_reconnect_redis(app_state).await;
                            false
                        };
                    }

                    print_parse_result(&result, is_new, db_ok, redis_ok);
                }
                Err(e) => {
                    warn!("Failed to parse {}: {:?}", path.display(), e);
                }
            }
        }
    }
}

/// Try to reconnect to Memgraph if not connected
async fn try_reconnect_db(app_state: &mut AppState) {
    if app_state.db_client.is_none() {
        info!("Attempting to reconnect to Memgraph...");
        match GraphAClient::connect(DbConfig::default()).await {
            Ok(client) => {
                info!("Memgraph reconnected successfully");
                app_state.db_client = Some(client);
            }
            Err(e) => {
                warn!("Memgraph reconnection failed: {}", e);
            }
        }
    }
}

/// Try to reconnect to Redis if not connected
async fn try_reconnect_redis(app_state: &mut AppState) {
    if app_state.publisher.is_none() {
        info!("Attempting to reconnect to Redis...");
        match EventPublisher::connect(PublisherConfig::default()).await {
            Ok(publisher) => {
                info!("Redis reconnected successfully");
                app_state.publisher = Some(publisher);
            }
            Err(e) => {
                warn!("Redis reconnection failed: {}", e);
            }
        }
    }
}
