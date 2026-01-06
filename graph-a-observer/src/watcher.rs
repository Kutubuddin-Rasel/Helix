//! File Watcher Module - Project Helix Graph A Observer
//!
//! Provides debounced file system watching using the `notify` crate.
//! Filters events to only track supported source files (Rust, Python).

use notify::{RecommendedWatcher, RecursiveMode};
use notify_debouncer_mini::{new_debouncer, DebouncedEvent, Debouncer};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;
use thiserror::Error;
use tracing::{debug, info, instrument, warn};

/// Default debounce duration in milliseconds
const DEFAULT_DEBOUNCE_MS: u64 = 500;

/// Errors that can occur during file watching
#[derive(Error, Debug)]
pub enum WatcherError {
    #[error("Failed to initialize watcher: {0}")]
    InitError(String),

    #[error("Failed to watch path: {0}")]
    WatchPathError(String),

    #[error("Watcher event error: {0}")]
    EventError(String),
}

/// Patterns to ignore when watching files
const IGNORE_PATTERNS: &[&str] = &[
    "node_modules",
    "target",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".next",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "Thumbs.db",
];

/// File extensions to watch (Blueprint: 5 languages)
const WATCH_EXTENSIONS: &[&str] = &[
    // Rust
    "rs",
    // Python
    "py", "pyi", "pyw",
    // JavaScript/TypeScript
    "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts",
    // Go
    "go",
    // Java
    "java",
];

/// A file change event with parsed information
#[derive(Debug, Clone)]
pub struct FileChangeEvent {
    /// Path to the changed file
    pub path: PathBuf,

    /// Type of change
    pub kind: FileChangeKind,
}

/// Type of file change
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileChangeKind {
    /// File was created
    Created,
    /// File was modified
    Modified,
    /// File was deleted
    Deleted,
    /// File was renamed (we treat as delete + create)
    Renamed,
}

impl std::fmt::Display for FileChangeKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FileChangeKind::Created => write!(f, "Created"),
            FileChangeKind::Modified => write!(f, "Modified"),
            FileChangeKind::Deleted => write!(f, "Deleted"),
            FileChangeKind::Renamed => write!(f, "Renamed"),
        }
    }
}

/// Configuration for the file watcher
#[derive(Debug, Clone)]
pub struct WatcherConfig {
    /// Paths to watch
    pub watch_paths: Vec<PathBuf>,

    /// Debounce duration
    pub debounce_duration: Duration,

    /// Additional patterns to ignore
    pub ignore_patterns: Vec<String>,

    /// Whether to watch recursively
    pub recursive: bool,
}

impl Default for WatcherConfig {
    fn default() -> Self {
        Self {
            watch_paths: vec![PathBuf::from(".")],
            debounce_duration: Duration::from_millis(DEFAULT_DEBOUNCE_MS),
            ignore_patterns: Vec::new(),
            recursive: true,
        }
    }
}

/// File system watcher with debouncing
pub struct FileWatcher {
    /// Configuration
    config: WatcherConfig,

    /// Receiver for debounced events
    rx: Receiver<Result<Vec<DebouncedEvent>, notify::Error>>,

    /// The underlying debouncer (kept alive)
    _debouncer: Debouncer<RecommendedWatcher>,

    /// Set of paths currently being processed (for deduplication)
    processing: HashSet<PathBuf>,
}

impl FileWatcher {
    /// Create a new file watcher with the given configuration
    #[instrument(skip_all)]
    pub fn new(config: WatcherConfig) -> Result<Self, WatcherError> {
        let (tx, rx) = channel();

        // Create the debouncer
        let mut debouncer = new_debouncer(config.debounce_duration, tx)
            .map_err(|e| WatcherError::InitError(e.to_string()))?;

        // Add watch paths
        let mode = if config.recursive {
            RecursiveMode::Recursive
        } else {
            RecursiveMode::NonRecursive
        };

        for path in &config.watch_paths {
            let canonical = path.canonicalize().unwrap_or_else(|_| path.clone());
            info!("Watching path: {}", canonical.display());

            debouncer
                .watcher()
                .watch(&canonical, mode)
                .map_err(|e| WatcherError::WatchPathError(format!("{}: {}", path.display(), e)))?;
        }

        Ok(Self {
            config,
            rx,
            _debouncer: debouncer,
            processing: HashSet::new(),
        })
    }

    /// Create a watcher for the current directory
    pub fn for_current_dir() -> Result<Self, WatcherError> {
        Self::new(WatcherConfig::default())
    }

    /// Check if a path should be ignored
    fn should_ignore(&self, path: &Path) -> bool {
        let path_str = path.to_string_lossy();

        // Check built-in ignore patterns
        for pattern in IGNORE_PATTERNS {
            if path_str.contains(pattern) {
                return true;
            }
        }

        // Check custom ignore patterns
        for pattern in &self.config.ignore_patterns {
            if path_str.contains(pattern) {
                return true;
            }
        }

        false
    }

    /// Check if a file is a supported source file
    fn is_supported_file(&self, path: &Path) -> bool {
        // Must be a file
        if !path.is_file() {
            return false;
        }

        // Check extension
        let ext = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e.to_lowercase());

        match ext {
            Some(ref e) => WATCH_EXTENSIONS.contains(&e.as_str()),
            None => false,
        }
    }

    /// Process a debounced event into a FileChangeEvent
    fn process_event(&self, event: &DebouncedEvent) -> Option<FileChangeEvent> {
        let path = &event.path;

        // Skip ignored paths
        if self.should_ignore(path) {
            debug!("Ignoring path: {}", path.display());
            return None;
        }

        // For existing files, check if supported
        // For deleted files, check extension only
        if path.exists() {
            if !self.is_supported_file(path) {
                return None;
            }
        } else {
            // File was deleted - check extension
            let ext = path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e.to_lowercase());

            match ext {
                Some(ref e) if WATCH_EXTENSIONS.contains(&e.as_str()) => {}
                _ => return None,
            }
        }

        // Determine change kind based on file existence
        // (DebouncedEvent doesn't preserve the original event kind well)
        let kind = if path.exists() {
            FileChangeKind::Modified // Could be created or modified
        } else {
            FileChangeKind::Deleted
        };

        Some(FileChangeEvent {
            path: path.clone(),
            kind,
        })
    }

    /// Wait for and receive the next batch of file change events
    ///
    /// This blocks until events are available or an error occurs.
    pub fn next_events(&mut self) -> Result<Vec<FileChangeEvent>, WatcherError> {
        // Wait for events from the debouncer
        let result = self
            .rx
            .recv()
            .map_err(|e| WatcherError::EventError(format!("Channel error: {}", e)))?;

        match result {
            Ok(debounced_events) => {
                let mut events = Vec::new();
                let mut seen_paths = HashSet::new();

                for event in debounced_events {
                    // Deduplicate by path
                    if seen_paths.contains(&event.path) {
                        continue;
                    }
                    seen_paths.insert(event.path.clone());

                    if let Some(change_event) = self.process_event(&event) {
                        events.push(change_event);
                    }
                }

                Ok(events)
            }
            Err(error) => {
                // Log error but continue
                warn!("Watcher error: {:?}", error);
                Ok(Vec::new())
            }
        }
    }

    /// Try to receive events without blocking
    pub fn try_next_events(&mut self) -> Option<Vec<FileChangeEvent>> {
        match self.rx.try_recv() {
            Ok(Ok(debounced_events)) => {
                let mut events = Vec::new();
                let mut seen_paths = HashSet::new();

                for event in debounced_events {
                    if seen_paths.contains(&event.path) {
                        continue;
                    }
                    seen_paths.insert(event.path.clone());

                    if let Some(change_event) = self.process_event(&event) {
                        events.push(change_event);
                    }
                }

                Some(events)
            }
            Ok(Err(error)) => {
                warn!("Watcher error: {:?}", error);
                Some(Vec::new())
            }
            Err(_) => None, // No events available
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::Write;
    use tempfile::TempDir;

    #[test]
    fn test_should_ignore() {
        let config = WatcherConfig::default();
        let watcher = FileWatcher::new(config).unwrap();

        assert!(watcher.should_ignore(Path::new("/project/node_modules/foo.js")));
        assert!(watcher.should_ignore(Path::new("/project/target/debug/main")));
        assert!(watcher.should_ignore(Path::new("/project/.git/config")));
        assert!(!watcher.should_ignore(Path::new("/project/src/main.rs")));
    }

    #[test]
    fn test_is_supported_file() {
        let temp_dir = TempDir::new().unwrap();
        let rs_file = temp_dir.path().join("test.rs");
        let py_file = temp_dir.path().join("test.py");
        let txt_file = temp_dir.path().join("test.txt");

        fs::write(&rs_file, "fn main() {}").unwrap();
        fs::write(&py_file, "def main(): pass").unwrap();
        fs::write(&txt_file, "hello").unwrap();

        let config = WatcherConfig::default();
        let watcher = FileWatcher::new(config).unwrap();

        assert!(watcher.is_supported_file(&rs_file));
        assert!(watcher.is_supported_file(&py_file));
        assert!(!watcher.is_supported_file(&txt_file));
    }
}
