"""Memory command handlers for Signal bot."""

from typing import TYPE_CHECKING, Optional

import structlog

if TYPE_CHECKING:
    from .manager import MemoryManager

logger = structlog.get_logger()


class MemoryCommands:
    """Handlers for memory-related slash commands."""

    def __init__(self, memory_manager: "MemoryManager"):
        self.memory = memory_manager

    async def handle_remember(self, phone: str, args: str, project: Optional[str] = None) -> str:
        """/remember <text> - Store an explicit memory for current project."""
        if not args.strip():
            if project:
                return f"Usage: /remember <something to remember>\n\nThis will be saved for project: {project}\nUse /global remember for cross-project memories."
            return "Usage: /remember <something to remember>\n\nNo project selected - this will be a global memory."

        content = args.strip()
        memory_id = await self.memory.remember(phone, content, project_name=project)
        preview = content[:50] + "..." if len(content) > 50 else content
        project_info = f" [{project}]" if project else " [global]"
        return f"Remembered{project_info}: \"{preview}\" (ID: {memory_id})"

    async def handle_recall(self, phone: str, args: str, project: Optional[str] = None) -> str:
        """/recall <query> - Semantic search past conversations for current project."""
        if not args.strip():
            if project:
                return f"Usage: /recall <search query>\n\nSearching in project: {project}\nUse /global recall to search all projects."
            return "Usage: /recall <search query>\n\nNo project selected - searching all projects."

        query = args.strip()
        results = await self.memory.semantic_search(phone, query, limit=5, project_name=project)

        if not results:
            scope = f" in {project}" if project else ""
            return f"No relevant memories found{scope} for: \"{query}\""

        scope = f" [{project}]" if project else " [all projects]"
        lines = [f"Found {len(results)} relevant memories{scope}:\n"]
        for i, r in enumerate(results, 1):
            date = r.timestamp.strftime("%Y-%m-%d")
            role = "You" if r.role == "user" else "Claude"
            preview = r.content[:100].replace("\n", " ")
            if len(r.content) > 100:
                preview += "..."
            # Show project tag if searching globally
            proj_tag = f" [{r.project_name}]" if r.project_name and not project else ""
            lines.append(f"{i}. [{date}]{proj_tag} {role}: {preview}")

        return "\n".join(lines)

    async def handle_history(self, phone: str, args: str, project: Optional[str] = None) -> str:
        """/history [count] - Show recent conversation history for current project."""
        limit = 10
        if args.strip():
            try:
                limit = int(args.strip())
                limit = max(1, min(limit, 50))  # Clamp between 1 and 50
            except ValueError:
                return "Usage: /history [count]\n\nExample: /history 20"

        history = await self.memory.get_history(phone, limit=limit, project_name=project)

        if not history:
            scope = f" for {project}" if project else ""
            return f"No conversation history found{scope}."

        scope = f" [{project}]" if project else " [all projects]"
        lines = [f"Last {len(history)} messages{scope}:\n"]
        for msg in history:
            date = msg.timestamp.strftime("%m/%d %H:%M")
            role = "You" if msg.role == "user" else "Claude"
            preview = msg.content[:80].replace("\n", " ")
            if len(msg.content) > 80:
                preview += "..."
            lines.append(f"[{date}] {role}: {preview}")

        return "\n".join(lines)

    async def handle_forget(self, phone: str, args: str) -> str:
        """/forget [all|preferences|today] - Delete memories."""
        target = args.strip().lower() if args else ""

        if not target or target == "help":
            return (
                "Usage: /forget <scope>\n\n"
                "Scopes:\n"
                "  all - Delete all your data\n"
                "  preferences - Clear learned preferences\n"
                "  today - Delete today's conversations"
            )

        if target not in ["all", "preferences", "today"]:
            return f"Unknown scope: \"{target}\"\n\nValid scopes: all, preferences, today"

        success = await self.memory.forget(phone, target)

        if success:
            if target == "all":
                return "All your data has been deleted."
            elif target == "preferences":
                return "Your preferences have been cleared."
            elif target == "today":
                return "Today's conversations have been deleted."

        return "Nothing to delete."

    async def handle_memories(self, phone: str, args: str, project: Optional[str] = None) -> str:
        """/memories - List stored explicit memories for current project."""
        memories = await self.memory.get_memories(phone, limit=20, project_name=project)

        if not memories:
            scope = f" for {project}" if project else ""
            return f"No memories stored{scope}. Use /remember <text> to store memories."

        scope = f" [{project}]" if project else " [all projects]"
        lines = [f"Stored memories{scope} ({len(memories)}):\n"]
        for i, m in enumerate(memories, 1):
            date = m.created_at.strftime("%Y-%m-%d")
            preview = m.memory_text[:60]
            if len(m.memory_text) > 60:
                preview += "..."
            # Show project tag if viewing globally
            proj_tag = f" [{m.project_name}]" if m.project_name and not project else ""
            lines.append(f"{i}. [{date}]{proj_tag} {preview}")

        return "\n".join(lines)

    async def handle_preferences(self, phone: str, args: str) -> str:
        """/preferences - List learned preferences."""
        prefs = await self.memory.get_preferences(phone)

        if not prefs:
            return "No preferences stored yet."

        lines = ["Your preferences:\n"]
        by_category: dict[str, list] = {}

        for p in prefs:
            if p.category not in by_category:
                by_category[p.category] = []
            by_category[p.category].append(p)

        for category, items in sorted(by_category.items()):
            lines.append(f"\n{category.upper()}:")
            for p in items:
                lines.append(f"  - {p.key}: {p.value}")

        return "\n".join(lines)
