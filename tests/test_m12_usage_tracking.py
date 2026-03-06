"""Tests for Milestone 12: Usage & Cost Tracking.

Covers:
- Schema migration (v5 usage_records table)
- Database CRUD (record_usage, get_usage_summary, get_usage_by_project, etc.)
- ClaudeRunner last_usage property
- NightwireRunner last_usage property
- HaikuSummarizer last_usage property
- TaskManager _record_usage + budget alerts
- /usage command (default, project, all)
- Config properties (daily/weekly budget)
- Usage accumulation in executor
"""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nightwire.memory.database import DatabaseConnection


async def _make_db(tmp_path):
    """Create an initialized DatabaseConnection for testing."""
    db_path = tmp_path / "test.db"
    db = DatabaseConnection(db_path)
    await db.initialize()
    return db


# ============================================================
# Schema & Database Tests
# ============================================================

class TestUsageSchema:
    """Tests for the usage_records table schema and migration."""

    def test_schema_version_is_5(self):
        from nightwire.memory.database import SCHEMA_VERSION
        assert SCHEMA_VERSION == 5

    async def test_usage_records_table_exists(self, tmp_path):
        db = await _make_db(tmp_path)
        cursor = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='usage_records'"
        )
        assert cursor.fetchone() is not None
        db._conn.close()

    async def test_usage_records_columns(self, tmp_path):
        db = await _make_db(tmp_path)
        cursor = db._conn.execute("PRAGMA table_info(usage_records)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "id", "phone_number", "project_name", "model",
            "input_tokens", "output_tokens", "cost_usd",
            "source", "session_id", "timestamp",
        }
        assert expected.issubset(columns)
        db._conn.close()

    async def test_usage_records_indexes(self, tmp_path):
        db = await _make_db(tmp_path)
        cursor = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='usage_records'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_usage_phone_time" in indexes
        assert "idx_usage_phone_project" in indexes
        assert "idx_usage_source" in indexes
        db._conn.close()


class TestUsageDatabaseOps:
    """Tests for usage database CRUD operations."""

    async def test_record_usage_basic(self, tmp_path):
        db = await _make_db(tmp_path)
        await db.record_usage(
            phone_number="+1234567890",
            project_name="myproject",
            model="claude-sonnet",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.005,
            source="do",
        )
        cursor = db._conn.execute("SELECT COUNT(*) FROM usage_records")
        assert cursor.fetchone()[0] == 1
        db._conn.close()

    async def test_record_usage_with_session_id(self, tmp_path):
        db = await _make_db(tmp_path)
        await db.record_usage(
            phone_number="+1234567890",
            project_name="myproject",
            model="claude-sonnet",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.005,
            source="do",
            session_id="sess-abc123",
        )
        cursor = db._conn.execute("SELECT session_id FROM usage_records")
        assert cursor.fetchone()[0] == "sess-abc123"
        db._conn.close()

    async def test_get_usage_summary_all_time(self, tmp_path):
        db = await _make_db(tmp_path)
        await db.record_usage("+1234567890", source="do", model="model",
                              input_tokens=100, output_tokens=50, cost_usd=0.01,
                              project_name="proj")
        await db.record_usage("+1234567890", source="ask", model="model",
                              input_tokens=200, output_tokens=100, cost_usd=0.02,
                              project_name="proj")
        result = await db.get_usage_summary("+1234567890")
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 150
        assert abs(result["cost_usd"] - 0.03) < 0.001
        db._conn.close()

    async def test_get_usage_summary_with_days_filter(self, tmp_path):
        db = await _make_db(tmp_path)
        # Insert a record with old timestamp
        db._conn.execute(
            "INSERT INTO usage_records (phone_number, model, input_tokens, "
            "output_tokens, cost_usd, source, timestamp) VALUES "
            "(?, ?, ?, ?, ?, ?, ?)",
            ("+1234567890", "model", 500, 500, 1.0, "do", "2020-01-01 00:00:00"),
        )
        db._conn.commit()
        # Insert recent record
        await db.record_usage("+1234567890", source="do", model="model",
                              input_tokens=100, output_tokens=50, cost_usd=0.01,
                              project_name="proj")
        # Query for today only
        result = await db.get_usage_summary("+1234567890", days=0)
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        db._conn.close()

    async def test_get_usage_by_project(self, tmp_path):
        db = await _make_db(tmp_path)
        await db.record_usage("+1234567890", source="do", model="model",
                              input_tokens=100, output_tokens=50, cost_usd=0.01,
                              project_name="proj-a")
        await db.record_usage("+1234567890", source="ask", model="model",
                              input_tokens=200, output_tokens=100, cost_usd=0.02,
                              project_name="proj-a")
        await db.record_usage("+1234567890", source="do", model="model",
                              input_tokens=50, output_tokens=25, cost_usd=0.005,
                              project_name="proj-b")
        rows = await db.get_usage_by_project("+1234567890")
        assert len(rows) == 2
        projects = {r["project_name"] for r in rows}
        assert projects == {"proj-a", "proj-b"}
        db._conn.close()

    async def test_get_usage_all_users(self, tmp_path):
        db = await _make_db(tmp_path)
        await db.record_usage("+1111111111", source="do", model="model",
                              input_tokens=100, output_tokens=50, cost_usd=0.01,
                              project_name="proj")
        await db.record_usage("+2222222222", source="ask", model="model",
                              input_tokens=200, output_tokens=100, cost_usd=0.02,
                              project_name="proj")
        rows = await db.get_usage_all_users()
        assert len(rows) == 2
        phones = {r["phone_number"] for r in rows}
        assert phones == {"+1111111111", "+2222222222"}
        db._conn.close()

    async def test_get_usage_cost_since(self, tmp_path):
        db = await _make_db(tmp_path)
        await db.record_usage("+1234567890", source="do", model="model",
                              input_tokens=100, output_tokens=50, cost_usd=0.15,
                              project_name="proj")
        await db.record_usage("+1234567890", source="ask", model="model",
                              input_tokens=200, output_tokens=100, cost_usd=0.25,
                              project_name="proj")
        cost = await db.get_usage_cost_since("+1234567890", 1)
        assert abs(cost - 0.40) < 0.001
        db._conn.close()

    async def test_source_constraint_rejects_invalid(self, tmp_path):
        db = await _make_db(tmp_path)
        with pytest.raises(Exception):
            await db.record_usage("+1234567890", "proj", "model", 0, 0, 0, "INVALID")
        db._conn.close()


# ============================================================
# Runner last_usage Tests
# ============================================================

class TestClaudeRunnerUsage:
    """Tests for ClaudeRunner._last_usage tracking."""

    def test_initial_last_usage_is_none(self):
        from nightwire.claude_runner import ClaudeRunner
        with patch("nightwire.claude_runner.get_config") as mock_config:
            mock_config.return_value = MagicMock(
                claude_path="claude",
                claude_model="sonnet",
                claude_system_prompt=None,
                claude_max_budget_usd=None,
                sandbox_enabled=False,
            )
            runner = ClaudeRunner()
            assert runner.last_usage is None

    def test_invocation_state_has_last_usage(self):
        from nightwire.claude_runner import _InvocationState
        state = _InvocationState()
        assert state._last_usage is None
        state._last_usage = {"input_tokens": 100}
        assert state._last_usage["input_tokens"] == 100


class TestNightwireRunnerUsage:
    """Tests for NightwireRunner._last_usage tracking."""

    def test_initial_last_usage_is_none(self):
        from nightwire.nightwire_runner import NightwireRunner
        runner = NightwireRunner(
            api_url="https://api.example.com/v1/chat/completions",
            api_key="test-key",
            model="gpt-4o",
        )
        assert runner.last_usage is None

    def test_parse_response_populates_usage(self):
        from nightwire.nightwire_runner import NightwireRunner
        runner = NightwireRunner(
            api_url="https://api.example.com/v1/chat/completions",
            api_key="test-key",
            model="gpt-4o",
        )
        data = {
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            "model": "gpt-4o",
        }
        result = runner._parse_response(data)
        assert result is not None
        assert runner.last_usage is not None
        assert runner.last_usage["input_tokens"] == 100
        assert runner.last_usage["output_tokens"] == 50
        assert runner.last_usage["model"] == "gpt-4o"
        assert runner.last_usage["cost_usd"] == 0.0


class TestHaikuSummarizerUsage:
    """Tests for HaikuSummarizer._last_usage tracking."""

    def test_initial_last_usage_is_none(self):
        from nightwire.memory.haiku_summarizer import HaikuSummarizer
        summarizer = HaikuSummarizer()
        assert summarizer.last_usage is None


# ============================================================
# TaskManager Recording & Budget Alerts
# ============================================================

class TestTaskManagerUsageRecording:
    """Tests for TaskManager._record_usage and budget alerts."""

    async def _make_task_manager(self, tmp_path):
        from nightwire.task_manager import TaskManager

        db = await _make_db(tmp_path)

        memory = MagicMock()
        memory.db = db

        config = MagicMock()
        config.usage_daily_budget_usd = None
        config.usage_weekly_budget_usd = None

        send_message = AsyncMock()
        get_memory_context = AsyncMock(return_value=None)

        tm = TaskManager(
            runner=MagicMock(),
            project_manager=MagicMock(),
            memory=memory,
            config=config,
            send_message=send_message,
            send_typing_indicator=AsyncMock(),
            get_memory_context=get_memory_context,
        )
        return tm, db, send_message

    async def test_record_usage_stores_to_db(self, tmp_path):
        tm, db, _ = await self._make_task_manager(tmp_path)
        await tm._record_usage(
            phone_number="+1234567890",
            project_name="myproj",
            source="do",
            usage_data={
                "input_tokens": 100, "output_tokens": 50,
                "model": "sonnet", "cost_usd": 0.01,
            },
        )
        cursor = db._conn.execute("SELECT COUNT(*) FROM usage_records")
        assert cursor.fetchone()[0] == 1
        db._conn.close()

    async def test_record_usage_none_data_no_op(self, tmp_path):
        tm, db, _ = await self._make_task_manager(tmp_path)
        await tm._record_usage(
            phone_number="+1234567890",
            project_name="myproj",
            source="do",
            usage_data=None,
        )
        cursor = db._conn.execute("SELECT COUNT(*) FROM usage_records")
        assert cursor.fetchone()[0] == 0
        db._conn.close()

    async def test_budget_alert_at_80_percent(self, tmp_path):
        tm, db, send_msg = await self._make_task_manager(tmp_path)
        tm.config.usage_daily_budget_usd = 1.00
        # Record $0.85 of usage (85% of $1.00)
        await tm._record_usage(
            phone_number="+1234567890",
            project_name="proj",
            source="do",
            usage_data={
                "input_tokens": 1000, "output_tokens": 500,
                "model": "sonnet", "cost_usd": 0.85,
            },
        )
        # Should have sent an 80% warning
        send_msg.assert_called_once()
        msg = send_msg.call_args[0][1]
        assert "WARNING" in msg
        db._conn.close()

    async def test_budget_alert_at_100_percent(self, tmp_path):
        tm, db, send_msg = await self._make_task_manager(tmp_path)
        tm.config.usage_daily_budget_usd = 1.00
        # Record $1.50 of usage (150%)
        await tm._record_usage(
            phone_number="+1234567890",
            project_name="proj",
            source="do",
            usage_data={
                "input_tokens": 5000, "output_tokens": 2000,
                "model": "sonnet", "cost_usd": 1.50,
            },
        )
        # Should have sent a 100% EXCEEDED alert
        assert send_msg.call_count >= 1
        msg = send_msg.call_args[0][1]
        assert "EXCEEDED" in msg
        db._conn.close()

    async def test_budget_alert_spam_prevention(self, tmp_path):
        tm, db, send_msg = await self._make_task_manager(tmp_path)
        tm.config.usage_daily_budget_usd = 1.00
        usage = {
            "input_tokens": 500, "output_tokens": 250,
            "model": "sonnet", "cost_usd": 0.90,
        }
        # Record twice — first triggers 80% alert
        await tm._record_usage("+1234567890", "proj", "do", usage)
        # Second takes total to $1.80 → triggers 100% but NOT another 80%
        await tm._record_usage("+1234567890", "proj", "do", usage)
        alert_messages = [call[0][1] for call in send_msg.call_args_list]
        warning_count = sum(1 for m in alert_messages if "WARNING" in m)
        assert warning_count <= 1
        db._conn.close()


# ============================================================
# /usage Command Tests
# ============================================================

class TestUsageCommand:
    """Tests for the /usage command handler."""

    async def _make_handler_with_db(self, tmp_path):
        from nightwire.commands.core import CoreCommandHandler
        from nightwire.commands.base import BotContext

        db = await _make_db(tmp_path)

        memory = MagicMock()
        memory.db = db

        task_manager = MagicMock()
        task_manager.memory = memory

        config = MagicMock()
        config.allowed_numbers = ["+1111111111"]

        project_manager = MagicMock()
        project_manager.get_current_project.return_value = "testproj"

        ctx = MagicMock(spec=BotContext)
        ctx.task_manager = task_manager
        ctx.config = config
        ctx.project_manager = project_manager
        ctx.nightwire_runner = None

        handler = CoreCommandHandler(ctx)
        return handler, db

    async def test_usage_no_data(self, tmp_path):
        handler, db = await self._make_handler_with_db(tmp_path)
        result = await handler.handle_usage("+1234567890", "")
        assert "Usage Summary" in result
        assert "0 tokens" in result
        db._conn.close()

    async def test_usage_with_data(self, tmp_path):
        handler, db = await self._make_handler_with_db(tmp_path)
        await db.record_usage("+1234567890", source="do", model="sonnet",
                              input_tokens=1000, output_tokens=500, cost_usd=0.05,
                              project_name="proj")
        result = await handler.handle_usage("+1234567890", "")
        assert "Usage Summary" in result
        assert "1,500 tokens" in result
        assert "$0.0500" in result
        db._conn.close()

    async def test_usage_project_breakdown(self, tmp_path):
        handler, db = await self._make_handler_with_db(tmp_path)
        await db.record_usage("+1234567890", source="do", model="sonnet",
                              input_tokens=100, output_tokens=50, cost_usd=0.01,
                              project_name="proj-a")
        await db.record_usage("+1234567890", source="ask", model="sonnet",
                              input_tokens=200, output_tokens=100, cost_usd=0.02,
                              project_name="proj-b")
        result = await handler.handle_usage("+1234567890", "project")
        assert "By Project" in result
        assert "proj-a" in result
        assert "proj-b" in result
        db._conn.close()

    async def test_usage_all_admin_only(self, tmp_path):
        handler, db = await self._make_handler_with_db(tmp_path)
        # Non-admin user
        result = await handler.handle_usage("+9999999999", "all")
        assert "admin" in result.lower()
        db._conn.close()

    async def test_usage_all_admin_sees_all_users(self, tmp_path):
        handler, db = await self._make_handler_with_db(tmp_path)
        await db.record_usage("+1111111111", source="do", model="sonnet",
                              input_tokens=100, output_tokens=50, cost_usd=0.01,
                              project_name="proj")
        await db.record_usage("+2222222222", source="ask", model="sonnet",
                              input_tokens=200, output_tokens=100, cost_usd=0.02,
                              project_name="proj")
        # Admin user (first in allowed_numbers)
        result = await handler.handle_usage("+1111111111", "all")
        assert "All Users" in result
        assert "1111" in result
        assert "2222" in result
        db._conn.close()


# ============================================================
# Config Property Tests
# ============================================================

class TestUsageConfigProperties:
    """Tests for usage-related config properties."""

    def test_daily_budget_default_none(self):
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {}
        config.env_vars = {}
        assert config.usage_daily_budget_usd is None

    def test_daily_budget_from_settings(self):
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {"usage": {"daily_budget_usd": 5.0}}
        config.env_vars = {}
        assert config.usage_daily_budget_usd == 5.0

    def test_weekly_budget_from_settings(self):
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {"usage": {"weekly_budget_usd": 25.0}}
        config.env_vars = {}
        assert config.usage_weekly_budget_usd == 25.0

    def test_budget_invalid_value_returns_none(self):
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {"usage": {"daily_budget_usd": "not-a-number"}}
        config.env_vars = {}
        assert config.usage_daily_budget_usd is None

    def test_usage_non_dict_returns_none(self):
        from nightwire.config import Config
        config = Config.__new__(Config)
        config.settings = {"usage": "not-a-dict"}
        config.env_vars = {}
        assert config.usage_daily_budget_usd is None


# ============================================================
# Autonomous Usage Accumulation
# ============================================================

class TestAutonomousUsageAccumulation:
    """Tests for usage accumulation in executor and result models."""

    def test_task_execution_result_has_usage_data(self):
        from nightwire.autonomous.models import TaskExecutionResult
        result = TaskExecutionResult(
            task_id=1,
            success=True,
            claude_output="done",
            usage_data=[
                {"input_tokens": 100, "output_tokens": 50, "model": "sonnet", "cost_usd": 0.01},
                {"input_tokens": 200, "output_tokens": 100, "model": "sonnet", "cost_usd": 0.02},
            ],
        )
        assert len(result.usage_data) == 2
        total_cost = sum(u["cost_usd"] for u in result.usage_data)
        assert abs(total_cost - 0.03) < 0.001

    def test_verification_result_has_usage_data(self):
        from nightwire.autonomous.models import VerificationResult
        result = VerificationResult(
            passed=True,
            usage_data={
                "input_tokens": 50, "output_tokens": 25,
                "model": "haiku", "cost_usd": 0.001,
            },
        )
        assert result.usage_data["model"] == "haiku"

    def test_task_execution_result_usage_default_none(self):
        from nightwire.autonomous.models import TaskExecutionResult
        result = TaskExecutionResult(
            task_id=1,
            success=True,
            claude_output="done",
        )
        assert result.usage_data is None
