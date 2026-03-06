"""Tests for Milestone 14b: Deferred items (concurrent /do, graceful shutdown,
cancel reasons, command history, startup notification, WS diagnostics, etc.)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from nightwire.memory.context_builder import ContextBuilder

# ---------------------------------------------------------------------------
# 14.1.1: Concurrent /do per project (TaskManager composite keys)
# ---------------------------------------------------------------------------


def _make_task_manager():
    from nightwire.task_manager import TaskManager

    return TaskManager(
        runner=MagicMock(),
        project_manager=MagicMock(),
        memory=MagicMock(),
        config=MagicMock(),
        send_message=AsyncMock(),
        send_typing_indicator=AsyncMock(),
        get_memory_context=AsyncMock(),
    )


class TestConcurrentDoPerProject:
    """Verify _sender_tasks uses (sender, project) tuple keys."""

    def test_sender_tasks_uses_tuple_keys(self):
        tm = _make_task_manager()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        tm._sender_tasks[("+1234", "proj_a")] = {
            "task": mock_task, "description": "task A",
        }
        tm._sender_tasks[("+1234", "proj_b")] = {
            "task": mock_task, "description": "task B",
        }
        # Same sender, different projects — both stored
        assert len(tm._sender_tasks) == 2

    def test_check_busy_scoped_to_project(self):
        tm = _make_task_manager()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        tm._sender_tasks[("+1234", "proj_a")] = {
            "task": mock_task, "description": "busy",
            "start": None,
        }
        # Busy on proj_a
        assert tm.check_busy("+1234", "proj_a") is not None
        # Not busy on proj_b
        assert tm.check_busy("+1234", "proj_b") is None

    def test_get_task_state_scoped_to_project(self):
        tm = _make_task_manager()
        state = {"description": "test", "task": MagicMock()}
        tm._sender_tasks[("+1234", "proj_a")] = state
        assert tm.get_task_state("+1234", "proj_a") is state
        assert tm.get_task_state("+1234", "proj_b") is None

    def test_get_all_tasks_for_sender(self):
        tm = _make_task_manager()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        tm._sender_tasks[("+1234", "proj_a")] = {
            "task": mock_task, "description": "A",
        }
        tm._sender_tasks[("+1234", "proj_b")] = {
            "task": mock_task, "description": "B",
        }
        tm._sender_tasks[("+5678", "proj_c")] = {
            "task": mock_task, "description": "C",
        }
        result = tm.get_all_tasks_for_sender("+1234")
        assert len(result) == 2
        assert "proj_a" in result
        assert "proj_b" in result
        assert "proj_c" not in result


# ---------------------------------------------------------------------------
# 14.2.1: Cancel reasons
# ---------------------------------------------------------------------------


class TestCancelReasons:
    """Verify cancel_reason is stored and propagated."""

    async def test_cancel_sets_reason(self):
        tm = _make_task_manager()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        tm._sender_tasks[("+1234", "proj")] = {
            "task": mock_task,
            "description": "building",
            "start": None,
            "cancel_reason": None,
        }
        tm.runner.cancel = AsyncMock()
        await tm.cancel_current_task("+1234", "proj")
        state = tm._sender_tasks.get(("+1234", "proj"))
        if state:
            assert state["cancel_reason"] == "user cancel"

    async def test_cancel_all_sets_shutdown_reason(self):
        tm = _make_task_manager()

        # Create a real never-completing task so done() returns False
        async def never_finish():
            await asyncio.sleep(999)

        real_task = asyncio.create_task(never_finish())
        state = {
            "task": real_task,
            "description": "building",
            "cancel_reason": None,
        }
        tm._sender_tasks[("+1234", "proj")] = state
        await tm.cancel_all_tasks(reason="service restarting")
        # State was modified in-place before clear()
        assert state["cancel_reason"] == "service restarting"


# ---------------------------------------------------------------------------
# 14.2.1: Interrupted task persistence
# ---------------------------------------------------------------------------


class TestInterruptedTasks:
    """Verify interrupted task save/notify lifecycle."""

    def test_save_creates_json_file(self, tmp_path):
        tm = _make_task_manager()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        tm._sender_tasks[("+1234", "myproj")] = {
            "task": mock_task,
            "description": "big task",
            "step": "Running tests",
        }
        tm.save_interrupted_tasks(tmp_path)
        target = tmp_path / "interrupted_tasks.json"
        assert target.exists()
        data = json.loads(target.read_text())
        assert len(data) == 1
        assert data[0]["sender"] == "+1234"
        assert data[0]["project"] == "myproj"
        assert data[0]["description"] == "big task"

    def test_save_skips_when_no_active_tasks(self, tmp_path):
        tm = _make_task_manager()
        tm.save_interrupted_tasks(tmp_path)
        assert not (tmp_path / "interrupted_tasks.json").exists()

    async def test_notify_sends_messages_and_deletes_file(self, tmp_path):
        tm = _make_task_manager()
        data = [{"sender": "+1234", "project": "proj",
                 "description": "task", "step": "working"}]
        (tmp_path / "interrupted_tasks.json").write_text(json.dumps(data))

        await tm.notify_interrupted_tasks(tmp_path)

        tm._send_message.assert_called_once()
        args = tm._send_message.call_args
        assert "+1234" in args[0]
        assert "restarted" in args[0][1].lower()
        # File should be deleted after notification
        assert not (tmp_path / "interrupted_tasks.json").exists()

    async def test_notify_handles_corrupt_json(self, tmp_path):
        (tmp_path / "interrupted_tasks.json").write_text("{bad json")
        tm = _make_task_manager()
        # Should not raise
        await tm.notify_interrupted_tasks(tmp_path)
        # File should be cleaned up
        assert not (tmp_path / "interrupted_tasks.json").exists()


# ---------------------------------------------------------------------------
# 14.1.4: Graceful shutdown
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    """Verify shutdown grace period constant and method exist."""

    def test_grace_seconds_constant(self):
        from nightwire.bot import SignalBot
        assert hasattr(SignalBot, "SHUTDOWN_GRACE_SECONDS")
        assert SignalBot.SHUTDOWN_GRACE_SECONDS == 90

    def test_set_shutdown_callback_exists(self):
        from nightwire.bot import SignalBot
        assert hasattr(SignalBot, "set_shutdown_callback")


# ---------------------------------------------------------------------------
# 14.3.5: Message handling timeout
# ---------------------------------------------------------------------------


class TestMessageHandlingTimeout:
    """Verify _handle_signal_message has asyncio.wait_for timeout."""

    def test_timeout_in_handler(self):
        import inspect

        from nightwire.bot import SignalBot
        source = inspect.getsource(SignalBot._handle_signal_message)
        assert "wait_for" in source
        assert "timeout=120" in source


# ---------------------------------------------------------------------------
# 14.3.4: WebSocket diagnostic logging
# ---------------------------------------------------------------------------


class TestWsDiagnostics:
    """Verify WS frame counter and envelope logging."""

    def test_ws_frames_counter_exists(self):
        import inspect

        from nightwire.bot import SignalBot
        source = inspect.getsource(SignalBot.__init__)
        assert "_ws_frames_received" in source

    def test_envelope_type_logging(self):
        import inspect

        from nightwire.bot import SignalBot
        # The poll_messages or ws receive loop should log envelope types
        source = inspect.getsource(SignalBot)
        assert "ws_envelope" in source


# ---------------------------------------------------------------------------
# 14.2.4: Startup ready notification
# ---------------------------------------------------------------------------


class TestStartupNotification:
    """Verify startup notification flag and logic."""

    def test_startup_notified_flag_exists(self):
        import inspect

        from nightwire.bot import SignalBot
        source = inspect.getsource(SignalBot.__init__)
        assert "_startup_notified" in source

    def test_startup_notification_in_ws_loop(self):
        import inspect

        from nightwire.bot import SignalBot
        source = inspect.getsource(SignalBot.poll_messages)
        assert "_startup_notified" in source
        # The notification message contains "started" and "ready"
        assert "started" in source
        assert "ready" in source


# ---------------------------------------------------------------------------
# 14.2.2: /do command history
# ---------------------------------------------------------------------------


class TestCommandHistory:
    """Verify command history in context builder."""

    def test_build_context_accepts_command_history(self):
        builder = ContextBuilder(max_tokens=500)
        result = builder.build_context_section(
            command_history=[
                {"role": "user", "content": "/do fix the bug"},
                {"role": "assistant", "content": "I fixed the null check."},
            ],
        )
        assert "Recent /do History" in result
        assert "fix the bug" in result
        assert "null check" in result

    def test_command_history_strips_do_prefix(self):
        builder = ContextBuilder(max_tokens=500)
        result = builder.build_context_section(
            command_history=[
                {"role": "user", "content": "/do add logging"},
            ],
        )
        # Should strip "/do " prefix
        assert "/do add logging" not in result
        assert "add logging" in result

    def test_command_history_truncates_long_responses(self):
        builder = ContextBuilder(max_tokens=2000)
        result = builder.build_context_section(
            command_history=[
                {"role": "assistant", "content": "x" * 1000},
            ],
        )
        # Should be truncated to ~500 chars + "..."
        assert "..." in result

    def test_empty_history_no_section(self):
        builder = ContextBuilder(max_tokens=500)
        result = builder.build_context_section(command_history=[])
        assert result == ""

    def test_none_history_no_section(self):
        builder = ContextBuilder(max_tokens=500)
        result = builder.build_context_section(command_history=None)
        assert result == ""


# ---------------------------------------------------------------------------
# 14.2.5: Enhanced /diagnose — mode detection
# ---------------------------------------------------------------------------


class TestEnhancedDiagnose:
    """Verify Signal API mode detection in diagnostics."""

    def test_diagnose_checks_mode(self):
        import inspect

        from nightwire.diagnostics import check_signal_api
        source = inspect.getsource(check_signal_api)
        assert "mode" in source
        assert "json-rpc" in source
