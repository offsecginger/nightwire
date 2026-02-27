"""Memory system for Signal Claude Bot.

Provides episodic memory with vector search for context-aware conversations.
"""

from .commands import MemoryCommands
from .context_builder import ContextBuilder
from .database import (
    DatabaseConnection,
    get_database,
    initialize_database,
)
from .embeddings import EmbeddingService, get_embedding_service
from .haiku_summarizer import HaikuSummarizer, get_haiku_summarizer
from .manager import (
    MemoryManager,
    get_memory_manager,
    initialize_memory_manager,
)
from .models import (
    Conversation,
    ExplicitMemory,
    MemoryContext,
    Preference,
    SearchResult,
    Session,
    User,
)

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
