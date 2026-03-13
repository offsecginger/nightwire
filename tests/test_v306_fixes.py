"""Tests for v3.0.6 production fixes.

Fix 1: /prd ingest missing project selection check
Fix 2: /tasks purge three-tier (purge / purge failed / purge all)
Fix 3: /prd delete and /story delete with explicit multi-table DELETE
Fix 4: files_changed detection using base_ref instead of HEAD~1
"""

import sqlite3
import threading
from unittest.mock import AsyncMock, MagicMock, patch

from nightwire.autonomous.commands import AutonomousCommands
from nightwire.autonomous.database import AutonomousDatabase
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
            phone_number TEXT,
            project_name TEXT,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'draft',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prd_id INTEGER,
            phone_number TEXT,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'pending',
            acceptance_criteria TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
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
            max_retries INTEGER DEFAULT 2,
            error_message TEXT,
            claude_output TEXT,
            files_changed TEXT,
            quality_gate_results TEXT,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    return db, conn


def _make_commands(
    manager=None,
    get_current_project=None,
    create_prd_fn=None,
):
    """Create an AutonomousCommands instance for testing."""
    if manager is None:
        manager = AsyncMock()
    if get_current_project is None:
        get_current_project = lambda phone: ("TestProject", "/tmp/test")
    return AutonomousCommands(
        manager=manager,
        get_current_project=get_current_project,
        create_prd_fn=create_prd_fn,
    )


def _seed_prd_with_tasks(db, prd_title="Test PRD", task_statuses=None):
    """Seed a PRD with one story and tasks at various statuses.

    Returns (prd_id, story_id, task_ids).
    """
    if task_statuses is None:
        task_statuses = [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.QUEUED]

    cursor = db._conn.cursor()
    cursor.execute(
        "INSERT INTO prds (phone_number, project_name, title, description, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ("+1234", "TestProject", prd_title, "desc", "active"),
    )
    prd_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO stories (prd_id, phone_number, title, description, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (prd_id, "+1234", "Test Story", "desc", "pending"),
    )
    story_id = cursor.lastrowid

    task_ids = []
    for i, status in enumerate(task_statuses):
        cursor.execute(
            "INSERT INTO tasks (story_id, phone_number, project_name, title, "
            "description, status) VALUES (?, ?, ?, ?, ?, ?)",
            (story_id, "+1234", "TestProject", f"Task {i+1}", "desc", status.value),
        )
        task_ids.append(cursor.lastrowid)

    db._conn.commit()
    return prd_id, story_id, task_ids


# =====================================================================
# Fix 1: /prd ingest missing project selection check
# =====================================================================


class TestPrdIngestProjectCheck:
    """Verify /prd ingest returns error when no project selected."""

    async def test_ingest_no_project_selected(self):
        """Should return 'No project selected' when no project."""
        cmds = _make_commands(
            get_current_project=lambda phone: (None, None),
            create_prd_fn=AsyncMock(),
        )
        result = await cmds.handle_prd("+1234", "ingest")
        assert "No project selected" in result
        assert "/select" in result

    async def test_ingest_with_project_selected(self):
        """Should proceed to create_prd_fn when project is selected."""
        mock_create = AsyncMock(return_value="PRD #1 created")
        cmds = _make_commands(create_prd_fn=mock_create)
        result = await cmds.handle_prd("+1234", "ingest")
        assert mock_create.called
        assert "PRD #1 created" in result


# =====================================================================
# Fix 2: /tasks purge three-tier
# =====================================================================


class TestTasksPurgeThreeTier:
    """Verify three-tier purge: purge / purge failed / purge all."""

    async def test_purge_default_unchanged(self):
        """Default /tasks purge still only targets PENDING/QUEUED/BLOCKED."""
        manager = AsyncMock()
        manager.purge_non_terminal_tasks = AsyncMock(return_value=3)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "purge")
        manager.purge_non_terminal_tasks.assert_called_once()
        assert "3 task(s)" in result
        assert "pending/queued/blocked" in result

    async def test_purge_failed(self):
        """/tasks purge failed only targets FAILED tasks."""
        manager = AsyncMock()
        manager.purge_failed_tasks = AsyncMock(return_value=2)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "purge failed")
        manager.purge_failed_tasks.assert_called_once()
        assert "2 failed task(s)" in result

    async def test_purge_all(self):
        """/tasks purge all targets both queued and failed."""
        manager = AsyncMock()
        manager.purge_non_terminal_tasks = AsyncMock(return_value=3)
        manager.purge_failed_tasks = AsyncMock(return_value=2)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "purge all")
        manager.purge_non_terminal_tasks.assert_called_once()
        manager.purge_failed_tasks.assert_called_once()
        assert "5 task(s)" in result
        assert "3 queued" in result
        assert "2 failed" in result

    async def test_purge_failed_empty(self):
        """No failed tasks to purge."""
        manager = AsyncMock()
        manager.purge_failed_tasks = AsyncMock(return_value=0)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "purge failed")
        assert "No failed tasks" in result

    async def test_purge_all_empty(self):
        """No tasks to purge at all."""
        manager = AsyncMock()
        manager.purge_non_terminal_tasks = AsyncMock(return_value=0)
        manager.purge_failed_tasks = AsyncMock(return_value=0)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_tasks("+1234", "purge all")
        assert "No tasks to purge" in result


class TestPurgeFailedDB:
    """Verify purge_failed_tasks DB method."""

    async def test_purge_failed_tasks_db(self):
        """purge_failed_tasks cancels only FAILED tasks."""
        db, conn = _make_db()
        _seed_prd_with_tasks(
            db,
            task_statuses=[
                TaskStatus.FAILED, TaskStatus.FAILED,
                TaskStatus.QUEUED, TaskStatus.COMPLETED,
            ],
        )
        count = await db.purge_failed_tasks("+1234")
        assert count == 2

        # Verify QUEUED and COMPLETED are untouched
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM tasks WHERE phone_number = ? ORDER BY id",
            ("+1234",),
        )
        statuses = [row[0] for row in cursor.fetchall()]
        assert statuses.count("cancelled") == 2
        assert statuses.count("queued") == 1
        assert statuses.count("completed") == 1

    async def test_purge_failed_with_project_filter(self):
        """purge_failed_tasks respects project filter."""
        db, conn = _make_db()
        _seed_prd_with_tasks(db, task_statuses=[TaskStatus.FAILED])
        # Create a task in a different project
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tasks (story_id, phone_number, project_name, "
            "title, description, status) VALUES (1, '+1234', 'OtherProject', "
            "'Other', 'desc', 'failed')"
        )
        conn.commit()

        count = await db.purge_failed_tasks("+1234", "TestProject")
        assert count == 1  # Only TestProject's failed task


# =====================================================================
# Fix 3: /prd delete and /story delete
# =====================================================================


class TestPrdDelete:
    """Verify /prd delete cascading deletes."""

    async def test_delete_prd_cascade(self):
        """delete_prd removes PRD + stories + tasks."""
        db, conn = _make_db()
        prd_id, story_id, task_ids = _seed_prd_with_tasks(
            db,
            task_statuses=[TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.QUEUED],
        )

        result = await db.delete_prd(prd_id)
        assert result is not None
        assert result["tasks"] == 3
        assert result["stories"] == 1
        assert result["prd_title"] == "Test PRD"

        # Verify everything is gone
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM prds WHERE id = ?", (prd_id,))
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM stories WHERE prd_id = ?", (prd_id,))
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE story_id = ?", (story_id,))
        assert cursor.fetchone()[0] == 0

    async def test_delete_prd_not_found(self):
        """delete_prd returns None for non-existent PRD."""
        db, _ = _make_db()
        result = await db.delete_prd(9999)
        assert result is None

    async def test_delete_prd_refuses_in_progress(self):
        """delete_prd raises ValueError when tasks are IN_PROGRESS."""
        db, _ = _make_db()
        prd_id, _, _ = _seed_prd_with_tasks(
            db,
            task_statuses=[TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED],
        )

        try:
            await db.delete_prd(prd_id)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "in progress" in str(e).lower()

    async def test_delete_prd_command(self):
        """Command handler formats delete response correctly."""
        manager = AsyncMock()
        manager.delete_prd = AsyncMock(return_value={
            "tasks": 5,
            "stories": 2,
            "prd_title": "Auth System",
        })
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_prd("+1234", "delete 3")
        assert "Deleted PRD #3" in result
        assert "Auth System" in result
        assert "2 story(ies)" in result
        assert "5 task(s)" in result

    async def test_delete_prd_not_found_command(self):
        """Command handler returns not found message."""
        manager = AsyncMock()
        manager.delete_prd = AsyncMock(return_value=None)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_prd("+1234", "delete 99")
        assert "not found" in result

    async def test_delete_prd_in_progress_command(self):
        """Command handler returns error for in-progress tasks."""
        manager = AsyncMock()
        manager.delete_prd = AsyncMock(
            side_effect=ValueError("has tasks in progress")
        )
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_prd("+1234", "delete 3")
        assert "in progress" in result

    async def test_delete_prd_usage(self):
        """Command handler returns usage when no ID given."""
        cmds = _make_commands()
        result = await cmds.handle_prd("+1234", "delete")
        assert "Usage" in result


class TestStoryDelete:
    """Verify /story delete cascading deletes."""

    async def test_delete_story_cascade(self):
        """delete_story removes story + tasks."""
        db, conn = _make_db()
        _, story_id, _ = _seed_prd_with_tasks(
            db,
            task_statuses=[TaskStatus.COMPLETED, TaskStatus.FAILED],
        )

        result = await db.delete_story(story_id)
        assert result is not None
        assert result["tasks"] == 2
        assert result["story_title"] == "Test Story"

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stories WHERE id = ?", (story_id,))
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE story_id = ?", (story_id,))
        assert cursor.fetchone()[0] == 0

    async def test_delete_story_not_found(self):
        """delete_story returns None for non-existent story."""
        db, _ = _make_db()
        result = await db.delete_story(9999)
        assert result is None

    async def test_delete_story_refuses_in_progress(self):
        """delete_story raises ValueError when tasks are IN_PROGRESS."""
        db, _ = _make_db()
        _, story_id, _ = _seed_prd_with_tasks(
            db,
            task_statuses=[TaskStatus.IN_PROGRESS],
        )

        try:
            await db.delete_story(story_id)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "in progress" in str(e).lower()

    async def test_delete_story_command(self):
        """Command handler formats delete response correctly."""
        manager = AsyncMock()
        manager.delete_story = AsyncMock(return_value={
            "tasks": 3,
            "story_title": "User Login",
            "prd_id": 1,
        })
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_story("+1234", "delete 5")
        assert "Deleted Story #5" in result
        assert "User Login" in result
        assert "3 task(s)" in result
        assert "PRD #1" in result

    async def test_delete_story_not_found_command(self):
        """Command handler returns not found message."""
        manager = AsyncMock()
        manager.delete_story = AsyncMock(return_value=None)
        cmds = _make_commands(manager=manager)
        result = await cmds.handle_story("+1234", "delete 99")
        assert "not found" in result


# =====================================================================
# Fix 4: files_changed detection using base_ref
# =====================================================================


class TestFilesChangedBaseRef:
    """Verify _get_files_changed uses base_ref for comparison."""

    async def test_base_ref_used_when_no_uncommitted(self):
        """When no uncommitted changes, compares against base_ref not HEAD~1."""
        from nightwire.autonomous.executor import TaskExecutor

        executor = TaskExecutor.__new__(TaskExecutor)

        # Mock all three git commands to return empty (no uncommitted changes)
        call_count = 0
        diff_args_captured = []

        async def mock_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            proc.returncode = 0

            cmd_list = list(args)
            diff_args_captured.append(cmd_list)

            # The 4th call (base_ref comparison) returns actual files
            if call_count == 4:
                proc.communicate = AsyncMock(
                    return_value=(b"src/main.py\nsrc/config.py\n", b"")
                )
            else:
                proc.communicate = AsyncMock(return_value=(b"", b""))

            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess):
            from pathlib import Path
            result = await executor._get_files_changed(
                Path("/tmp/test"), base_ref="abc123"
            )

        assert "src/main.py" in result
        assert "src/config.py" in result
        # Verify the 4th call used base_ref, not HEAD~1
        assert any(
            "abc123" in str(args) for args in diff_args_captured
        ), f"base_ref not found in git calls: {diff_args_captured}"

    async def test_fallback_to_head1_when_no_base_ref(self):
        """Without base_ref, falls back to HEAD~1."""
        from nightwire.autonomous.executor import TaskExecutor

        executor = TaskExecutor.__new__(TaskExecutor)

        call_count = 0
        diff_args_captured = []

        async def mock_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            proc.returncode = 0
            diff_args_captured.append(list(args))
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess):
            from pathlib import Path
            result = await executor._get_files_changed(Path("/tmp/test"))

        # Verify HEAD~1 was used (no base_ref)
        assert any(
            "HEAD~1" in str(args) for args in diff_args_captured
        ), f"HEAD~1 not found in git calls: {diff_args_captured}"

    async def test_uncommitted_changes_bypass_base_ref(self):
        """If uncommitted changes exist, base_ref comparison is skipped."""
        from nightwire.autonomous.executor import TaskExecutor

        executor = TaskExecutor.__new__(TaskExecutor)

        call_count = 0

        async def mock_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            proc.returncode = 0

            # First call (git diff HEAD) returns files
            if call_count == 1:
                proc.communicate = AsyncMock(
                    return_value=(b"src/app.py\n", b"")
                )
            else:
                proc.communicate = AsyncMock(return_value=(b"", b""))

            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess):
            from pathlib import Path
            result = await executor._get_files_changed(
                Path("/tmp/test"), base_ref="abc123"
            )

        assert "src/app.py" in result
        # Should only have 3 calls (diff HEAD, ls-files, diff --cached)
        # NOT 4 (the base_ref comparison should be skipped since we found files)
        assert call_count == 3


# =====================================================================
# Help text includes new commands
# =====================================================================


class TestHelpTextUpdates:
    """Verify help text includes new delete subcommands."""

    def test_prd_help_includes_delete(self):
        cmds = _make_commands()
        help_text = cmds._prd_help()
        assert "delete" in help_text.lower()

    def test_story_help_includes_delete(self):
        cmds = _make_commands()
        help_text = cmds._story_help()
        assert "delete" in help_text.lower()

    def test_help_metadata_prd_includes_delete(self):
        from nightwire.autonomous.commands import get_autonomous_help_metadata
        meta = get_autonomous_help_metadata()
        assert "delete" in meta["prd"].usage
        assert any("delete" in ex for ex in meta["prd"].examples)

    def test_help_metadata_story_includes_delete(self):
        from nightwire.autonomous.commands import get_autonomous_help_metadata
        meta = get_autonomous_help_metadata()
        assert "delete" in meta["story"].usage
        assert any("delete" in ex for ex in meta["story"].examples)

    def test_help_metadata_tasks_purge_tiers(self):
        from nightwire.autonomous.commands import get_autonomous_help_metadata
        meta = get_autonomous_help_metadata()
        assert "purge failed" in meta["tasks"].usage
        assert "purge all" in meta["tasks"].usage
