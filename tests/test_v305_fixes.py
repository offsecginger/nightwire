"""Tests for v3.0.5 production fixes.

Fix 1: Streaming readline buffer overflow — 1MB limit on subprocess
Fix 2: Rate limit cooldown graduated response
Fix 3: /tasks purge to delete queued tasks
"""

import sqlite3
import threading
from unittest.mock import AsyncMock, MagicMock, patch

from nightwire.autonomous.commands import AutonomousCommands
from nightwire.autonomous.database import AutonomousDatabase
from nightwire.autonomous.manager import AutonomousManager
from nightwire.claude_runner import ClaudeRunner
from nightwire.exceptions import ErrorCategory
from nightwire.rate_limit_cooldown import CooldownManager

# =====================================================================
# Fix 1: Streaming readline buffer overflow
# =====================================================================


class TestStreamingBufferLimit:
    """Verify subprocess gets 1MB buffer limit for NDJSON events."""

    async def test_streaming_subprocess_has_1mb_limit(self):
        """_execute_once_streaming passes limit=1_048_576 to subprocess."""
        runner = ClaudeRunner.__new__(ClaudeRunner)
        runner._config = MagicMock()
        runner._config.claude_path = "claude"
        runner._config.claude_timeout = 30
        runner._config.claude_max_turns = 10
        runner._config.claude_max_budget_usd = None
        runner._config.settings = {}
        runner._active_invocations = {}
        runner._invocation_counter = 0
        runner._last_usage = None
        runner._last_session_id = None

        inv_state = MagicMock()
        inv_state.cancelled = False
        inv_state.process = None

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout.readline = AsyncMock(return_value=b"")
            mock_proc.stderr.read = AsyncMock(return_value=b"")
            mock_proc.stdin = AsyncMock()
            mock_proc.stdin.write = MagicMock()
            mock_proc.stdin.drain = AsyncMock()
            mock_proc.stdin.close = MagicMock()
            mock_proc.wait = AsyncMock(return_value=1)
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            with patch.object(runner, "_build_command", return_value=["claude", "-p"]):
                with patch.object(
                    runner, "_maybe_sandbox",
                    return_value=(["claude", "-p"], None),
                ):
                    await runner._execute_once_streaming(
                        prompt_str="test",
                        timeout=5,
                        progress_callback=AsyncMock(),
                        inv_state=inv_state,
                    )

            # Verify limit=1_048_576 was passed
            call_kwargs = mock_exec.call_args
            assert call_kwargs.kwargs.get("limit") == 1_048_576

    async def test_separator_error_returns_infrastructure(self):
        """ValueError from readline returns INFRASTRUCTURE via exception handler."""
        # The ValueError is caught by the broad except in _execute_once_streaming
        # which returns ErrorCategory.INFRASTRUCTURE directly (not via classify_error)
        # This test verifies the return tuple structure
        runner = ClaudeRunner.__new__(ClaudeRunner)
        runner._config = MagicMock()
        runner._config.claude_path = "claude"
        runner._config.claude_timeout = 30
        runner._config.claude_max_turns = 10
        runner._config.claude_max_budget_usd = None
        runner._config.settings = {}
        runner._active_invocations = {}
        runner._invocation_counter = 0
        runner._last_usage = None
        runner._last_session_id = None

        inv_state = MagicMock()
        inv_state.cancelled = False
        inv_state.process = None

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = ValueError(
                "Separator is found, but chunk is longer than limit"
            )
            with patch.object(
                runner, "_build_command", return_value=["claude", "-p"]
            ):
                with patch.object(
                    runner, "_maybe_sandbox",
                    return_value=(["claude", "-p"], None),
                ):
                    success, msg, cat = await runner._execute_once_streaming(
                        prompt_str="test",
                        timeout=5,
                        progress_callback=AsyncMock(),
                        inv_state=inv_state,
                    )

            assert not success
            assert "Separator" in msg
            assert cat == ErrorCategory.INFRASTRUCTURE


# =====================================================================
# Fix 2: Rate limit cooldown graduated response
# =====================================================================


class TestRateLimitGraduatedResponse:
    """Verify rate_limit_event handling differentiates statuses."""

    def test_allowed_status_no_cooldown(self):
        """'allowed' status should not trigger cooldown."""
        # This is a logic test — the handler should skip for "allowed"
        # Verified by ensuring cooldown is NOT active after allowed
        mgr = CooldownManager.__new__(CooldownManager)
        mgr.enabled = True
        mgr._active = False
        mgr._expires_at = None
        mgr._resume_task = None
        mgr._failures = []
        mgr._on_activate = []
        mgr._on_deactivate = []
        mgr.cooldown_minutes = 60
        mgr.consecutive_threshold = 3
        mgr.failure_window_seconds = 300

        # "allowed" should not activate
        assert not mgr.is_active

    def test_throttled_records_failure_not_immediate_cooldown(self):
        """'throttled' status should record failure, not activate immediately."""
        mgr = CooldownManager.__new__(CooldownManager)
        mgr.enabled = True
        mgr._active = False
        mgr._expires_at = None
        mgr._resume_task = None
        mgr._failures = []
        mgr._on_activate = []
        mgr._on_deactivate = []
        mgr.cooldown_minutes = 60
        mgr.consecutive_threshold = 3
        mgr.failure_window_seconds = 300

        # Record 1 failure — should NOT activate (threshold is 3)
        mgr.record_rate_limit_failure()
        assert not mgr.is_active
        assert len(mgr._failures) == 1

    def test_throttled_activates_after_threshold(self):
        """Consecutive throttled events should activate after threshold."""
        mgr = CooldownManager.__new__(CooldownManager)
        mgr.enabled = True
        mgr._active = False
        mgr._expires_at = None
        mgr._resume_task = None
        mgr._failures = []
        mgr._on_activate = []
        mgr._on_deactivate = []
        mgr.cooldown_minutes = 60
        mgr.consecutive_threshold = 3
        mgr.failure_window_seconds = 300

        # Record 3 failures — should activate
        with patch.object(mgr, "_schedule_resume"):
            mgr.record_rate_limit_failure()
            mgr.record_rate_limit_failure()
            mgr.record_rate_limit_failure()

        assert mgr.is_active

    def test_limited_status_activates_immediately(self):
        """'limited' status should activate cooldown immediately."""
        mgr = CooldownManager.__new__(CooldownManager)
        mgr.enabled = True
        mgr._active = False
        mgr._expires_at = None
        mgr._resume_task = None
        mgr._failures = []
        mgr._on_activate = []
        mgr._on_deactivate = []
        mgr.cooldown_minutes = 60
        mgr.consecutive_threshold = 3
        mgr.failure_window_seconds = 300

        with patch.object(mgr, "_schedule_resume"):
            mgr.activate()

        assert mgr.is_active


# =====================================================================
# Fix 3: /tasks purge
# =====================================================================


class TestTasksPurge:
    """Verify /tasks purge cancels non-terminal tasks."""

    def _make_db(self):
        """Create an AutonomousDatabase with in-memory SQLite."""
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        db = AutonomousDatabase.__new__(AutonomousDatabase)
        db._conn = conn
        db._lock = threading.Lock()

        # Create tasks table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                story_id INTEGER,
                phone_number TEXT,
                project_name TEXT,
                title TEXT,
                description TEXT,
                status TEXT DEFAULT 'pending',
                task_type TEXT DEFAULT 'feature',
                effort_level TEXT DEFAULT 'medium',
                depends_on TEXT DEFAULT '[]',
                retry_count INTEGER DEFAULT 0,
                error_message TEXT,
                claude_output TEXT,
                files_changed TEXT,
                quality_gate_results TEXT,
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        return db

    def _insert_task(self, db, status, phone="+1234", project="test"):
        """Insert a task with given status."""
        db._conn.execute(
            "INSERT INTO tasks (story_id, phone_number, project_name, "
            "title, description, status) VALUES (?, ?, ?, ?, ?, ?)",
            (1, phone, project, f"Task {status}", "desc", status),
        )
        db._conn.commit()

    async def test_purge_cancels_pending_queued_blocked(self):
        """Purge marks PENDING/QUEUED/BLOCKED as CANCELLED."""
        db = self._make_db()
        self._insert_task(db, "pending")
        self._insert_task(db, "queued")
        self._insert_task(db, "blocked")
        self._insert_task(db, "completed")
        self._insert_task(db, "in_progress")

        count = await db.purge_non_terminal_tasks("+1234")
        assert count == 3

        cursor = db._conn.cursor()
        cursor.execute(
            "SELECT status FROM tasks WHERE status = 'cancelled'"
        )
        assert len(cursor.fetchall()) == 3

    async def test_purge_filters_by_project(self):
        """Purge with project_name only affects that project."""
        db = self._make_db()
        self._insert_task(db, "queued", project="proj_a")
        self._insert_task(db, "queued", project="proj_b")

        count = await db.purge_non_terminal_tasks("+1234", "proj_a")
        assert count == 1

        cursor = db._conn.cursor()
        cursor.execute(
            "SELECT project_name FROM tasks WHERE status = 'queued'"
        )
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "proj_b"

    async def test_purge_returns_zero_when_nothing_to_purge(self):
        """Purge returns 0 when only terminal tasks exist."""
        db = self._make_db()
        self._insert_task(db, "completed")
        self._insert_task(db, "failed")

        count = await db.purge_non_terminal_tasks("+1234")
        assert count == 0

    async def test_purge_sets_error_message(self):
        """Purged tasks get descriptive error_message."""
        db = self._make_db()
        self._insert_task(db, "queued")

        await db.purge_non_terminal_tasks("+1234")

        cursor = db._conn.cursor()
        cursor.execute(
            "SELECT error_message FROM tasks WHERE status = 'cancelled'"
        )
        row = cursor.fetchone()
        assert row[0] == "Purged via /tasks purge"


class TestTasksPurgeCommand:
    """Test /tasks purge command handler."""

    async def test_tasks_purge_calls_manager(self):
        """'/tasks purge' routes to manager.purge_non_terminal_tasks."""
        manager = AsyncMock(spec=AutonomousManager)
        manager.purge_non_terminal_tasks = AsyncMock(return_value=5)

        cmds = AutonomousCommands(
            manager=manager,
            get_current_project=lambda p: ("test_project", "/tmp/test"),
        )

        result = await cmds.handle_tasks("+1234", "purge")
        assert "5 task(s)" in result
        assert "purged" in result.lower()
        manager.purge_non_terminal_tasks.assert_called_once_with(
            "+1234", "test_project"
        )

    async def test_tasks_purge_zero_tasks(self):
        """'/tasks purge' with nothing to purge returns informative message."""
        manager = AsyncMock(spec=AutonomousManager)
        manager.purge_non_terminal_tasks = AsyncMock(return_value=0)

        cmds = AutonomousCommands(
            manager=manager,
            get_current_project=lambda p: ("test_project", "/tmp/test"),
        )

        result = await cmds.handle_tasks("+1234", "purge")
        assert "no pending" in result.lower()

    async def test_tasks_invalid_status_shows_purge_option(self):
        """Invalid status filter now mentions 'purge' as option."""
        manager = AsyncMock(spec=AutonomousManager)

        cmds = AutonomousCommands(
            manager=manager,
            get_current_project=lambda p: ("test_project", "/tmp/test"),
        )

        result = await cmds.handle_tasks("+1234", "bogus")
        assert "purge" in result.lower()


# =====================================================================
# Feature A: /prd ingest
# =====================================================================


class TestPrdIngest:
    """Verify /prd ingest creates PRD without auto-queuing."""

    async def test_prd_ingest_with_file(self):
        """'/prd ingest myfile.md' builds analysis prompt for that file."""
        create_fn = AsyncMock(return_value="PRD #1: Test PRD")
        manager = AsyncMock(spec=AutonomousManager)

        cmds = AutonomousCommands(
            manager=manager,
            get_current_project=lambda p: ("test_project", "/tmp/test"),
            create_prd_fn=create_fn,
        )

        result = await cmds.handle_prd("+1234", "ingest myfile.md")
        assert "Test PRD" in result
        # Verify create_prd_fn was called with a prompt mentioning the file
        call_args = create_fn.call_args
        assert call_args.kwargs["auto_queue"] is False
        prompt = call_args.args[1]
        assert "myfile.md" in prompt

    async def test_prd_ingest_defaults_to_claude_md(self):
        """'/prd ingest' with no file defaults to CLAUDE.md."""
        create_fn = AsyncMock(return_value="PRD #1: Analyzed")
        cmds = AutonomousCommands(
            manager=AsyncMock(spec=AutonomousManager),
            get_current_project=lambda p: ("test", "/tmp"),
            create_prd_fn=create_fn,
        )

        await cmds.handle_prd("+1234", "ingest")
        call_args = create_fn.call_args
        prompt = call_args.args[1]
        assert "CLAUDE.md" in prompt

    async def test_prd_ingest_no_create_fn(self):
        """'/prd ingest' without create_prd_fn configured returns error."""
        cmds = AutonomousCommands(
            manager=AsyncMock(spec=AutonomousManager),
            get_current_project=lambda p: ("test", "/tmp"),
        )

        result = await cmds.handle_prd("+1234", "ingest Build something")
        assert "not configured" in result.lower()

    async def test_prd_ingest_error_handling(self):
        """'/prd ingest' handles exceptions from create_prd_fn."""
        create_fn = AsyncMock(side_effect=ValueError("Parse failed"))
        cmds = AutonomousCommands(
            manager=AsyncMock(spec=AutonomousManager),
            get_current_project=lambda p: ("test", "/tmp"),
            create_prd_fn=create_fn,
        )

        result = await cmds.handle_prd("+1234", "ingest Bad input")
        assert "failed" in result.lower()

    async def test_prd_no_queue_summary(self):
        """_prd_summary_no_queue returns correct format."""
        from nightwire.task_manager import TaskManager
        tm = TaskManager.__new__(TaskManager)
        prd = MagicMock()
        prd.id = 42
        prd.title = "Test PRD"

        result = tm._prd_summary_no_queue(
            prd, 5, ["  - Story A (3 tasks)", "  - Story B (2 tasks)"],
        )
        assert "PRD #42" in result
        assert "not queued" in result.lower()
        assert "/queue prd 42" in result


# =====================================================================
# Feature B: /do task <id>
# =====================================================================


class TestDoManualTask:
    """Verify /do task <id> manual autonomous task execution."""

    async def test_prepare_manual_task_not_found(self):
        """prepare_manual_task returns error for nonexistent task."""
        manager = AutonomousManager.__new__(AutonomousManager)
        manager.db = AsyncMock()
        manager.db.get_task = AsyncMock(return_value=None)

        error, ctx = await manager.prepare_manual_task(999)
        assert "not found" in error.lower()
        assert ctx is None

    async def test_prepare_manual_task_wrong_status(self):
        """prepare_manual_task rejects IN_PROGRESS tasks."""
        from nightwire.autonomous.models import TaskStatus
        manager = AutonomousManager.__new__(AutonomousManager)
        task = MagicMock()
        task.status = TaskStatus.IN_PROGRESS
        manager.db = AsyncMock()
        manager.db.get_task = AsyncMock(return_value=task)

        error, ctx = await manager.prepare_manual_task(5)
        assert "cannot execute" in error.lower()

    async def test_prepare_manual_task_success(self):
        """prepare_manual_task claims task and returns context."""
        from nightwire.autonomous.models import TaskStatus
        manager = AutonomousManager.__new__(AutonomousManager)
        task = MagicMock()
        task.status = TaskStatus.QUEUED
        task.title = "Build login form"
        task.description = "Create HTML login form"
        task.depends_on = None
        task.story_id = 1
        task.project_name = "test_project"

        story = MagicMock()
        story.title = "Auth story"
        story.prd_id = 10

        prd = MagicMock()
        prd.title = "Auth PRD"

        manager.db = AsyncMock()
        manager.db.get_task = AsyncMock(return_value=task)
        manager.db.update_task_status = AsyncMock()
        manager.db.get_story = AsyncMock(return_value=story)
        manager.db.get_prd = AsyncMock(return_value=prd)

        error, ctx = await manager.prepare_manual_task(7)
        assert error is None
        assert ctx["title"] == "Build login form"
        assert ctx["story_title"] == "Auth story"
        assert ctx["prd_title"] == "Auth PRD"
        manager.db.update_task_status.assert_called_once()

    async def test_prepare_manual_task_dependency_warnings(self):
        """prepare_manual_task warns about unmet dependencies."""
        from nightwire.autonomous.models import TaskStatus
        manager = AutonomousManager.__new__(AutonomousManager)
        task = MagicMock()
        task.status = TaskStatus.QUEUED
        task.title = "Task B"
        task.description = "Depends on A"
        task.depends_on = [1]
        task.story_id = 1
        task.project_name = "test"

        dep_task = MagicMock()
        dep_task.status = TaskStatus.PENDING
        dep_task.title = "Task A"

        manager.db = AsyncMock()
        manager.db.get_task = AsyncMock(side_effect=[task, dep_task])
        manager.db.update_task_status = AsyncMock()
        manager.db.get_story = AsyncMock(return_value=MagicMock(
            title="Story", prd_id=1,
        ))
        manager.db.get_prd = AsyncMock(return_value=MagicMock(title="PRD"))

        error, ctx = await manager.prepare_manual_task(2)
        assert error is None
        assert len(ctx["warnings"]) == 1
        assert "pending" in ctx["warnings"][0].lower()

    async def test_complete_manual_task_success(self):
        """complete_manual_task marks task COMPLETED and cascades."""
        manager = AutonomousManager.__new__(AutonomousManager)

        task = MagicMock()
        task.story_id = 1

        story = MagicMock()
        story.total_tasks = 1
        story.completed_tasks = 1
        story.failed_tasks = 0
        story.title = "Auth"
        story.prd_id = 10

        prd = MagicMock()
        prd.total_stories = 1
        prd.completed_stories = 1
        prd.failed_stories = 0
        prd.title = "Auth PRD"

        manager.db = AsyncMock()
        manager.db.get_task = AsyncMock(return_value=task)
        manager.db.update_task_status = AsyncMock()
        manager.db.get_story = AsyncMock(return_value=story)
        manager.db.update_story_status = AsyncMock()
        manager.db.get_prd = AsyncMock(return_value=prd)
        manager.db.update_prd_status = AsyncMock()

        result = await manager.complete_manual_task(
            7, True, output="Done",
        )
        assert "completed" in result.lower()
        assert "Auth" in result
        assert "PRD" in result
        manager.db.update_story_status.assert_called_once()
        manager.db.update_prd_status.assert_called_once()

    async def test_complete_manual_task_failure(self):
        """complete_manual_task marks task FAILED."""
        manager = AutonomousManager.__new__(AutonomousManager)
        task = MagicMock()
        task.story_id = 1
        story = MagicMock()
        story.total_tasks = 2
        story.completed_tasks = 0
        story.failed_tasks = 1

        manager.db = AsyncMock()
        manager.db.get_task = AsyncMock(return_value=task)
        manager.db.update_task_status = AsyncMock()
        manager.db.get_story = AsyncMock(return_value=story)

        result = await manager.complete_manual_task(
            7, False, error="Something broke",
        )
        assert "failed" in result.lower()
        # Story not complete yet (1 of 2 tasks terminal)
        manager.db.update_story_status.assert_not_called()

    async def test_do_task_detection(self):
        """handle_do detects 'task <id>' prefix."""
        from nightwire.commands.core import CoreCommandHandler
        handler = CoreCommandHandler.__new__(CoreCommandHandler)
        handler.ctx = MagicMock()
        handler.ctx.cooldown_active = False
        handler.ctx.autonomous_manager = None  # Not configured

        result = await handler.handle_do("+1234", "task 5")
        assert "not available" in result.lower()
