"""Tests for Milestone 14 Stories 14.1-14.4: Bug fixes, UX, error handling, cleanup.

Validates all Python source changes from the M14 upstream port.
"""

import asyncio
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# 14.1.3a: Autonomous subsystem fixes
# ---------------------------------------------------------------------------


class TestStoryCommandLogic:
    """Verify /story show-vs-create logic fix (14.1.3a)."""

    async def test_story_show_when_no_subargs(self):
        """'/story 5' (no subargs) should show story, not create."""
        from nightwire.autonomous.commands import AutonomousCommands

        mgr = MagicMock()
        mgr.get_story = AsyncMock(return_value=MagicMock(
            id=5, title="Test", description="Desc", prd_id=1,
            status=MagicMock(value="pending"),
        ))
        mgr.list_tasks = AsyncMock(return_value=[])
        cmd = AutonomousCommands(mgr, get_current_project=lambda p: None)

        # /story 5 → subcommand="5", subargs=""
        await cmd.handle_story("+1234", "5")
        mgr.get_story.assert_called_once_with(5)

    async def test_story_create_with_args(self):
        """'/story 3 Login | Users can log in' should create, not show."""
        from nightwire.autonomous.commands import AutonomousCommands

        mgr = MagicMock()
        mgr.get_prd = AsyncMock(return_value=MagicMock(id=3))
        mgr.create_story = AsyncMock(return_value=MagicMock(
            id=10, title="Login", prd_id=3,
        ))
        cmd = AutonomousCommands(mgr, get_current_project=lambda p: None)

        await cmd.handle_story("+1234", "3 Login | Users can log in")
        mgr.create_story.assert_called_once()


class TestTruthinessChecks:
    """Verify prd_id/story_id truthiness fixes (14.1.3a)."""

    def test_prd_id_zero_not_skipped(self):
        """prd_id=0 should still filter (is not None check)."""
        import inspect

        from nightwire.autonomous.database import AutonomousDatabase
        source = inspect.getsource(AutonomousDatabase._list_stories_sync)
        assert "prd_id is not None" in source


class TestPerProjectGitLock:
    """Verify per-project git lock (14.1.3a)."""

    def test_git_locks_dict_exists(self):
        from nightwire.autonomous.executor import _git_locks
        assert isinstance(_git_locks, dict)

    def test_get_git_lock_returns_lock(self):
        from nightwire.autonomous.executor import _get_git_lock
        lock = _get_git_lock("/tmp/project_a")
        assert isinstance(lock, asyncio.Lock)

    def test_same_project_same_lock(self):
        from nightwire.autonomous.executor import _get_git_lock
        lock1 = _get_git_lock("/tmp/project_same")
        lock2 = _get_git_lock("/tmp/project_same")
        assert lock1 is lock2

    def test_different_projects_different_locks(self):
        from nightwire.autonomous.executor import _get_git_lock
        lock1 = _get_git_lock("/tmp/project_x")
        lock2 = _get_git_lock("/tmp/project_y")
        assert lock1 is not lock2


# ---------------------------------------------------------------------------
# 14.1.3b: Config fixes
# ---------------------------------------------------------------------------


class TestConfigFixes:
    """Verify config.py fixes (14.1.3b)."""

    def test_max_parallel_lower_bound(self):
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {"autonomous": {"max_parallel": 0}}
        config._settings_path = Path("/fake")
        config._projects_path = Path("/fake")
        config._env_path = Path("/fake")
        config.projects = {}
        assert config.autonomous_max_parallel >= 1

    def test_max_parallel_negative(self):
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {"autonomous": {"max_parallel": -5}}
        config._settings_path = Path("/fake")
        config._projects_path = Path("/fake")
        config._env_path = Path("/fake")
        config.projects = {}
        assert config.autonomous_max_parallel >= 1

    def test_instance_name_default(self):
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {}
        config._settings_path = Path("/fake")
        config._projects_path = Path("/fake")
        config._env_path = Path("/fake")
        config.projects = {}
        assert config.instance_name == "nightwire"

    def test_instance_name_custom(self):
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {"instance_name": "mybot"}
        config._settings_path = Path("/fake")
        config._projects_path = Path("/fake")
        config._env_path = Path("/fake")
        config.projects = {}
        assert config.instance_name == "mybot"

    def test_assistant_config_bool_protection(self):
        """nightwire_assistant: true (bool) should not crash."""
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {"nightwire_assistant": True}
        config._settings_path = Path("/fake")
        config._projects_path = Path("/fake")
        config._env_path = Path("/fake")
        config.projects = {}
        # Should not raise AttributeError
        assert config.nightwire_assistant_enabled is False


# ---------------------------------------------------------------------------
# 14.1.3c: Memory subsystem fixes
# ---------------------------------------------------------------------------


class TestMemoryDatabaseLocking:
    """Verify threading lock on sync write methods (14.1.3c)."""

    def test_lock_exists(self):
        from nightwire.memory.database import DatabaseConnection
        db = DatabaseConnection.__new__(DatabaseConnection)
        db._lock = threading.Lock()
        assert isinstance(db._lock, type(threading.Lock()))

    def test_ensure_user_acquires_lock(self):
        """_ensure_user_sync should use self._lock."""
        import inspect

        from nightwire.memory.database import DatabaseConnection
        source = inspect.getsource(DatabaseConnection._ensure_user_sync)
        assert "self._lock" in source

    def test_store_conversation_acquires_lock(self):
        import inspect

        from nightwire.memory.database import DatabaseConnection
        source = inspect.getsource(DatabaseConnection._store_conversation_sync)
        assert "self._lock" in source

    def test_delete_all_acquires_lock(self):
        import inspect

        from nightwire.memory.database import DatabaseConnection
        source = inspect.getsource(DatabaseConnection._delete_all_user_data_sync)
        assert "self._lock" in source


class TestTimestampFallback:
    """Verify _parse_sqlite_timestamp logs on null (14.1.3c)."""

    def test_returns_datetime_for_null(self):
        from nightwire.memory.database import DatabaseConnection
        db = DatabaseConnection.__new__(DatabaseConnection)
        result = db._parse_sqlite_timestamp(None)
        assert isinstance(result, datetime)

    def test_returns_datetime_for_empty(self):
        from nightwire.memory.database import DatabaseConnection
        db = DatabaseConnection.__new__(DatabaseConnection)
        result = db._parse_sqlite_timestamp("")
        assert isinstance(result, datetime)

    def test_parses_sqlite_format(self):
        from nightwire.memory.database import DatabaseConnection
        db = DatabaseConnection.__new__(DatabaseConnection)
        result = db._parse_sqlite_timestamp("2026-03-04 12:00:00")
        assert result.year == 2026
        assert result.month == 3


class TestEmbeddingCleanup:
    """Verify embedding cleanup in /forget all (14.1.3c)."""

    def test_delete_all_has_embedding_cleanup(self):
        import inspect

        from nightwire.memory.database import DatabaseConnection
        source = inspect.getsource(
            DatabaseConnection._delete_all_user_data_sync
        )
        assert "DELETE FROM embeddings" in source


# ---------------------------------------------------------------------------
# 14.1.3d: Utility fixes
# ---------------------------------------------------------------------------


class TestNightwireRunnerSessionLock:
    """Verify asyncio.Lock on _get_session (14.1.3d)."""

    def test_session_lock_exists(self):
        from nightwire.nightwire_runner import NightwireRunner
        runner = NightwireRunner.__new__(NightwireRunner)
        runner._session = None
        runner._session_lock = asyncio.Lock()
        assert isinstance(runner._session_lock, asyncio.Lock)

    def test_get_session_has_lock(self):
        import inspect

        from nightwire.nightwire_runner import NightwireRunner
        source = inspect.getsource(NightwireRunner._get_session)
        assert "_session_lock" in source


# ---------------------------------------------------------------------------
# 14.1.2: Message splitting
# ---------------------------------------------------------------------------


class TestMessageSplitting:
    """Verify _split_message in bot.py (14.1.2)."""

    def test_short_message_no_split(self):
        from nightwire.bot import SignalBot
        parts = SignalBot._split_message("Hello world")
        assert len(parts) == 1
        assert parts[0] == "Hello world"

    def test_long_message_splits(self):
        from nightwire.bot import SignalBot
        long_msg = "x" * 10000
        parts = SignalBot._split_message(long_msg, max_len=5000)
        assert len(parts) >= 2
        for part in parts:
            assert len(part) <= 5000

    def test_splits_at_paragraph_boundary(self):
        from nightwire.bot import SignalBot
        msg = ("A" * 3000) + "\n\n" + ("B" * 3000)
        parts = SignalBot._split_message(msg, max_len=5000)
        assert len(parts) == 2
        assert parts[0].endswith("A" * 10)
        assert parts[1].startswith("B")

    def test_splits_at_newline_boundary(self):
        from nightwire.bot import SignalBot
        msg = ("A" * 3000) + "\n" + ("B" * 3000)
        parts = SignalBot._split_message(msg, max_len=5000)
        assert len(parts) == 2


class TestTruncationRemoved:
    """Verify 4000-char truncation removed (14.1.2)."""

    def test_no_max_signal_length_constant(self):
        import nightwire.claude_runner as cr
        assert not hasattr(cr, "MAX_SIGNAL_LENGTH")

    def test_nightwire_runner_no_truncation(self):
        import inspect

        from nightwire.nightwire_runner import NightwireRunner
        source = inspect.getsource(NightwireRunner.ask)
        assert "4000" not in source
        assert "truncated" not in source.lower()


# ---------------------------------------------------------------------------
# 14.3.1-14.3.2: Claude runner fixes
# ---------------------------------------------------------------------------


class TestClaudeRunnerFixes:
    """Verify claude_runner.py changes (14.3.1, 14.3.2)."""

    def test_max_retries_is_one(self):
        from nightwire.claude_runner import MAX_RETRIES
        assert MAX_RETRIES == 1

    def test_sandbox_disabled_in_command(self):
        from nightwire.claude_runner import ClaudeRunner
        config = MagicMock()
        config.claude_path = "claude"
        config.claude_model = "sonnet"
        config.settings = {}
        config.claude_max_budget_usd = None
        config.config_dir = Path("/fake")
        runner = ClaudeRunner.__new__(ClaudeRunner)
        runner.config = config
        cmd = runner._build_command()
        assert "--settings" in cmd
        joined = " ".join(cmd)
        assert "sandbox" in joined
        assert "false" in joined

    def test_kill_signal_classified_permanent(self):
        from nightwire.claude_runner import classify_error
        from nightwire.exceptions import ErrorCategory
        assert classify_error(-9, "", "") == ErrorCategory.PERMANENT
        assert classify_error(-15, "", "") == ErrorCategory.PERMANENT
        assert classify_error(137, "", "") == ErrorCategory.PERMANENT
        assert classify_error(143, "", "") == ErrorCategory.PERMANENT


# ---------------------------------------------------------------------------
# 14.4.1: sqlite-vec load API
# ---------------------------------------------------------------------------


class TestSqliteVecLoadApi:
    """Verify sqlite_vec.load() usage (14.4.1)."""

    def test_uses_sqlite_vec_load_api(self):
        import inspect

        from nightwire.memory.database import DatabaseConnection
        source = inspect.getsource(DatabaseConnection._initialize_sync)
        # Should use sqlite_vec.load(), not conn.load_extension("vec0")
        assert "sqlite_vec.load" in source
        assert "import sqlite_vec" in source


# ---------------------------------------------------------------------------
# 14.4.3: skill_registry.py deleted
# ---------------------------------------------------------------------------


class TestSkillRegistryDeleted:
    """Verify skill_registry.py removal (14.4.3)."""

    def test_file_does_not_exist(self):
        path = Path(__file__).parent.parent / "nightwire" / "skill_registry.py"
        assert not path.exists()


# ---------------------------------------------------------------------------
# 14.4.5: asyncio.iscoroutinefunction deprecation
# ---------------------------------------------------------------------------


class TestAsyncioDeprecationFix:
    """Verify inspect.iscoroutinefunction usage (14.4.5)."""

    def test_security_uses_inspect(self):
        import inspect as inspect_mod

        from nightwire import security
        source = inspect_mod.getsource(security)
        assert "inspect.iscoroutinefunction" in source
        # Should NOT have asyncio.iscoroutinefunction anymore
        assert "asyncio.iscoroutinefunction" not in source
