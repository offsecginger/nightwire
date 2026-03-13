"""Tests for v3.0.7 production fixes.

Fix 1: Already-done detection — tasks completed by sibling parallel tasks
       succeed instead of failing with "files changed: 0"
Fix 2: Purge hint after /tasks purge + clearer stats line with failed count
"""

import sqlite3
import threading
from unittest.mock import AsyncMock, MagicMock, patch

from nightwire.autonomous.commands import AutonomousCommands
from nightwire.autonomous.database import AutonomousDatabase
from nightwire.autonomous.executor import _ALREADY_DONE_PATTERNS
from nightwire.autonomous.models import TaskStatus


# =====================================================================
# Helpers
# =====================================================================


def _make_db():
    """Create an AutonomousDatabase with in-memory SQLite."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    db = AutonomousDatabase.__new__(AutonomousDatabase)
    db._conn = conn
    db._lock = threading.Lock()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS prds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT, project_name TEXT, title TEXT,
            description TEXT, status TEXT DEFAULT 'draft',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prd_id INTEGER, phone_number TEXT, title TEXT,
            description TEXT, status TEXT DEFAULT 'pending',
            acceptance_criteria TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER, phone_number TEXT, project_name TEXT,
            title TEXT, description TEXT,
            status TEXT DEFAULT 'pending',
            task_type TEXT DEFAULT 'feature',
            effort_level TEXT DEFAULT 'medium',
            depends_on TEXT DEFAULT '[]',
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 2,
            error_message TEXT, claude_output TEXT,
            files_changed TEXT, quality_gate_results TEXT,
            started_at TEXT, completed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    return db, conn


def _make_commands(manager=None, get_current_project=None):
    if manager is None:
        manager = AsyncMock()
    if get_current_project is None:
        get_current_project = lambda phone: ("TestProject", "/tmp/test")
    return AutonomousCommands(
        manager=manager,
        get_current_project=get_current_project,
    )


# =====================================================================
# Fix 1: Already-done detection
# =====================================================================


class TestAlreadyDonePatterns:
    """Verify _ALREADY_DONE_PATTERNS constant is defined and usable."""

    def test_patterns_exist(self):
        assert len(_ALREADY_DONE_PATTERNS) >= 10

    def test_key_patterns_present(self):
        assert "already implemented" in _ALREADY_DONE_PATTERNS
        assert "already complete" in _ALREADY_DONE_PATTERNS
        assert "nothing to do" in _ALREADY_DONE_PATTERNS
        assert "no changes required" in _ALREADY_DONE_PATTERNS

    def test_pattern_matching(self):
        """Patterns match typical Claude outputs for already-done tasks."""
        test_outputs = [
            "The endpoint was already implemented in a previous task.",
            "All 474 tests pass. This is already complete.",
            "The code already exists and is working correctly.",
            "Nothing to do — the files are already in place.",
            "No changes required, everything was done by task #2.",
        ]
        for output in test_outputs:
            output_lower = output.lower()
            assert any(
                p in output_lower for p in _ALREADY_DONE_PATTERNS
            ), f"No pattern matched: {output}"

    def test_normal_output_no_match(self):
        """Normal completion output does NOT match already-done patterns."""
        normal_outputs = [
            "Created 5 new files. All tests pass.",
            "Implementation complete. Modified routes/endpoints.py",
            "Done. Here's what was created:",
        ]
        for output in normal_outputs:
            output_lower = output.lower()
            assert not any(
                p in output_lower for p in _ALREADY_DONE_PATTERNS
            ), f"False positive match: {output}"


class TestAlreadyDoneExecutor:
    """Verify executor treats already-done tasks as success."""

    async def test_already_done_returns_success(self):
        """When Claude reports work already done, task succeeds."""
        from nightwire.autonomous.executor import TaskExecutor

        executor = TaskExecutor.__new__(TaskExecutor)
        executor.config = MagicMock()
        executor.config.claude_timeout = 30
        executor.config.claude_max_turns_execution = None
        executor.config.autonomous_verification = False
        executor.config.get_project_path = MagicMock(return_value=None)
        executor.config.projects_base_path = MagicMock()
        executor.config.autonomous_effort_levels = {}
        executor.run_quality_gates = False
        executor.run_verification = False
        executor.learning_extractor = MagicMock()
        executor.learning_extractor.extract_with_claude = AsyncMock(
            return_value=[]
        )
        executor.db = AsyncMock()
        executor.db.get_story = AsyncMock(return_value=None)
        executor.db.get_relevant_learnings = AsyncMock(return_value=[])
        executor.db.list_tasks = AsyncMock(return_value=[])

        task = MagicMock()
        task.id = 1
        task.title = "Endpoint list route"
        task.description = "Create endpoint list route"
        task.task_type = None
        task.effort_level = None
        task.story_id = 1
        task.phone_number = "+1234"
        task.project_name = "TestProject"

        mock_runner = MagicMock()
        mock_runner.run_claude = AsyncMock(
            return_value=(
                True,
                "All 474 tests pass. The endpoint route was already implemented.",
            )
        )
        mock_runner.last_usage = None
        mock_runner.close = AsyncMock()
        mock_runner.set_project = MagicMock()

        async def mock_get_files(*args, **kwargs):
            return []

        async def mock_checkpoint(*args, **kwargs):
            return False

        async def mock_head(*args, **kwargs):
            return "abc123"

        with patch.object(executor, "_get_files_changed", mock_get_files):
            with patch.object(
                executor, "_git_save_checkpoint", mock_checkpoint
            ):
                with patch.object(executor, "_get_head_hash", mock_head):
                    with patch(
                        "nightwire.autonomous.executor.ClaudeRunner",
                        return_value=mock_runner,
                    ):
                        result = await executor.execute(task)

        assert result.success is True
        assert result.files_changed == []


# =====================================================================
# Fix 2: Purge hint + stats clarity
# =====================================================================


class TestPurgeHint:
    """Verify /tasks purge shows hint about purge failed/all."""

    async def test_purge_default_includes_hint(self):
        """Default purge response includes tip about other purge modes."""
        manager = AsyncMock()
        manager.purge_non_terminal_tasks = AsyncMock(return_value=3)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "purge")
        assert "3 task(s)" in result
        assert "purge failed" in result.lower()
        assert "purge all" in result.lower()

    async def test_purge_failed_no_extra_hint(self):
        """/tasks purge failed does NOT show the hint (user already knows)."""
        manager = AsyncMock()
        manager.purge_failed_tasks = AsyncMock(return_value=2)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "purge failed")
        assert "2 failed task(s)" in result
        assert "Tip" not in result

    async def test_purge_empty_no_hint(self):
        """When nothing to purge, no hint shown."""
        manager = AsyncMock()
        manager.purge_non_terminal_tasks = AsyncMock(return_value=0)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "purge")
        assert "No pending" in result
        assert "Tip" not in result


class TestStatsClarity:
    """Verify /tasks stats line includes failed total when > 0."""

    async def test_stats_shows_failed_total(self):
        """Stats line includes (N failed) when failed tasks exist."""
        manager = AsyncMock()
        task_mock = MagicMock()
        task_mock.status = MagicMock()
        task_mock.status.value = "failed"
        task_mock.id = 1
        task_mock.title = "Test task"
        manager.list_tasks = AsyncMock(return_value=[task_mock])
        manager.get_task_stats = AsyncMock(return_value={
            "total": 14,
            "failed": 1,
            "completed": 10,
            "queued": 0,
            "pending": 0,
            "in_progress": 0,
            "blocked": 0,
            "cancelled": 3,
            "running_tests": 0,
            "verifying": 0,
            "completed_today": 0,
            "failed_today": 0,
        })
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "")
        assert "Total: 14 (1 failed)" in result

    async def test_stats_no_failed_parenthetical(self):
        """Stats line omits (N failed) when no failed tasks."""
        manager = AsyncMock()
        task_mock = MagicMock()
        task_mock.status = MagicMock()
        task_mock.status.value = "completed"
        task_mock.id = 1
        task_mock.title = "Done task"
        manager.list_tasks = AsyncMock(return_value=[task_mock])
        manager.get_task_stats = AsyncMock(return_value={
            "total": 5,
            "failed": 0,
            "completed": 5,
            "queued": 0,
            "pending": 0,
            "in_progress": 0,
            "blocked": 0,
            "cancelled": 0,
            "running_tests": 0,
            "verifying": 0,
            "completed_today": 2,
            "failed_today": 0,
        })
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "")
        assert "Total: 5 |" in result
        assert "(0 failed)" not in result
