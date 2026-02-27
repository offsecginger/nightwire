"""Context builder for Claude prompt injection.

Assembles user preferences, explicit memories, and relevant
conversation history into a formatted context string that is
prepended to Claude prompts. Enforces a configurable token budget
using a rough character-based estimate (4 chars per token).

Key class:
    ContextBuilder -- formats and assembles context sections
        within a token budget. Supports both raw history and
        pre-summarized context (from HaikuSummarizer).
"""

from typing import List, Optional

import structlog

from .models import ExplicitMemory, Preference, SearchResult

logger = structlog.get_logger("nightwire.memory")


class ContextBuilder:
    """Builds context sections for injection into Claude prompts.

    Formats preferences, memories, and relevant history into a structured
    context string that can be prepended to prompts.
    """

    def __init__(self, max_tokens: int = 1500):
        """Initialize the context builder.

        Args:
            max_tokens: Maximum approximate tokens for the context section
        """
        self.max_tokens = max_tokens
        # Rough estimate: 1 token ≈ 4 characters
        self.max_chars = max_tokens * 4

    def build_context_section(
        self,
        preferences: Optional[List[Preference]] = None,
        explicit_memories: Optional[List[ExplicitMemory]] = None,
        relevant_history: Optional[List[SearchResult]] = None,
        summarized_context: Optional[str] = None,
        current_project: Optional[str] = None
    ) -> str:
        """Build a context section to prepend to prompts.

        Args:
            preferences: User preferences
            explicit_memories: Explicit memories from /remember
            relevant_history: Relevant past conversations
            summarized_context: Pre-summarized context (from Haiku)
            current_project: Current project name for filtering

        Returns:
            Formatted context string, or empty string if no context
        """
        sections = []
        remaining_chars = self.max_chars

        # Add preferences section
        if preferences:
            pref_section = self._format_preferences(preferences)
            if pref_section and len(pref_section) < remaining_chars:
                sections.append(pref_section)
                remaining_chars -= len(pref_section)

        # Add explicit memories section
        if explicit_memories:
            mem_section = self._format_memories(explicit_memories)
            if mem_section and len(mem_section) < remaining_chars:
                sections.append(mem_section)
                remaining_chars -= len(mem_section)

        # Add summarized context if available (preferred over raw history)
        if summarized_context:
            summary_section = f"## Relevant Past Context\n{summarized_context}"
            if len(summary_section) < remaining_chars:
                sections.append(summary_section)
                remaining_chars -= len(summary_section)
        # Otherwise add raw history snippets
        elif relevant_history:
            history_section = self._format_history(relevant_history, remaining_chars)
            if history_section:
                sections.append(history_section)

        if not sections:
            return ""

        context = (
            "---\n"
            "# Memory Context (from past conversations)\n\n"
            + "\n\n".join(sections)
            + "\n---\n\n"
        )

        used_tokens = len(context) // 4  # Rough estimate: 4 chars per token
        logger.debug("context_budget", max_tokens=self.max_tokens, used_tokens=used_tokens)

        return context

    def _format_preferences(self, preferences: List[Preference]) -> str:
        """Format preferences grouped by category.

        Limits to 5 preferences per category to avoid bloat.
        """
        if not preferences:
            return ""

        # Group by category
        by_category: dict[str, list] = {}
        for pref in preferences:
            if pref.category not in by_category:
                by_category[pref.category] = []
            by_category[pref.category].append(pref)

        lines = ["## User Preferences"]
        for category, prefs in sorted(by_category.items()):
            for p in prefs[:5]:  # Limit per category
                lines.append(f"- {category}/{p.key}: {p.value}")

        return "\n".join(lines)

    def _format_memories(self, memories: List[ExplicitMemory]) -> str:
        """Format explicit memories as bullet points.

        Limits to 10 memories, each truncated to 200 chars.
        """
        if not memories:
            return ""

        lines = ["## Remembered Facts"]
        for mem in memories[:10]:  # Limit to 10 memories
            # Truncate long memories
            text = mem.memory_text[:200]
            if len(mem.memory_text) > 200:
                text += "..."
            lines.append(f"- {text}")

        return "\n".join(lines)

    def _format_history(
        self,
        history: List[SearchResult],
        max_chars: int
    ) -> str:
        """Format relevant history as dated conversation snippets.

        Stops adding entries once ``max_chars`` would be exceeded.
        Each entry is truncated to 300 chars with newlines
        collapsed.

        Args:
            history: Ranked search results to format.
            max_chars: Character budget for this section.

        Returns:
            Formatted history section, or empty string if no
            entries fit.
        """
        if not history:
            return ""

        lines = ["## Relevant Past Context"]
        current_length = len(lines[0])

        for result in history[:10]:  # Max 10 items
            date = result.timestamp.strftime("%Y-%m-%d")
            role = "User" if result.role == "user" else "Claude"

            # Truncate content
            content = result.content[:300].replace("\n", " ")
            if len(result.content) > 300:
                content += "..."

            line = f"[{date}] {role}: {content}"

            if current_length + len(line) + 1 > max_chars:
                break

            lines.append(line)
            current_length += len(line) + 1

        if len(lines) == 1:  # Only header, no content
            return ""

        return "\n".join(lines)

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for text.

        Args:
            text: Text to estimate

        Returns:
            Estimated token count
        """
        # Rough estimate: 1 token ≈ 4 characters
        return len(text) // 4
