"""Tests for TaskManager background task lifecycle."""

from unittest.mock import AsyncMock, MagicMock

from nightwire.task_manager import TaskManager, log_task_exception


def _make_task_manager(**overrides):
    """Create a TaskManager with mocked dependencies."""
    defaults = {
        "runner": MagicMock(),
        "project_manager": MagicMock(),
        "memory": MagicMock(),
        "config": MagicMock(),
        "send_message": AsyncMock(),
        "send_typing_indicator": AsyncMock(),
        "get_memory_context": AsyncMock(return_value=None),
    }
    defaults.update(overrides)
    return TaskManager(**defaults)


class TestCheckBusy:
    def test_not_busy_when_no_tasks(self):
        tm = _make_task_manager()
        assert tm.check_busy("+1234567890") is None

    def test_not_busy_when_task_done(self):
        tm = _make_task_manager()
        mock_task = MagicMock()
        mock_task.done.return_value = True
        tm._sender_tasks[("+1234567890", "")] = {"task": mock_task, "description": "test"}
        assert tm.check_busy("+1234567890") is None

    def test_busy_when_task_running(self):
        tm = _make_task_manager()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        tm._sender_tasks[("+1234567890", "")] = {
            "task": mock_task,
            "description": "doing something",
            "start": None,
        }
        result = tm.check_busy("+1234567890")
        assert result is not None
        assert "Task in progress" in result
        assert "doing something" in result


class TestCancelCurrentTask:
    async def test_cancel_no_task(self):
        tm = _make_task_manager()
        result = await tm.cancel_current_task("+1234567890")
        assert result == "No task is currently running."

    async def test_cancel_done_task(self):
        tm = _make_task_manager()
        mock_task = MagicMock()
        mock_task.done.return_value = True
        tm._sender_tasks[("+1234567890", "")] = {"task": mock_task}
        result = await tm.cancel_current_task("+1234567890")
        assert result == "No task is currently running."

    async def test_cancel_running_task(self):
        tm = _make_task_manager()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        tm._sender_tasks[("+1234567890", "")] = {
            "task": mock_task,
            "description": "building feature",
            "start": None,
        }
        tm.runner.cancel = AsyncMock()
        result = await tm.cancel_current_task("+1234567890")
        assert "Cancelled" in result
        assert "building feature" in result
        mock_task.cancel.assert_called_once()
        tm.runner.cancel.assert_called_once()


class TestGetTaskState:
    def test_returns_none_for_unknown_sender(self):
        tm = _make_task_manager()
        assert tm.get_task_state("+1234567890") is None

    def test_returns_state_for_known_sender(self):
        tm = _make_task_manager()
        state = {"description": "test", "task": MagicMock()}
        tm._sender_tasks[("+1234567890", "")] = state
        assert tm.get_task_state("+1234567890") is state


class TestLogTaskException:
    def test_cancelled_task_no_error(self):
        mock_task = MagicMock()
        mock_task.cancelled.return_value = True
        log_task_exception(mock_task)  # Should not raise

    def test_successful_task_no_error(self):
        mock_task = MagicMock()
        mock_task.cancelled.return_value = False
        mock_task.exception.return_value = None
        log_task_exception(mock_task)  # Should not raise

    def test_failed_task_logs_error(self, caplog):
        mock_task = MagicMock()
        mock_task.cancelled.return_value = False
        mock_task.exception.return_value = ValueError("test error")
        log_task_exception(mock_task)
