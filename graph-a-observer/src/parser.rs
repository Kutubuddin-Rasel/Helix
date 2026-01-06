//! AST Parser Module - Project Helix Graph A Observer
//!
//! Provides language-agnostic AST parsing using Tree-sitter.
//! Supports 5 languages per Blueprint: Rust, Python, JavaScript/TypeScript, Go, Java.
//! Generates structural hashes (SHA-256 of AST s-expression) to detect
//! meaningful code changes vs. whitespace-only changes.
//! Extracts function definitions for Graph A [:DEFINES] relationships.

use crate::db::FunctionInfo;
use anyhow::Result;
use sha2::{Digest, Sha256};
use std::path::Path;
use thiserror::Error;
use tracing::{debug, instrument, warn};

/// Errors that can occur during parsing
#[derive(Error, Debug)]
pub enum ParseError {
    #[error("Unsupported file extension: {0}")]
    UnsupportedLanguage(String),

    #[error("Failed to read file: {0}")]
    FileReadError(String),

    #[error("Failed to parse file: {0}")]
    ParseFailure(String),

    #[error("Tree-sitter language initialization failed: {0}")]
    LanguageInitError(String),
}

/// Supported programming languages (Blueprint: 5 languages)
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Language {
    Rust,
    Python,
    JavaScript,
    TypeScript,
    Go,
    Java,
}

impl Language {
    /// Detect language from file extension
    pub fn from_extension(ext: &str) -> Option<Self> {
        match ext.to_lowercase().as_str() {
            "rs" => Some(Language::Rust),
            "py" | "pyw" | "pyi" => Some(Language::Python),
            "js" | "jsx" | "mjs" | "cjs" => Some(Language::JavaScript),
            "ts" | "tsx" | "mts" | "cts" => Some(Language::TypeScript),
            "go" => Some(Language::Go),
            "java" => Some(Language::Java),
            _ => None,
        }
    }

    /// Get the tree-sitter language for this language type
    fn tree_sitter_language(&self) -> tree_sitter::Language {
        match self {
            Language::Rust => tree_sitter_rust::language(),
            Language::Python => tree_sitter_python::language(),
            Language::JavaScript | Language::TypeScript => tree_sitter_javascript::language(),
            Language::Go => tree_sitter_go::language(),
            Language::Java => tree_sitter_java::language(),
        }
    }

    /// Get the node kind that represents a function definition in this language
    fn function_node_kinds(&self) -> &'static [&'static str] {
        match self {
            Language::Rust => &["function_item", "impl_item"],
            Language::Python => &["function_definition", "async_function_definition"],
            Language::JavaScript | Language::TypeScript => &[
                "function_declaration",
                "function_expression",
                "arrow_function",
                "method_definition",
                "variable_declarator",  // For const foo = () => {} pattern
            ],
            Language::Go => &["function_declaration", "method_declaration"],
            Language::Java => &["method_declaration", "constructor_declaration"],
        }
    }
}

impl std::fmt::Display for Language {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Language::Rust => write!(f, "Rust"),
            Language::Python => write!(f, "Python"),
            Language::JavaScript => write!(f, "JavaScript"),
            Language::TypeScript => write!(f, "TypeScript"),
            Language::Go => write!(f, "Go"),
            Language::Java => write!(f, "Java"),
        }
    }
}

/// Result of parsing a file
#[derive(Debug, Clone)]
pub struct ParseResult {
    /// Path to the parsed file
    pub file_path: String,

    /// Detected programming language
    pub language: Language,

    /// SHA-256 hash of the AST structure (s-expression)
    pub structural_hash: String,

    /// Number of top-level nodes in the AST
    pub node_count: usize,

    /// Whether the parse had any syntax errors
    pub has_errors: bool,

    /// Extracted function definitions
    pub functions: Vec<FunctionInfo>,

    /// Extracted doc comments (///, //!, docstrings)
    pub doc_comments: Vec<String>,

    /// Semantic keywords for embedding-based search (max 500 chars)
    pub semantic_keywords: String,
}

/// AST Parser using Tree-sitter
pub struct Parser {
    /// Tree-sitter parser instance
    parser: tree_sitter::Parser,
}

impl Parser {
    /// Create a new parser instance
    pub fn new() -> Result<Self> {
        let parser = tree_sitter::Parser::new();
        Ok(Self { parser })
    }

    /// Check if a file is supported for parsing
    #[allow(dead_code)]
    pub fn is_supported(path: &Path) -> bool {
        path.extension()
            .and_then(|e| e.to_str())
            .and_then(Language::from_extension)
            .is_some()
    }

    /// Parse a file and return the structural hash
    #[instrument(skip(self), fields(path = %path.display()))]
    pub fn parse_file(&mut self, path: &Path) -> Result<ParseResult, ParseError> {
        // Detect language from extension
        let ext = path
            .extension()
            .and_then(|e| e.to_str())
            .ok_or_else(|| ParseError::UnsupportedLanguage("no extension".to_string()))?;

        let language = Language::from_extension(ext)
            .ok_or_else(|| ParseError::UnsupportedLanguage(ext.to_string()))?;

        debug!("Detected language: {} for extension: {}", language, ext);

        // Set the language for the parser
        self.parser
            .set_language(language.tree_sitter_language())
            .map_err(|e| ParseError::LanguageInitError(e.to_string()))?;

        // Read the file content
        let content = std::fs::read_to_string(path)
            .map_err(|e| ParseError::FileReadError(format!("{}: {}", path.display(), e)))?;

        // Parse the content
        let tree = self
            .parser
            .parse(&content, None)
            .ok_or_else(|| ParseError::ParseFailure(path.display().to_string()))?;

        let root_node = tree.root_node();

        // Check for parse errors
        let has_errors = root_node.has_error();
        if has_errors {
            warn!("Parse errors detected in {}", path.display());
        }

        // Generate structural hash from AST
        let structural_hash = self.compute_structural_hash(&root_node, &content);

        // Count top-level nodes
        let node_count = root_node.child_count();

        // Extract function definitions
        let functions = self.extract_functions(&root_node, &content, &language);

        // Extract doc comments (///, //!, docstrings)
        let doc_comments = self.extract_doc_comments(&root_node, &content, &language);

        // Extract semantic keywords for embedding-based search
        let semantic_keywords = self.extract_semantic_keywords(&root_node, &content);

        Ok(ParseResult {
            file_path: path.display().to_string(),
            language,
            structural_hash,
            node_count,
            has_errors,
            functions,
            doc_comments,
            semantic_keywords,
        })
    }

    /// Extract function definitions from the AST
    fn extract_functions(
        &self,
        root: &tree_sitter::Node,
        source: &str,
        language: &Language,
    ) -> Vec<FunctionInfo> {
        let mut functions = Vec::new();
        let function_kinds = language.function_node_kinds();

        self.walk_for_functions(root, source, language, function_kinds, &mut functions);

        functions
    }

    /// Extract doc comments from the AST (///, //!, docstrings)
    /// Returns up to 5 most relevant doc comments, truncated to 200 chars each
    fn extract_doc_comments(
        &self,
        root: &tree_sitter::Node,
        source: &str,
        language: &Language,
    ) -> Vec<String> {
        let mut comments = Vec::new();
        self.walk_for_doc_comments(root, source, language, &mut comments);
        
        // Limit to 5 comments, 200 chars each
        comments
            .into_iter()
            .take(5)
            .map(|c| if c.len() > 200 { c[..200].to_string() + "..." } else { c })
            .collect()
    }

    /// Recursively walk AST to find doc comments
    fn walk_for_doc_comments(
        &self,
        node: &tree_sitter::Node,
        source: &str,
        language: &Language,
        comments: &mut Vec<String>,
    ) {
        let kind = node.kind();
        
        // Check if this is a doc comment based on language
        let is_doc_comment = match language {
            Language::Rust => kind == "line_comment" || kind == "block_comment",
            Language::Python => kind == "string" || kind == "comment",
            Language::JavaScript | Language::TypeScript => kind == "comment",
            Language::Go => kind == "comment",
            Language::Java => kind == "line_comment" || kind == "block_comment",
        };

        if is_doc_comment {
            let text = node
                .utf8_text(source.as_bytes())
                .unwrap_or("")
                .trim()
                .to_string();
            
            // Filter for actual doc comments (///, //!, /** */, docstrings)
            let is_doc = match language {
                Language::Rust => text.starts_with("///") || text.starts_with("//!"),
                Language::Python => {
                    // Python docstrings start with """ or '''
                    text.starts_with("\"\"\"") || text.starts_with("'''")
                }
                Language::JavaScript | Language::TypeScript => {
                    text.starts_with("/**") || text.contains("@param") || text.contains("@returns")
                }
                Language::Go => text.starts_with("//"),
                Language::Java => text.starts_with("/**"),
            };

            if is_doc && !text.is_empty() && text.len() > 3 {
                // Clean up the comment text
                let cleaned = text
                    .trim_start_matches("///")
                    .trim_start_matches("//!")
                    .trim_start_matches("/**")
                    .trim_end_matches("*/")
                    .trim_start_matches("//")
                    .trim_start_matches("\"\"\"")
                    .trim_end_matches("\"\"\"")
                    .trim()
                    .to_string();
                
                if !cleaned.is_empty() {
                    comments.push(cleaned);
                }
            }
        }

        // Recurse into children
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            self.walk_for_doc_comments(&child, source, language, comments);
        }
    }

    /// Extract semantic keywords from code for embedding-based search
    /// Collects identifiers, string literals, and properties (max 500 chars)
    fn extract_semantic_keywords(&self, root: &tree_sitter::Node, source: &str) -> String {
        let mut keywords: Vec<String> = Vec::new();
        self.walk_for_keywords(root, source, &mut keywords);
        
        // Deduplicate while preserving order
        let mut seen = std::collections::HashSet::new();
        let unique: Vec<String> = keywords
            .into_iter()
            .filter(|k| seen.insert(k.clone()))
            .collect();
        
        // Join and truncate to 500 chars
        let result = unique.join(" ");
        if result.len() > 500 {
            result[..500].to_string()
        } else {
            result
        }
    }

    /// Recursively walk AST to collect meaningful tokens
    fn walk_for_keywords(
        &self,
        node: &tree_sitter::Node,
        source: &str,
        keywords: &mut Vec<String>,
    ) {
        let kind = node.kind();
        
        // Common keywords to filter out (syntax, not semantic)
        const SKIP_KEYWORDS: &[&str] = &[
            "const", "let", "var", "function", "return", "if", "else", "for", "while",
            "import", "export", "from", "async", "await", "new", "this", "true", "false",
            "null", "undefined", "try", "catch", "throw", "class", "extends", "super",
            "pub", "fn", "impl", "struct", "enum", "mod", "use", "mut", "self", "Self",
            "def", "print", "pass", "None", "True", "False", "lambda", "with", "as",
        ];
        
        // Token types to extract
        let is_extractable = matches!(kind,
            "identifier" | "property_identifier" | "shorthand_property_identifier" |
            "string" | "string_fragment" | "template_string" |
            "type_identifier" | "field_identifier"
        );

        if is_extractable {
            if let Ok(text) = node.utf8_text(source.as_bytes()) {
                let cleaned = text
                    .trim()
                    .trim_matches('"')
                    .trim_matches('\'')
                    .trim_matches('`')
                    .to_string();
                
                // Filter: length >= 2, not common keyword, not all digits
                if cleaned.len() >= 2 
                    && !SKIP_KEYWORDS.contains(&cleaned.as_str())
                    && !cleaned.chars().all(|c| c.is_digit(10))
                {
                    keywords.push(cleaned);
                }
            }
        }

        // Recurse into children
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            self.walk_for_keywords(&child, source, keywords);
        }
    }

    /// Recursively walk the AST to find function definitions
    fn walk_for_functions(
        &self,
        node: &tree_sitter::Node,
        source: &str,
        language: &Language,
        function_kinds: &[&str],
        functions: &mut Vec<FunctionInfo>,
    ) {
        let kind = node.kind();

        // Check if this node is a function definition
        if function_kinds.contains(&kind) {
            if let Some(func_info) = self.extract_function_info(node, source, language) {
                functions.push(func_info);
            }
        }

        // Recurse into children
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            self.walk_for_functions(&child, source, language, function_kinds, functions);
        }
    }

    /// Extract function information from a function node
    fn extract_function_info(
        &self,
        node: &tree_sitter::Node,
        source: &str,
        language: &Language,
    ) -> Option<FunctionInfo> {
        // For JavaScript variable_declarator, only extract if it contains an arrow function
        if matches!(language, Language::JavaScript | Language::TypeScript) 
            && node.kind() == "variable_declarator" 
        {
            // Check if this variable_declarator contains an arrow_function
            let mut has_arrow = false;
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                if child.kind() == "arrow_function" {
                    has_arrow = true;
                    break;
                }
            }
            if !has_arrow {
                return None;  // Not an arrow function assignment, skip
            }
        }

        let start_line = node.start_position().row as u32 + 1;
        let end_line = node.end_position().row as u32 + 1;

        // Find the function name based on language
        let name = self.find_function_name(node, source, language)?;

        // Build signature (first line of the function)
        let signature = self.extract_signature(node, source);

        Some(FunctionInfo {
            name,
            start_line,
            end_line,
            signature: Some(signature),
        })
    }

    /// Find the function name from a function node
    fn find_function_name(
        &self,
        node: &tree_sitter::Node,
        source: &str,
        language: &Language,
    ) -> Option<String> {
        // Look for identifier/name child nodes
        let name_kinds: &[&str] = match language {
            Language::Rust => &["identifier", "type_identifier"],
            Language::Python => &["identifier"],
            Language::JavaScript | Language::TypeScript => &["identifier", "property_identifier"],
            Language::Go => &["identifier"],
            Language::Java => &["identifier"],
        };

        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            if name_kinds.contains(&child.kind()) {
                if let Ok(text) = child.utf8_text(source.as_bytes()) {
                    return Some(text.to_string());
                }
            }
            // Also check for name field if it exists
            if child.kind() == "name" {
                if let Ok(text) = child.utf8_text(source.as_bytes()) {
                    return Some(text.to_string());
                }
            }
        }

        // For JavaScript arrow functions assigned to variables (const foo = () => {}),
        // the name is in the parent variable_declarator, not the arrow_function itself
        if matches!(language, Language::JavaScript | Language::TypeScript) 
            && node.kind() == "arrow_function" 
        {
            if let Some(parent) = node.parent() {
                if parent.kind() == "variable_declarator" {
                    let mut parent_cursor = parent.walk();
                    for child in parent.children(&mut parent_cursor) {
                        if child.kind() == "identifier" {
                            if let Ok(text) = child.utf8_text(source.as_bytes()) {
                                return Some(text.to_string());
                            }
                        }
                    }
                }
            }
        }

        None
    }

    /// Extract the function signature (first line)
    fn extract_signature(&self, node: &tree_sitter::Node, source: &str) -> String {
        let start = node.start_byte();
        let text = &source[start..];

        // Get first line or until opening brace
        let signature: String = text
            .lines()
            .next()
            .unwrap_or("")
            .chars()
            .take(200) // Limit length
            .collect();

        signature.trim().to_string()
    }

    /// Compute a SHA-256 hash of the AST structure
    fn compute_structural_hash(&self, root: &tree_sitter::Node, source: &str) -> String {
        let mut hasher = Sha256::new();
        let structure = self.node_to_structure(root, source, 0);
        hasher.update(structure.as_bytes());
        let result = hasher.finalize();
        format!("{:x}", result)
    }

    /// Convert a node to a structural string representation
    fn node_to_structure(&self, node: &tree_sitter::Node, source: &str, depth: usize) -> String {
        let kind = node.kind();

        // Skip comments and whitespace in structural hash
        if kind == "comment"
            || kind == "line_comment"
            || kind == "block_comment"
            || kind == "documentation_comment"
        {
            return String::new();
        }

        let mut result = String::new();
        result.push('(');
        result.push_str(kind);

        // For identifiers and other named nodes, include the text
        if Self::is_structural_identifier(kind) {
            if let Ok(text) = node.utf8_text(source.as_bytes()) {
                result.push(' ');
                result.push_str(text);
            }
        }

        // Recursively process children
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            let child_structure = self.node_to_structure(&child, source, depth + 1);
            if !child_structure.is_empty() {
                result.push(' ');
                result.push_str(&child_structure);
            }
        }

        result.push(')');
        result
    }

    /// Check if a node kind represents a structural identifier
    fn is_structural_identifier(kind: &str) -> bool {
        matches!(
            kind,
            "identifier"
                | "type_identifier"
                | "field_identifier"
                | "property_identifier"
                | "shorthand_property_identifier"
                | "attribute"
                | "name"
                | "primitive_type"
                | "integer_literal"
                | "float_literal"
                | "boolean_literal"
                | "number"
                | "true"
                | "false"
                | "null"
        )
    }
}

impl Default for Parser {
    fn default() -> Self {
        Self::new().expect("Failed to create parser")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    #[test]
    fn test_language_detection() {
        assert_eq!(Language::from_extension("rs"), Some(Language::Rust));
        assert_eq!(Language::from_extension("py"), Some(Language::Python));
        assert_eq!(Language::from_extension("js"), Some(Language::JavaScript));
        assert_eq!(Language::from_extension("ts"), Some(Language::TypeScript));
        assert_eq!(Language::from_extension("go"), Some(Language::Go));
        assert_eq!(Language::from_extension("java"), Some(Language::Java));
        assert_eq!(Language::from_extension("unknown"), None);
    }

    #[test]
    fn test_parse_rust_file() {
        let mut parser = Parser::new().unwrap();

        let mut temp_file = NamedTempFile::with_suffix(".rs").unwrap();
        writeln!(temp_file, "fn hello() {{ println!(\"Hello\"); }}").unwrap();

        let result = parser.parse_file(temp_file.path()).unwrap();

        assert_eq!(result.language, Language::Rust);
        assert!(!result.structural_hash.is_empty());
        assert!(!result.has_errors);
        assert!(!result.functions.is_empty());
        assert_eq!(result.functions[0].name, "hello");
    }

    #[test]
    fn test_parse_python_file() {
        let mut parser = Parser::new().unwrap();

        let mut temp_file = NamedTempFile::with_suffix(".py").unwrap();
        writeln!(temp_file, "def hello():\n    print('Hello')").unwrap();

        let result = parser.parse_file(temp_file.path()).unwrap();

        assert_eq!(result.language, Language::Python);
        assert!(!result.functions.is_empty());
        assert_eq!(result.functions[0].name, "hello");
    }

    #[test]
    fn test_parse_javascript_file() {
        let mut parser = Parser::new().unwrap();

        let mut temp_file = NamedTempFile::with_suffix(".js").unwrap();
        writeln!(temp_file, "function hello() {{ console.log('Hello'); }}").unwrap();

        let result = parser.parse_file(temp_file.path()).unwrap();

        assert_eq!(result.language, Language::JavaScript);
        assert!(!result.structural_hash.is_empty());
    }

    #[test]
    fn test_structural_hash_consistency() {
        let mut parser = Parser::new().unwrap();

        let mut file1 = NamedTempFile::with_suffix(".rs").unwrap();
        writeln!(file1, "fn foo() {{ let x = 1; }}").unwrap();

        let mut file2 = NamedTempFile::with_suffix(".rs").unwrap();
        writeln!(file2, "fn   foo()  {{\n    let   x   =   1;\n}}").unwrap();

        let result1 = parser.parse_file(file1.path()).unwrap();
        let result2 = parser.parse_file(file2.path()).unwrap();

        // Structural hashes should be identical (whitespace ignored)
        assert_eq!(result1.structural_hash, result2.structural_hash);
    }
}
