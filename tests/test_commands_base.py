"""Tests for command handler base infrastructure."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nightwire.commands.base import (
    BUILTIN_COMMANDS,
    BaseCommandHandler,
    BotContext,
    HandlerRegistry,
)


def _make_context(**overrides):
    """Create a BotContext with all-mocked dependencies."""
    ctx = BotContext(
        config=MagicMock(),
        runner=MagicMock(),
        project_manager=MagicMock(),
        memory=MagicMock(),
        memory_commands=MagicMock(),
        plugin_loader=MagicMock(),
        send_message=AsyncMock(),
        send_typing_indicator=AsyncMock(),
        task_manager=MagicMock(),
        get_memory_context=AsyncMock(),
    )
    for key, val in overrides.items():
        setattr(ctx, key, val)
    return ctx


# --- BotContext guard properties ---


class TestBotContextGuards:
    def test_autonomous_manager_raises_before_start(self):
        ctx = _make_context()
        with pytest.raises(RuntimeError, match="Bot not started"):
            _ = ctx.autonomous_manager

    def test_autonomous_commands_raises_before_start(self):
        ctx = _make_context()
        with pytest.raises(RuntimeError, match="Bot not started"):
            _ = ctx.autonomous_commands

    def test_updater_raises_before_start(self):
        ctx = _make_context()
        with pytest.raises(RuntimeError, match="Bot not started"):
            _ = ctx.updater

    def test_cooldown_manager_raises_before_start(self):
        ctx = _make_context()
        with pytest.raises(RuntimeError, match="Bot not started"):
            _ = ctx.cooldown_manager

    def test_cooldown_active_safe_before_start(self):
        ctx = _make_context()
        assert ctx.cooldown_active is False

    def test_autonomous_manager_accessible_after_set(self):
        ctx = _make_context()
        mock_am = MagicMock()
        ctx._autonomous_manager = mock_am
        assert ctx.autonomous_manager is mock_am

    def test_cooldown_manager_accessible_after_set(self):
        ctx = _make_context()
        mock_cm = MagicMock()
        ctx._cooldown_manager = mock_cm
        assert ctx.cooldown_manager is mock_cm

    def test_cooldown_active_true_when_set(self):
        ctx = _make_context()
        mock_cm = MagicMock()
        mock_cm.is_active = True
        ctx._cooldown_manager = mock_cm
        assert ctx.cooldown_active is True

    def test_nightwire_runner_none_by_default(self):
        ctx = _make_context()
        assert ctx.nightwire_runner is None


# --- HandlerRegistry ---


class TestHandlerRegistry:
    def test_register_and_get(self):
        registry = HandlerRegistry()
        handler = AsyncMock(return_value="response")

        class TestHandler(BaseCommandHandler):
            def get_commands(self):
                return {"test": handler}

        ctx = _make_context()
        registry.register(TestHandler(ctx))
        assert registry.get("test") is handler

    def test_get_unknown_returns_none(self):
        registry = HandlerRegistry()
        assert registry.get("nonexistent") is None

    def test_register_external(self):
        registry = HandlerRegistry()
        handler = AsyncMock()
        registry.register_external({"ext_cmd": handler})
        assert registry.get("ext_cmd") is handler

    def test_command_names(self):
        registry = HandlerRegistry()
        registry.register_external({"cmd1": AsyncMock(), "cmd2": AsyncMock()})
        assert registry.command_names == frozenset({"cmd1", "cmd2"})

    def test_conflict_warning(self, caplog):
        registry = HandlerRegistry()
        registry.register_external({"dup": AsyncMock()})
        registry.register_external({"dup": AsyncMock()})
        # Second registration should warn but still override


# --- BUILTIN_COMMANDS ---


class TestBuiltinCommands:
    def test_contains_core_commands(self):
        for cmd in ["help", "projects", "select", "status", "do", "ask",
                     "complex", "cancel", "summary", "cooldown", "update"]:
            assert cmd in BUILTIN_COMMANDS

    def test_contains_memory_commands(self):
        for cmd in ["remember", "recall", "history", "forget", "memories",
                     "preferences", "global"]:
            assert cmd in BUILTIN_COMMANDS

    def test_contains_autonomous_commands(self):
        for cmd in ["prd", "story", "task", "tasks", "autonomous",
                     "queue", "learnings"]:
            assert cmd in BUILTIN_COMMANDS

    def test_contains_nightwire_commands(self):
        assert "nightwire" in BUILTIN_COMMANDS
        assert "sidechannel" in BUILTIN_COMMANDS

    def test_contains_previously_missing_commands(self):
        assert "remove" in BUILTIN_COMMANDS
        assert "add" in BUILTIN_COMMANDS
        assert "new" in BUILTIN_COMMANDS
