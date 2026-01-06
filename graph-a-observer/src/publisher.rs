//! Event Publisher Module - Project Helix Graph A Observer
//!
//! Publishes change events to Redis Streams for the Python Orchestrator.
//! Implements CEW-002 compliant MAXLEN policy to prevent stream bloat.
//!
//! **Write Authority:** PUBLISH only to Redis stream `helix:events`.
//! The Python Orchestrator consumes these events and writes to Graph B.

use anyhow::Result;
use redis::aio::MultiplexedConnection;
use redis::AsyncCommands;
use serde::{Deserialize, Serialize};
use std::time::{SystemTime, UNIX_EPOCH};
use thiserror::Error;
use tracing::{debug, info, instrument};

use crate::db::FunctionInfo;

/// Redis stream name for Helix events
const STREAM_NAME: &str = "helix:events";

/// Maximum stream length (approximate trimming per CEW-002)
const STREAM_MAXLEN: usize = 10000;

/// Errors that can occur during event publishing
#[derive(Error, Debug)]
pub enum PublisherError {
    #[error("Failed to connect to Redis: {0}")]
    ConnectionError(String),

    #[error("Failed to publish event: {0}")]
    PublishError(String),

    #[error("Serialization error: {0}")]
    SerializationError(String),
}

/// Configuration for the Redis publisher
#[derive(Debug, Clone)]
pub struct PublisherConfig {
    /// Redis URL (redis://host:port)
    pub redis_url: String,
    /// Stream name
    pub stream_name: String,
    /// Maximum stream length (MAXLEN ~)
    pub max_len: usize,
}

impl Default for PublisherConfig {
    fn default() -> Self {
        Self {
            redis_url: "redis://localhost:6889".to_string(),
            stream_name: STREAM_NAME.to_string(),
            max_len: STREAM_MAXLEN,
        }
    }
}

/// Event types that can be published
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum EventType {
    /// File structure changed (AST modified)
    StructureChanged,
    /// File was created
    FileCreated,
    /// File was deleted
    FileDeleted,
    /// Manual user edit detected (Ghost Commit)
    GhostCommit,
    /// Connectivity test event
    ConnectivityTest,
}

impl std::fmt::Display for EventType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EventType::StructureChanged => write!(f, "STRUCTURE_CHANGED"),
            EventType::FileCreated => write!(f, "FILE_CREATED"),
            EventType::FileDeleted => write!(f, "FILE_DELETED"),
            EventType::GhostCommit => write!(f, "GHOST_COMMIT"),
            EventType::ConnectivityTest => write!(f, "CONNECTIVITY_TEST"),
        }
    }
}

/// Event payload published to Redis stream
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChangeEvent {
    /// Type of event
    pub event_type: String,
    /// Path to the affected file
    pub file_path: String,
    /// ISO 8601 timestamp
    pub timestamp: String,
    /// Summary of the change
    pub diff_summary: String,
    /// Previous structural hash (if applicable)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub old_hash: Option<String>,
    /// New structural hash (if applicable)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub new_hash: Option<String>,
    /// Source of the change (USER, AI, or UNKNOWN)
    #[serde(default = "default_triggered_by")]
    pub triggered_by: String,
}

fn default_triggered_by() -> String {
    "UNKNOWN".to_string()
}

impl ChangeEvent {
    /// Create a new structure changed event with semantic content
    pub fn structure_changed(
        file_path: &str,
        old_hash: Option<&str>,
        new_hash: &str,
        functions: &[FunctionInfo],
        doc_comments: &[String],
        semantic_keywords: &str,
    ) -> Self {
        // Build semantic summary with function names, doc, and keywords
        let filename = file_path.split('/').last().unwrap_or(file_path);
        
        let func_names: Vec<&str> = functions.iter().map(|f| f.name.as_str()).collect();
        let func_part = if func_names.is_empty() {
            String::new()
        } else {
            format!(" Functions: {}.", func_names.join(", "))
        };
        
        let doc_part = doc_comments.first().map(|d| {
            let preview = if d.len() > 100 { &d[..100] } else { d };
            format!(" Doc: {}", preview)
        }).unwrap_or_default();

        // Include semantic keywords for embedding search
        let keywords_part = if semantic_keywords.is_empty() {
            String::new()
        } else {
            format!(" Keywords: {}", semantic_keywords)
        };
        
        let summary = match old_hash {
            Some(_) => format!("File {} changed.{}{}{}", filename, func_part, doc_part, keywords_part),
            None => format!("New file {}.{}{}{}", filename, func_part, doc_part, keywords_part),
        };

        Self {
            event_type: EventType::StructureChanged.to_string(),
            file_path: file_path.to_string(),
            timestamp: iso_timestamp(),
            diff_summary: summary,
            old_hash: old_hash.map(|s| s.to_string()),
            new_hash: Some(new_hash.to_string()),
            triggered_by: "USER".to_string(),
        }
    }

    /// Create a file deleted event
    pub fn file_deleted(file_path: &str, old_hash: Option<&str>) -> Self {
        Self {
            event_type: EventType::FileDeleted.to_string(),
            file_path: file_path.to_string(),
            timestamp: iso_timestamp(),
            diff_summary: "File deleted".to_string(),
            old_hash: old_hash.map(|s| s.to_string()),
            new_hash: None,
            triggered_by: "USER".to_string(),
        }
    }

    /// Create a file created event
    pub fn file_created(file_path: &str, hash: &str) -> Self {
        Self {
            event_type: EventType::FileCreated.to_string(),
            file_path: file_path.to_string(),
            timestamp: iso_timestamp(),
            diff_summary: format!("New file created with hash {}", &hash[..8.min(hash.len())]),
            old_hash: None,
            new_hash: Some(hash.to_string()),
            triggered_by: "USER".to_string(),
        }
    }
}

/// Redis event publisher
pub struct EventPublisher {
    /// Redis connection
    conn: MultiplexedConnection,
    /// Configuration
    config: PublisherConfig,
}

impl EventPublisher {
    /// Create a new publisher and establish connection
    #[instrument(skip_all, fields(url = %config.redis_url))]
    pub async fn connect(config: PublisherConfig) -> Result<Self, PublisherError> {
        info!("Connecting to Redis at {}", config.redis_url);

        let client = redis::Client::open(config.redis_url.as_str())
            .map_err(|e| PublisherError::ConnectionError(e.to_string()))?;

        let conn = client
            .get_multiplexed_async_connection()
            .await
            .map_err(|e| PublisherError::ConnectionError(e.to_string()))?;

        info!("Connected to Redis successfully");

        Ok(Self { conn, config })
    }

    /// Connect with default configuration
    pub async fn connect_default() -> Result<Self, PublisherError> {
        Self::connect(PublisherConfig::default()).await
    }

    /// Verify the connection is still alive
    pub async fn health_check(&mut self) -> Result<bool, PublisherError> {
        let pong: String = redis::cmd("PING")
            .query_async(&mut self.conn)
            .await
            .map_err(|e| PublisherError::ConnectionError(e.to_string()))?;

        Ok(pong == "PONG")
    }

    /// Publish a change event to the Redis stream
    #[instrument(skip(self, event), fields(event_type = %event.event_type, file = %event.file_path))]
    pub async fn publish_change(&mut self, event: &ChangeEvent) -> Result<String, PublisherError> {
        debug!("Publishing event: {:?}", event);

        // Build the stream entries (individual fields only - avoids double serialization)
        // Old/new hash are optional, so we handle them separately
        let old_hash_str = event.old_hash.as_deref().unwrap_or("");
        let new_hash_str = event.new_hash.as_deref().unwrap_or("");
        
        let entries: &[(&str, &str)] = &[
            ("event_type", &event.event_type),
            ("file_path", &event.file_path),
            ("timestamp", &event.timestamp),
            ("diff_summary", &event.diff_summary),
            ("triggered_by", &event.triggered_by),
            ("old_hash", old_hash_str),
            ("new_hash", new_hash_str),
        ];

        // Use XADD with MAXLEN ~ (approximate trimming) per CEW-002
        let event_id: String = redis::cmd("XADD")
            .arg(&self.config.stream_name)
            .arg("MAXLEN")
            .arg("~")
            .arg(self.config.max_len)
            .arg("*") // Auto-generate ID
            .arg(entries)
            .query_async(&mut self.conn)
            .await
            .map_err(|e| PublisherError::PublishError(e.to_string()))?;

        info!("Published event {} to stream", event_id);

        Ok(event_id)
    }

    /// Get the current stream length
    pub async fn get_stream_length(&mut self) -> Result<i64, PublisherError> {
        let len: i64 = self
            .conn
            .xlen(&self.config.stream_name)
            .await
            .map_err(|e| PublisherError::PublishError(e.to_string()))?;

        Ok(len)
    }
}

/// Generate an ISO 8601 timestamp
fn iso_timestamp() -> String {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();

    let secs = duration.as_secs();
    // Convert to ISO 8601 format (simplified)
    chrono_lite(secs)
}

/// Simple timestamp formatting without full chrono dependency
fn chrono_lite(unix_secs: u64) -> String {
    // Simple conversion - for production, use chrono crate
    let days_since_epoch = unix_secs / 86400;
    let secs_today = unix_secs % 86400;
    let hours = secs_today / 3600;
    let minutes = (secs_today % 3600) / 60;
    let seconds = secs_today % 60;

    // Calculate date (simplified - doesn't account for leap years perfectly)
    let years = 1970 + (days_since_epoch / 365);
    let day_of_year = days_since_epoch % 365;
    let month = (day_of_year / 30).min(11) + 1;
    let day = (day_of_year % 30) + 1;

    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        years, month, day, hours, minutes, seconds
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_event_serialization() {
        let event = ChangeEvent::structure_changed(
            "/src/main.rs",
            Some("abc12345"),
            "def67890",
        );

        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains("STRUCTURE_CHANGED"));
        assert!(json.contains("/src/main.rs"));
    }

    #[test]
    fn test_iso_timestamp() {
        let ts = iso_timestamp();
        // Should be in ISO 8601 format
        assert!(ts.contains("T"));
        assert!(ts.ends_with("Z"));
    }
}
