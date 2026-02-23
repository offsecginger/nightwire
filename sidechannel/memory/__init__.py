"""Memory system for Signal Claude Bot.

Provides episodic memory with vector search for context-aware conversations.
"""

from .models import (
    User,
    Session,
    Conversation,
    Preference,
    ExplicitMemory,
    SearchResult,
    MemoryContext,
)
from .manager import (
    MemoryManager,
    get_memory_manager,
    initialize_memory_manager,
)
from .database import (
    DatabaseConnection,
    get_database,
    initialize_database,
)
from .commands import MemoryCommands
from .embeddings import EmbeddingService, get_embedding_service
from .context_builder import ContextBuilder
from .haiku_summarizer import HaikuSummarizer, get_haiku_summarizer

__all__ = [
    # Models
    "User",
    "Session",
    "Conversation",
    "Preference",
    "ExplicitMemory",
    "SearchResult",
    "MemoryContext",
    # Manager
    "MemoryManager",
    "get_memory_manager",
    "initialize_memory_manager",
    # Database
    "DatabaseConnection",
    "get_database",
    "initialize_database",
    # Commands
    "MemoryCommands",
    # Embeddings
    "EmbeddingService",
    "get_embedding_service",
    # Context
    "ContextBuilder",
    "HaikuSummarizer",
    "get_haiku_summarizer",
]
