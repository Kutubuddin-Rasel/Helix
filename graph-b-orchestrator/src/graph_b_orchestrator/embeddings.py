"""
Embedding Engine Module - Project Helix Graph B Orchestrator

Provides vector embeddings for semantic search using fastembed.
Enables queries like "Find all changes related to authentication."

Architecture:
- Uses BAAI/bge-small-en-v1.5 (384 dimensions, runs on CPU)
- Singleton pattern to avoid reloading model on each call
- Thread-safe for concurrent access
"""

from functools import lru_cache
from typing import TYPE_CHECKING, Optional

import structlog

if TYPE_CHECKING:
    from fastembed import TextEmbedding

logger = structlog.get_logger()

# Model configuration
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSION = 384

# Pre-allocated zero vector (avoids allocation on every empty text)
# This is a single allocation reused across all empty-text cases
ZERO_VECTOR: list[float] = [0.0] * EMBEDDING_DIMENSION


class EmbeddingEngine:
    """
    Singleton embedding engine using fastembed.
    
    Generates dense vector embeddings for semantic similarity search.
    Model is loaded lazily on first use and cached.
    """
    
    _instance: Optional["EmbeddingEngine"] = None
    _initialized: bool = False
    
    def __new__(cls) -> "EmbeddingEngine":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        if self.__class__._initialized:
            return  # Already initialized
            
        self.model_name = model_name
        self._model: Optional["TextEmbedding"] = None
        self.__class__._initialized = True
        
    def _ensure_model(self) -> None:
        """Lazily load the embedding model."""
        if self._model is None:
            logger.info(
                "Loading embedding model",
                model=self.model_name,
            )
            
            from fastembed import TextEmbedding
            
            self._model = TextEmbedding(
                model_name=self.model_name,
            )
            
            logger.info(
                "Embedding model loaded",
                model=self.model_name,
                dimension=EMBEDDING_DIMENSION,
            )
    
    def generate_embedding(self, text: str) -> list[float]:
        """
        Generate a vector embedding for the given text.
        
        Args:
            text: The text to embed
            
        Returns:
            List of floats representing the embedding vector
        """
        if not text or not text.strip():
            # Return zero vector for empty text
            return ZERO_VECTOR
        
        self._ensure_model()
        
        # fastembed returns a generator, get first result
        if self._model is None:
            return ZERO_VECTOR
            
        embeddings = list(self._model.embed([text]))
        
        if embeddings:
            # Convert numpy array to list of floats
            embedding: list[float] = embeddings[0].tolist()
            logger.debug(
                "Generated embedding",
                text_length=len(text),
                embedding_dimension=len(embedding),
            )
            return embedding
        
        return [0.0] * EMBEDDING_DIMENSION
    
    def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple texts efficiently.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []
        
        self._ensure_model()
        
        # Filter empty texts but track positions
        valid_texts = []
        valid_indices = []
        
        for i, text in enumerate(texts):
            if text and text.strip():
                valid_texts.append(text)
                valid_indices.append(i)
        
        if not valid_texts:
            return [list(ZERO_VECTOR) for _ in texts]
        
        # Generate embeddings in batch
        if self._model is None:
            return [list(ZERO_VECTOR) for _ in texts]
            
        embeddings = list(self._model.embed(valid_texts))
        
        # Reconstruct result with zeros for empty texts
        result = [list(ZERO_VECTOR) for _ in texts]
        for i, embedding in zip(valid_indices, embeddings):
            result[i] = embedding.tolist()
        
        logger.debug(
            "Generated batch embeddings",
            total_texts=len(texts),
            valid_texts=len(valid_texts),
        )
        
        return result
    
    @property
    def dimension(self) -> int:
        """Return the embedding dimension."""
        return EMBEDDING_DIMENSION


@lru_cache(maxsize=1)
def get_embedding_engine() -> EmbeddingEngine:
    """Get the singleton embedding engine instance."""
    return EmbeddingEngine()


def generate_embedding(text: str) -> list[float]:
    """
    Convenience function to generate an embedding.
    
    Uses the singleton engine instance.
    """
    engine = get_embedding_engine()
    return engine.generate_embedding(text)


def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """
    Convenience function to generate embeddings in batch.
    
    Uses the singleton engine instance.
    """
    engine = get_embedding_engine()
    return engine.generate_embeddings_batch(texts)
