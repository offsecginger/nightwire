"""Integration tests for command routing through the handler registry.

Tests the full _handle_command → registry → handler path without
constructing SignalBot. Uses real HandlerRegistry and CoreCommandHandler
with mocked BotContext dependencies.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nightwire.commands.base import BUILTIN_COMMANDS, BotContext, HandlerRegistry
from nightwire.commands.core import CoreCommandHandler


def _make_mock_context(**overrides):
    """Create a mock BotContext with sensible defaults."""
    ctx = MagicMock(spec=BotContext)
    ctx.config = MagicMock()
    ctx.config.nightwire_assistant_enabled = False
    ctx.runner = MagicMock()
    ctx.project_manager = MagicMock()
    ctx.memory = MagicMock()
    ctx.memory_commands = MagicMock()
    ctx.plugin_loader = MagicMock()
    ctx.plugin_loader.get_all_plugins.return_value = []
    ctx.plugin_loader.get_all_commands.return_value = {}
    ctx.send_message = AsyncMock()
    ctx.task_manager = MagicMock()
    ctx.task_manager.check_busy.return_value = None
    ctx.get_memory_context = AsyncMock(return_value=None)
    ctx.nightwire_runner = None
    # Deferred fields — set to avoid RuntimeError
    ctx._autonomous_manager = MagicMock()
    ctx._autonomous_commands = MagicMock()
    ctx._updater = MagicMock()
    ctx._cooldown_manager = MagicMock()
    ctx._cooldown_manager.is_active = False
    ctx.cooldown_active = False
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


@pytest.fixture
def registry_with_core():
    """Create a registry with CoreCommandHandler + mock external commands."""
    ctx = _make_mock_context()
    handler = CoreCommandHandler(ctx)
    registry = HandlerRegistry()
    registry.register(handler)

    # Register mock memory commands (mirrors bot.py _make_memory_commands)
    registry.register_external({
        "remember": AsyncMock(return_value="Memory stored."),
        "recall": AsyncMock(return_value="No memories found."),
        "history": AsyncMock(return_value="No history."),
        "forget": AsyncMock(return_value="Nothing to forget."),
        "memories": AsyncMock(return_value="No memories."),
        "preferences": AsyncMock(return_value="No preferences."),
    })

    # Register mock autonomous commands (mirrors bot.py start())
    registry.register_external({
        "prd": AsyncMock(return_value="PRD help text"),
        "story": AsyncMock(return_value="Story help text"),
        "task": AsyncMock(return_value="Task help text"),
        "tasks": AsyncMock(return_value="Tasks help text"),
        "autonomous": AsyncMock(return_value="Autonomous help text"),
        "queue": AsyncMock(return_value="Queue help text"),
        "learnings": AsyncMock(return_value="Learnings help text"),
    })

    return registry, handler, ctx


class TestCommandRouting:
    """Test command routing through the handler registry."""

    async def test_help_routes_to_core_handler(self, registry_with_core):
        """'/help' routes through registry to CoreCommandHandler."""
        registry, handler, ctx = registry_with_core
        fn = registry.get("help")
        assert fn is not None
        result = await fn("+1234567890", "")
        assert isinstance(result, str)
        assert "help" in result.lower() or "commands" in result.lower()

    async def test_projects_routes_to_core_handler(self, registry_with_core):
        """'/projects' routes through registry to CoreCommandHandler."""
        registry, handler, ctx = registry_with_core
        # list_projects(sender) returns a formatted string
        ctx.project_manager.list_projects.return_value = (
            "Projects:\n  * myproject -> /path/to/myproject"
        )
        ctx.project_manager.get_current_project.return_value = "myproject"
        fn = registry.get("projects")
        assert fn is not None
        result = await fn("+1234567890", "")
        assert isinstance(result, str)
        assert "myproject" in result

    async def test_unknown_command_returns_none(self, registry_with_core):
        """Unknown command returns None from registry.get()."""
        registry, _, _ = registry_with_core
        fn = registry.get("nonexistent_xyz")
        assert fn is None

    async def test_memory_command_routes_to_external(
        self, registry_with_core
    ):
        """'/remember' routes to externally registered memory handler."""
        registry, _, _ = registry_with_core
        fn = registry.get("remember")
        assert fn is not None
        result = await fn("+1234567890", "test memory")
        assert result == "Memory stored."

    async def test_autonomous_command_routes_to_external(
        self, registry_with_core
    ):
        """'/prd' routes to externally registered autonomous handler."""
        registry, _, _ = registry_with_core
        fn = registry.get("prd")
        assert fn is not None
        result = await fn("+1234567890", "")
        assert result == "PRD help text"

    async def test_command_aliases_nightwire_sidechannel(
        self, registry_with_core
    ):
        """'/nightwire' and '/sidechannel' both resolve to handlers."""
        registry, _, _ = registry_with_core
        nw_fn = registry.get("nightwire")
        sc_fn = registry.get("sidechannel")
        assert nw_fn is not None
        assert sc_fn is not None
        # Both should point to the same underlying method
        assert nw_fn.__func__ == sc_fn.__func__

    async def test_command_case_insensitivity(self, registry_with_core):
        """Commands are case-insensitive (lowered before lookup)."""
        registry, _, _ = registry_with_core
        # Registry stores lowercase keys; the caller (bot._handle_command)
        # lowercases the input. Verify keys are lowercase.
        for cmd in registry.command_names:
            assert cmd == cmd.lower(), f"Command '{cmd}' is not lowercase"

    async def test_register_external_coexists_with_abc(
        self, registry_with_core
    ):
        """External commands coexist with BaseCommandHandler commands."""
        registry, _, _ = registry_with_core
        # Core handler commands
        assert registry.get("help") is not None
        assert registry.get("do") is not None
        assert registry.get("cancel") is not None
        # External commands
        assert registry.get("remember") is not None
        assert registry.get("prd") is not None
        assert registry.get("queue") is not None

    async def test_all_builtin_commands_registered(self, registry_with_core):
        """All BUILTIN_COMMANDS have handlers in the registry."""
        registry, _, _ = registry_with_core
        missing = []
        for cmd in BUILTIN_COMMANDS:
            if registry.get(cmd) is None:
                missing.append(cmd)
        assert missing == [], (
            f"BUILTIN_COMMANDS not registered: {missing}"
        )


class TestRegistryEdgeCases:
    """Test registry behavior for edge cases."""

    async def test_empty_registry_returns_none(self):
        """Empty registry returns None for any command."""
        registry = HandlerRegistry()
        assert registry.get("help") is None
        assert registry.get("anything") is None

    async def test_register_external_overwrites_with_warning(self):
        """Registering a duplicate command logs a warning."""
        registry = HandlerRegistry()
        registry.register_external({"test": AsyncMock()})
        with patch("nightwire.commands.base.logger") as mock_logger:
            registry.register_external({"test": AsyncMock()})
            mock_logger.warning.assert_called_once()

    async def test_do_command_starts_background_task(
        self, registry_with_core
    ):
        """/do routes to handler that invokes task_manager."""
        registry, _, ctx = registry_with_core
        ctx.project_manager.get_current_project.return_value = "myproject"
        ctx.project_manager.get_current_path.return_value = "/path"
        fn = registry.get("do")
        assert fn is not None
        # /do with no project selected returns message
        ctx.project_manager.get_current_project.return_value = None
        result = await fn("+1234567890", "write code")
        assert "project" in result.lower() or "select" in result.lower()
