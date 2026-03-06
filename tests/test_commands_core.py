"""Tests for CoreCommandHandler."""

from unittest.mock import AsyncMock, MagicMock

from nightwire.commands.base import BotContext
from nightwire.commands.core import CoreCommandHandler, get_memory_context


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


def _make_handler(**ctx_overrides):
    """Create a CoreCommandHandler with mocked BotContext."""
    ctx = _make_context(**ctx_overrides)
    return CoreCommandHandler(ctx), ctx


class TestGetCommands:
    def test_returns_all_core_commands(self):
        handler, _ = _make_handler()
        commands = handler.get_commands()
        expected = {
            "help", "projects", "select", "status", "add", "remove", "new",
            "ask", "do", "complex", "cancel", "summary",
            "cooldown", "update", "nightwire", "sidechannel", "global",
            "diagnose", "usage", "monitor", "worker",
        }
        assert set(commands.keys()) == expected

    def test_nightwire_and_sidechannel_same_handler(self):
        handler, _ = _make_handler()
        commands = handler.get_commands()
        # Both map to the same underlying method (handle_nightwire)
        assert commands["nightwire"].__func__ is commands["sidechannel"].__func__


class TestProjectCommands:
    async def test_handle_projects(self):
        handler, ctx = _make_handler()
        ctx.project_manager.list_projects.return_value = "project list"
        result = await handler.handle_projects("+1234567890", "")
        assert result == "project list"

    async def test_handle_select_no_args(self):
        handler, _ = _make_handler()
        result = await handler.handle_select("+1234567890", "")
        assert "Usage" in result

    async def test_handle_select_success(self):
        handler, ctx = _make_handler()
        ctx.project_manager.select_project.return_value = (True, "Selected: myapp")
        ctx.project_manager.get_current_path.return_value = "/projects/myapp"
        result = await handler.handle_select("+1234567890", "myapp")
        assert "Selected" in result
        ctx.runner.set_project.assert_called_once()

    async def test_handle_add_no_args(self):
        handler, _ = _make_handler()
        result = await handler.handle_add("+1234567890", "")
        assert "Usage" in result

    async def test_handle_remove_no_args(self):
        handler, _ = _make_handler()
        result = await handler.handle_remove("+1234567890", "")
        assert "Usage" in result

    async def test_handle_new_no_args(self):
        handler, _ = _make_handler()
        result = await handler.handle_new("+1234567890", "")
        assert "Usage" in result


class TestClaudeTaskCommands:
    async def test_handle_ask_no_args(self):
        handler, _ = _make_handler()
        result = await handler.handle_ask("+1234567890", "")
        assert "Usage" in result

    async def test_handle_ask_cooldown_active(self):
        handler, ctx = _make_handler()
        mock_cm = MagicMock()
        mock_cm.is_active = True
        mock_cm.get_state.return_value = MagicMock(user_message="Rate limited")
        ctx._cooldown_manager = mock_cm
        result = await handler.handle_ask("+1234567890", "question")
        assert "Rate limited" in result

    async def test_handle_ask_no_project(self):
        handler, ctx = _make_handler()
        ctx.project_manager.get_current_project.return_value = None
        result = await handler.handle_ask("+1234567890", "question")
        assert "No project selected" in result

    async def test_handle_ask_busy(self):
        handler, ctx = _make_handler()
        ctx.project_manager.get_current_project.return_value = "myapp"
        ctx.task_manager.check_busy.return_value = "Task in progress"
        result = await handler.handle_ask("+1234567890", "question")
        assert "Task in progress" in result

    async def test_handle_ask_starts_task(self):
        handler, ctx = _make_handler()
        ctx.project_manager.get_current_project.return_value = "myapp"
        ctx.task_manager.check_busy.return_value = None
        result = await handler.handle_ask("+1234567890", "what does this do?")
        assert result is None  # Background task
        ctx.task_manager.start_background_task.assert_called_once()

    async def test_handle_do_no_args(self):
        handler, _ = _make_handler()
        result = await handler.handle_do("+1234567890", "")
        assert "Usage" in result

    async def test_handle_do_starts_task(self):
        handler, ctx = _make_handler()
        ctx.project_manager.get_current_project.return_value = "myapp"
        ctx.task_manager.check_busy.return_value = None
        result = await handler.handle_do("+1234567890", "fix the bug")
        assert result is None
        ctx.task_manager.start_background_task.assert_called_once()

    async def test_handle_complex_no_args(self):
        handler, _ = _make_handler()
        result = await handler.handle_complex("+1234567890", "")
        assert "Usage" in result

    async def test_handle_cancel(self):
        handler, ctx = _make_handler()
        ctx.task_manager.cancel_current_task = AsyncMock(return_value="Cancelled")
        result = await handler.handle_cancel("+1234567890", "")
        assert result == "Cancelled"

    async def test_handle_summary_no_project(self):
        handler, ctx = _make_handler()
        ctx.project_manager.get_current_project.return_value = None
        result = await handler.handle_summary("+1234567890", "")
        assert "No project selected" in result


class TestCooldownCommand:
    async def test_cooldown_not_initialized(self):
        handler, ctx = _make_handler()
        result = await handler.handle_cooldown("+1234567890", "")
        assert "not initialized" in result

    async def test_cooldown_status_inactive(self):
        handler, ctx = _make_handler()
        mock_cm = MagicMock()
        mock_cm.get_state.return_value = MagicMock(active=False)
        ctx._cooldown_manager = mock_cm
        result = await handler.handle_cooldown("+1234567890", "status")
        assert "No active cooldown" in result

    async def test_cooldown_clear(self):
        handler, ctx = _make_handler()
        mock_cm = MagicMock()
        mock_cm.is_active = True
        ctx._cooldown_manager = mock_cm
        result = await handler.handle_cooldown("+1234567890", "clear")
        assert "Cooldown cleared" in result
        mock_cm.deactivate.assert_called_once()

    async def test_cooldown_unknown_subcommand(self):
        handler, ctx = _make_handler()
        ctx._cooldown_manager = MagicMock()
        result = await handler.handle_cooldown("+1234567890", "invalid")
        assert "Usage" in result


class TestNightwireCommands:
    async def test_nightwire_not_enabled(self):
        handler, ctx = _make_handler()
        ctx.nightwire_runner = None
        result = await handler.handle_nightwire("+1234567890", "hello")
        assert "not enabled" in result

    async def test_nightwire_no_args(self):
        handler, ctx = _make_handler()
        ctx.nightwire_runner = MagicMock()
        result = await handler.handle_nightwire("+1234567890", "")
        assert "Usage" in result

    def test_is_nightwire_query_colon(self):
        handler, ctx = _make_handler()
        ctx.nightwire_runner = MagicMock()
        assert handler._is_nightwire_query("nightwire: hello") is True

    def test_is_nightwire_query_space(self):
        handler, ctx = _make_handler()
        ctx.nightwire_runner = MagicMock()
        assert handler._is_nightwire_query("nightwire hello") is True

    def test_is_nightwire_query_disabled(self):
        handler, ctx = _make_handler()
        ctx.nightwire_runner = None
        assert handler._is_nightwire_query("nightwire: hello") is False

    def test_is_nightwire_query_exact_match(self):
        handler, ctx = _make_handler()
        ctx.nightwire_runner = MagicMock()
        assert handler._is_nightwire_query("nightwire") is True

    def test_is_nightwire_query_sidechannel(self):
        handler, ctx = _make_handler()
        ctx.nightwire_runner = MagicMock()
        assert handler._is_nightwire_query("sidechannel: test") is True


class TestGlobalCommand:
    async def test_global_no_args(self):
        handler, _ = _make_handler()
        result = await handler.handle_global("+1234567890", "")
        assert "Usage" in result

    async def test_global_remember(self):
        handler, ctx = _make_handler()
        ctx.memory_commands.handle_remember = AsyncMock(return_value="Stored")
        result = await handler.handle_global("+1234567890", "remember test note")
        assert result == "Stored"
        ctx.memory_commands.handle_remember.assert_called_once_with(
            "+1234567890", "test note", project=None
        )

    async def test_global_unknown_subcommand(self):
        handler, _ = _make_handler()
        result = await handler.handle_global("+1234567890", "invalid")
        assert "Unknown global command" in result


class TestUpdateCommand:
    async def test_update_non_admin(self):
        handler, ctx = _make_handler()
        ctx.config.allowed_numbers = ["+0000000000"]
        result = await handler.handle_update("+1234567890", "")
        assert "Only the admin" in result

    async def test_update_not_enabled(self):
        handler, ctx = _make_handler()
        ctx.config.allowed_numbers = ["+1234567890"]
        result = await handler.handle_update("+1234567890", "")
        assert "not enabled" in result


class TestHelpCommand:
    async def test_help_returns_text(self):
        handler, ctx = _make_handler()
        ctx.plugin_loader.get_all_help.return_value = []
        result = await handler.handle_help("+1234567890", "")
        assert "nightwire Commands" in result
        assert "/projects" in result
        assert "/do" in result

    async def test_help_includes_nightwire_when_enabled(self):
        handler, ctx = _make_handler()
        ctx.nightwire_runner = MagicMock()
        ctx.plugin_loader.get_all_help.return_value = []
        result = await handler.handle_help("+1234567890", "")
        assert "AI Assistant" in result


class TestGetMemoryContext:
    async def test_returns_context(self):
        mock_memory = MagicMock()
        mock_memory.get_relevant_context = AsyncMock(return_value="context data")
        mock_config = MagicMock()
        mock_config.memory_max_context_tokens = 1500
        mock_pm = MagicMock()
        mock_pm.get_current_project.return_value = "myapp"

        result = await get_memory_context(
            mock_memory, mock_config, mock_pm,
            "+1234567890", "test query",
        )
        assert result == "context data"

    async def test_returns_none_on_error(self):
        mock_memory = MagicMock()
        mock_memory.get_relevant_context = AsyncMock(side_effect=Exception("db error"))
        mock_config = MagicMock()
        mock_config.memory_max_context_tokens = 1500
        mock_pm = MagicMock()

        result = await get_memory_context(
            mock_memory, mock_config, mock_pm,
            "+1234567890", "test query",
        )
        assert result is None

    async def test_returns_none_for_empty_context(self):
        mock_memory = MagicMock()
        mock_memory.get_relevant_context = AsyncMock(return_value="")
        mock_config = MagicMock()
        mock_config.memory_max_context_tokens = 1500
        mock_pm = MagicMock()

        result = await get_memory_context(
            mock_memory, mock_config, mock_pm,
            "+1234567890", "test query",
        )
        assert result is None
