"""Embedding service for semantic search using sentence-transformers."""

import asyncio
from typing import List, Optional

import structlog

logger = structlog.get_logger()


class EmbeddingService:
    """Handles text embedding generation using sentence-transformers.

    Uses lazy loading to avoid startup delay - model is only loaded
    when first embedding is requested.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """Initialize the embedding service.

        Args:
            model_name: Name of the sentence-transformers model to use.
                       Default is all-MiniLM-L6-v2 (384 dimensions, fast, ~80MB).
        """
        self._model = None
        self._model_name = model_name
        self._dimension: Optional[int] = None

    @property
    def model(self):
        """Lazy load the embedding model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                logger.info("loading_embedding_model", model=self._model_name)
                self._model = SentenceTransformer(self._model_name)
                self._dimension = self._model.get_sentence_embedding_dimension()
                logger.info(
                    "embedding_model_loaded",
                    model=self._model_name,
                    dimension=self._dimension
                )
            except ImportError:
                logger.error("sentence_transformers_not_installed")
                raise RuntimeError(
                    "sentence-transformers is not installed. "
                    "Install with: pip install sentence-transformers"
                )
        return self._model

    @property
    def dimension(self) -> int:
        """Get the embedding dimension (loads model if needed)."""
        if self._dimension is None:
            _ = self.model  # Force model load
        return self._dimension or 384  # Default for all-MiniLM-L6-v2

    @property
    def is_loaded(self) -> bool:
        """Check if the model is loaded."""
        return self._model is not None

    def _embed_sync(self, text: str) -> List[float]:
        """Synchronous embedding generation.

        Args:
            text: Text to embed (will be truncated to model's max tokens)

        Returns:
            List of floats representing the embedding
        """
        # Truncate to reasonable length (model typically handles up to 512 tokens)
        max_chars = 2000  # Roughly corresponds to ~500 tokens
        if len(text) > max_chars:
            text = text[:max_chars]

        embedding = self.model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def _embed_batch_sync(self, texts: List[str]) -> List[List[float]]:
        """Synchronous batch embedding generation.

        Args:
            texts: List of texts to embed

        Returns:
            List of embeddings
        """
        # Truncate each text
        max_chars = 2000
        truncated_texts = [t[:max_chars] if len(t) > max_chars else t for t in texts]

        embeddings = self.model.encode(truncated_texts, convert_to_numpy=True)
        return [e.tolist() for e in embeddings]

    async def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text.

        Runs in thread pool to avoid blocking the event loop.

        Args:
            text: Text to embed

        Returns:
            List of floats representing the embedding
        """
        return await asyncio.to_thread(self._embed_sync, text)

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts.

        More efficient than calling embed() multiple times.

        Args:
            texts: List of texts to embed

        Returns:
            List of embeddings
        """
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_batch_sync, texts)

    async def similarity(self, text1: str, text2: str) -> float:
        """Calculate cosine similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Cosine similarity score (0 to 1)
        """
        embeddings = await self.embed_batch([text1, text2])
        return self._cosine_similarity(embeddings[0], embeddings[1])

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors.

        Args:
            vec1: First vector
            vec2: Second vector

        Returns:
            Cosine similarity (0 to 1)
        """
        import math

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)


# Global embedding service instance (lazy initialization)
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service(model_name: str = "all-MiniLM-L6-v2") -> EmbeddingService:
    """Get or create the global embedding service instance.

    Args:
        model_name: Model name (only used on first call)

    Returns:
        EmbeddingService instance
    """
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService(model_name)
    return _embedding_service
