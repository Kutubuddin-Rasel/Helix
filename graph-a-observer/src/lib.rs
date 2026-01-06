//! Graph A Observer - Module declarations
//!
//! Re-exports core modules for the observer binary.
//!
//! Note: Some items are unused in the current binary but are part of the
//! public library API for future use and testing.

// Allow dead code for public API that may not be used yet
#![allow(dead_code)]

// Allow enum variant naming (Error suffix is intentional for error types)
#![allow(clippy::enum_variant_names)]

// Allow recursive parameter usage (depth tracking in AST traversal)
#![allow(clippy::only_used_in_recursion)]

pub mod db;
pub mod parser;
pub mod publisher;
pub mod watcher;

