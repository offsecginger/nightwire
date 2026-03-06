"""Tests for M13: Bot Monitoring & Loop Resilience.

Covers:
    - WorkerStatus and CircuitBreakerState models
    - LoopStatus extended fields (worker_statuses, errors, circuit_breakers)
    - Worker tracking lifecycle in AutonomousLoop
    - /monitor command formatting
    - /worker list|stop|restart commands
    - Circuit breaker trip/reset/filtering
    - Stuck task runtime detection
    - reset_retry_count database method
    - Config properties for M13
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nightwire.autonomous.database import AutonomousDatabase
from nightwire.autonomous.executor import detect_task_type
from nightwire.autonomous.loop import AutonomousLoop, _WorkerInfo
from nightwire.autonomous.models import (
    CircuitBreakerState,
    LoopStatus,
    Task,
    TaskStatus,
    TaskType,
    WorkerStatus,
)
from nightwire.config import Config


# ============================================================
# Model Tests
# ============================================================


class TestWorkerStatusModel:
    def test_worker_status_fields(self):
        ws = WorkerStatus(
            task_id=42,
            task_title="Add auth endpoint",
            project_name="myproj",
            started_at=datetime.now(),
            elapsed_seconds=195.5,
            task_type="implementation",
            consecutive_type_failures=2,
        )
        assert ws.task_id == 42
        assert ws.task_title == "Add auth endpoint"
        assert ws.project_name == "myproj"
        assert ws.elapsed_seconds == 195.5
        assert ws.consecutive_type_failures == 2

    def test_worker_status_defaults(self):
        ws = WorkerStatus(
            task_id=1,
            task_title="Test",
            project_name="proj",
            started_at=datetime.now(),
        )
        assert ws.elapsed_seconds == 0.0
        assert ws.task_type == "implementation"
        assert ws.consecutive_type_failures == 0


class TestCircuitBreakerStateModel:
    def test_circuit_breaker_fields(self):
        cb = CircuitBreakerState(
            task_type="bug_fix",
            consecutive_failures=3,
            is_open=True,
            opened_at=datetime.now(),
            last_failure_at=datetime.now(),
        )
        assert cb.task_type == "bug_fix"
        assert cb.is_open is True
        assert cb.consecutive_failures == 3

    def test_circuit_breaker_defaults(self):
        cb = CircuitBreakerState(task_type="implementation")
        assert cb.consecutive_failures == 0
        assert cb.is_open is False
        assert cb.opened_at is None


class TestLoopStatusExtended:
    def test_loop_status_has_new_fields(self):
        status = LoopStatus(
            is_running=True,
            worker_statuses=[
                WorkerStatus(
                    task_id=1,
                    task_title="Test",
                    project_name="proj",
                    started_at=datetime.now(),
                ),
            ],
            total_errors=5,
            error_types={"TimeoutError": 3, "GitCommitError": 2},
            circuit_breakers=[
                CircuitBreakerState(
                    task_type="bug_fix", consecutive_failures=3, is_open=True,
                ),
            ],
        )
        assert len(status.worker_statuses) == 1
        assert status.total_errors == 5
        assert status.error_types["TimeoutError"] == 3
        assert len(status.circuit_breakers) == 1

    def test_loop_status_defaults_empty(self):
        status = LoopStatus()
        assert status.worker_statuses == []
        assert status.total_errors == 0
        assert status.error_types == {}
        assert status.circuit_breakers == []


# ============================================================
# Config Tests
# ============================================================


class TestM13Config:
    def test_stuck_task_timeout_default(self):
        config = Config.__new__(Config)
        config.settings = {}
        assert config.autonomous_stuck_task_timeout_minutes == 60

    def test_stuck_task_timeout_custom(self):
        config = Config.__new__(Config)
        config.settings = {"autonomous": {"stuck_task_timeout_minutes": 120}}
        assert config.autonomous_stuck_task_timeout_minutes == 120

    def test_circuit_breaker_threshold_default(self):
        config = Config.__new__(Config)
        config.settings = {}
        assert config.autonomous_circuit_breaker_threshold == 3

    def test_circuit_breaker_threshold_custom(self):
        config = Config.__new__(Config)
        config.settings = {"autonomous": {"circuit_breaker_threshold": 5}}
        assert config.autonomous_circuit_breaker_threshold == 5

    def test_circuit_breaker_reset_default(self):
        config = Config.__new__(Config)
        config.settings = {}
        assert config.autonomous_circuit_breaker_reset_minutes == 30

    def test_circuit_breaker_reset_custom(self):
        config = Config.__new__(Config)
        config.settings = {"autonomous": {"circuit_breaker_reset_minutes": 15}}
        assert config.autonomous_circuit_breaker_reset_minutes == 15


# ============================================================
# Database Tests
# ============================================================


class TestResetRetryCount:
    def test_reset_retry_count(self):
        """Test _reset_retry_count_sync with a minimal tasks table."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY, retry_count INTEGER DEFAULT 0)"
        )
        conn.execute("INSERT INTO tasks (id, retry_count) VALUES (1, 3)")
        conn.commit()

        db = AutonomousDatabase(conn)
        # Verify initial state
        row = conn.execute("SELECT retry_count FROM tasks WHERE id = 1").fetchone()
        assert row[0] == 3

        # Reset it
        db._reset_retry_count_sync(1)
        row = conn.execute("SELECT retry_count FROM tasks WHERE id = 1").fetchone()
        assert row[0] == 0
        conn.close()


# ============================================================
# Loop: Worker Tracking
# ============================================================


def _make_loop(**kwargs):
    """Create a test AutonomousLoop with mocked dependencies."""
    db = AsyncMock(spec=AutonomousDatabase)
    db.get_queued_task_count = AsyncMock(return_value=0)
    executor = MagicMock()
    defaults = dict(
        db=db,
        executor=executor,
        progress_callback=AsyncMock(),
        poll_interval=1,
        max_parallel=3,
    )
    defaults.update(kwargs)
    loop = AutonomousLoop(**defaults)
    return loop, db, executor


class TestWorkerTracking:
    async def test_get_status_includes_workers(self):
        loop, db, _ = _make_loop()
        loop._running = True
        loop._started_at = datetime.now()
        # Add a worker info entry
        loop._worker_info[42] = _WorkerInfo(
            task_id=42,
            task_title="Test task",
            project_name="myproj",
            task_type_value="implementation",
            started_at=datetime.now() - timedelta(seconds=30),
        )
        loop._active_task_ids.add(42)

        status = await loop.get_status()
        assert len(status.worker_statuses) == 1
        ws = status.worker_statuses[0]
        assert ws.task_id == 42
        assert ws.task_title == "Test task"
        assert ws.project_name == "myproj"
        assert ws.elapsed_seconds >= 29  # At least 29 seconds elapsed

    async def test_get_status_includes_error_counters(self):
        loop, db, _ = _make_loop()
        loop._running = True
        loop._started_at = datetime.now()
        loop._total_errors = 3
        loop._error_types = {"TimeoutError": 2, "OSError": 1}

        status = await loop.get_status()
        assert status.total_errors == 3
        assert status.error_types["TimeoutError"] == 2

    async def test_get_status_includes_circuit_breakers(self):
        loop, db, _ = _make_loop()
        loop._running = True
        loop._started_at = datetime.now()
        loop._circuit_breakers["bug_fix"] = CircuitBreakerState(
            task_type="bug_fix",
            consecutive_failures=3,
            is_open=True,
            opened_at=datetime.now(),
        )

        status = await loop.get_status()
        assert len(status.circuit_breakers) == 1
        assert status.circuit_breakers[0].task_type == "bug_fix"
        assert status.circuit_breakers[0].is_open is True

    async def test_get_status_excludes_zero_failure_breakers(self):
        loop, db, _ = _make_loop()
        loop._running = True
        loop._started_at = datetime.now()
        loop._circuit_breakers["implementation"] = CircuitBreakerState(
            task_type="implementation",
            consecutive_failures=0,
            is_open=False,
        )

        status = await loop.get_status()
        assert len(status.circuit_breakers) == 0

    async def test_cleanup_removes_worker_info(self):
        loop, _, _ = _make_loop()
        done_task = asyncio.Future()
        done_task.set_result(None)
        loop._active_workers[42] = done_task
        loop._active_task_ids.add(42)
        loop._worker_info[42] = _WorkerInfo(
            task_id=42,
            task_title="Done task",
            project_name="proj",
            task_type_value="implementation",
        )

        loop._cleanup_finished_workers()

        assert 42 not in loop._active_workers
        assert 42 not in loop._active_task_ids
        assert 42 not in loop._worker_info


# ============================================================
# Loop: Circuit Breaker
# ============================================================


class TestCircuitBreaker:
    def test_update_circuit_breaker_on_failure(self):
        loop, _, _ = _make_loop()
        with patch("nightwire.autonomous.loop.get_config") as mock_config:
            mock_config.return_value.autonomous_circuit_breaker_threshold = 3
            mock_config.return_value.allowed_numbers = []

            loop._update_circuit_breaker(TaskType.BUG_FIX, success=False)
            cb = loop._circuit_breakers["bug_fix"]
            assert cb.consecutive_failures == 1
            assert cb.is_open is False

    def test_circuit_breaker_trips_at_threshold(self):
        loop, _, _ = _make_loop()
        with patch("nightwire.autonomous.loop.get_config") as mock_config:
            mock_config.return_value.autonomous_circuit_breaker_threshold = 3
            mock_config.return_value.allowed_numbers = []

            for _ in range(3):
                loop._update_circuit_breaker(TaskType.BUG_FIX, success=False)

            cb = loop._circuit_breakers["bug_fix"]
            assert cb.consecutive_failures == 3
            assert cb.is_open is True
            assert cb.opened_at is not None

    def test_circuit_breaker_resets_on_success(self):
        loop, _, _ = _make_loop()
        with patch("nightwire.autonomous.loop.get_config") as mock_config:
            mock_config.return_value.autonomous_circuit_breaker_threshold = 3
            mock_config.return_value.allowed_numbers = []

            # Fail twice
            loop._update_circuit_breaker(TaskType.BUG_FIX, success=False)
            loop._update_circuit_breaker(TaskType.BUG_FIX, success=False)
            assert loop._circuit_breakers["bug_fix"].consecutive_failures == 2

            # Success resets
            loop._update_circuit_breaker(TaskType.BUG_FIX, success=True)
            assert loop._circuit_breakers["bug_fix"].consecutive_failures == 0
            assert loop._circuit_breakers["bug_fix"].is_open is False

    def test_circuit_breaker_auto_reset(self):
        loop, _, _ = _make_loop()
        with patch("nightwire.autonomous.loop.get_config") as mock_config:
            mock_config.return_value.autonomous_circuit_breaker_reset_minutes = 30

            # Create an open breaker that opened 31 minutes ago
            loop._circuit_breakers["bug_fix"] = CircuitBreakerState(
                task_type="bug_fix",
                consecutive_failures=3,
                is_open=True,
                opened_at=datetime.now() - timedelta(minutes=31),
            )

            loop._check_circuit_breaker_resets()

            cb = loop._circuit_breakers["bug_fix"]
            assert cb.is_open is False
            assert cb.consecutive_failures == 0

    def test_circuit_breaker_no_reset_before_period(self):
        loop, _, _ = _make_loop()
        with patch("nightwire.autonomous.loop.get_config") as mock_config:
            mock_config.return_value.autonomous_circuit_breaker_reset_minutes = 30

            loop._circuit_breakers["bug_fix"] = CircuitBreakerState(
                task_type="bug_fix",
                consecutive_failures=3,
                is_open=True,
                opened_at=datetime.now() - timedelta(minutes=10),
            )

            loop._check_circuit_breaker_resets()

            assert loop._circuit_breakers["bug_fix"].is_open is True

    def test_is_circuit_broken(self):
        loop, _, _ = _make_loop()
        task = Task(
            id=1,
            story_id=1,
            phone_number="+1",
            project_name="proj",
            title="Fix bug in auth",
            description="Fix the auth bug",
        )

        # No breakers — should not be broken
        assert loop._is_circuit_broken(task) is False

        # Add open breaker for bug_fix
        loop._circuit_breakers["bug_fix"] = CircuitBreakerState(
            task_type="bug_fix",
            consecutive_failures=3,
            is_open=True,
        )
        # Task title matches bug_fix keywords
        assert loop._is_circuit_broken(task) is True

    def test_is_circuit_broken_closed_breaker(self):
        loop, _, _ = _make_loop()
        task = Task(
            id=1,
            story_id=1,
            phone_number="+1",
            project_name="proj",
            title="Fix bug",
            description="Fix it",
        )
        loop._circuit_breakers["bug_fix"] = CircuitBreakerState(
            task_type="bug_fix",
            consecutive_failures=2,
            is_open=False,
        )
        assert loop._is_circuit_broken(task) is False


# ============================================================
# Loop: Error Recording
# ============================================================


class TestErrorRecording:
    def test_record_error(self):
        loop, _, _ = _make_loop()
        loop._record_error("TimeoutError")
        loop._record_error("TimeoutError")
        loop._record_error("OSError")

        assert loop._total_errors == 3
        assert loop._error_types["TimeoutError"] == 2
        assert loop._error_types["OSError"] == 1


# ============================================================
# Loop: Stop/Restart Worker
# ============================================================


class TestStopRestartWorker:
    async def test_stop_worker_cancels_task(self):
        loop, db, _ = _make_loop()
        # Create a non-done future as the worker
        worker = asyncio.Future()
        loop._active_workers[42] = worker

        result = await loop.stop_worker(42)
        assert result is True
        assert worker.cancelled()
        db.update_task_status.assert_called_once_with(
            42, TaskStatus.CANCELLED,
            error_message="Manually stopped via /worker stop",
        )

    async def test_stop_worker_not_found(self):
        loop, _, _ = _make_loop()
        result = await loop.stop_worker(99)
        assert result is False

    async def test_stop_worker_already_done(self):
        loop, _, _ = _make_loop()
        worker = asyncio.Future()
        worker.set_result(None)
        loop._active_workers[42] = worker

        result = await loop.stop_worker(42)
        assert result is False

    async def test_restart_task_success(self):
        loop, db, _ = _make_loop()
        failed_task = Task(
            id=42,
            story_id=1,
            phone_number="+1",
            project_name="proj",
            title="Failed task",
            description="It failed",
            status=TaskStatus.FAILED,
            error_message="Some error",
            retry_count=2,
        )
        db.get_task = AsyncMock(return_value=failed_task)

        error = await loop.restart_task(42)
        assert error is None
        db.reset_retry_count.assert_called_once_with(42)
        db.update_task_status.assert_called_once_with(
            42, TaskStatus.QUEUED, error_message=None,
        )

    async def test_restart_task_not_found(self):
        loop, db, _ = _make_loop()
        db.get_task = AsyncMock(return_value=None)

        error = await loop.restart_task(99)
        assert "not found" in error

    async def test_restart_task_not_terminal(self):
        loop, db, _ = _make_loop()
        running_task = Task(
            id=42,
            story_id=1,
            phone_number="+1",
            project_name="proj",
            title="Running task",
            description="Still running",
            status=TaskStatus.IN_PROGRESS,
        )
        db.get_task = AsyncMock(return_value=running_task)

        error = await loop.restart_task(42)
        assert "in_progress" in error

    async def test_restart_cancelled_task(self):
        loop, db, _ = _make_loop()
        cancelled_task = Task(
            id=42,
            story_id=1,
            phone_number="+1",
            project_name="proj",
            title="Cancelled task",
            description="Was cancelled",
            status=TaskStatus.CANCELLED,
        )
        db.get_task = AsyncMock(return_value=cancelled_task)

        error = await loop.restart_task(42)
        assert error is None  # Success


# ============================================================
# Loop: Stuck Task Detection
# ============================================================


class TestStuckTaskDetection:
    async def test_check_stuck_tasks_requeues(self):
        loop, db, _ = _make_loop()
        # Add worker info that started long ago
        loop._worker_info[42] = _WorkerInfo(
            task_id=42,
            task_title="Stuck task",
            project_name="proj",
            task_type_value="implementation",
            started_at=datetime.now() - timedelta(minutes=90),
        )

        # Create a non-done future as the worker
        worker = asyncio.Future()
        loop._active_workers[42] = worker

        stuck_task = Task(
            id=42,
            story_id=1,
            phone_number="+1234567890",
            project_name="proj",
            title="Stuck task",
            description="It got stuck",
            status=TaskStatus.IN_PROGRESS,
            retry_count=0,
            max_retries=2,
        )
        db.get_task = AsyncMock(return_value=stuck_task)

        with patch("nightwire.autonomous.loop.get_config") as mock_config:
            mock_config.return_value.autonomous_stuck_task_timeout_minutes = 60
            await loop._check_stuck_tasks()

        # Worker should be cancelled
        assert worker.cancelled()
        # Task should be re-queued
        db.increment_retry_count.assert_called_once_with(42)
        db.update_task_status.assert_called_once()

    async def test_check_stuck_tasks_fails_no_retries(self):
        loop, db, _ = _make_loop()
        loop._worker_info[42] = _WorkerInfo(
            task_id=42,
            task_title="Stuck task",
            project_name="proj",
            task_type_value="implementation",
            started_at=datetime.now() - timedelta(minutes=90),
        )
        worker = asyncio.Future()
        loop._active_workers[42] = worker

        stuck_task = Task(
            id=42,
            story_id=1,
            phone_number="+1234567890",
            project_name="proj",
            title="Stuck task",
            description="It got stuck",
            status=TaskStatus.IN_PROGRESS,
            retry_count=2,
            max_retries=2,
        )
        db.get_task = AsyncMock(return_value=stuck_task)

        with patch("nightwire.autonomous.loop.get_config") as mock_config:
            mock_config.return_value.autonomous_stuck_task_timeout_minutes = 60
            await loop._check_stuck_tasks()

        db.update_task_status.assert_called_once()
        call_args = db.update_task_status.call_args
        assert call_args[0][1] == TaskStatus.FAILED

    async def test_check_stuck_tasks_ignores_recent(self):
        loop, db, _ = _make_loop()
        loop._worker_info[42] = _WorkerInfo(
            task_id=42,
            task_title="Recent task",
            project_name="proj",
            task_type_value="implementation",
            started_at=datetime.now() - timedelta(minutes=5),
        )
        worker = asyncio.Future()
        loop._active_workers[42] = worker

        with patch("nightwire.autonomous.loop.get_config") as mock_config:
            mock_config.return_value.autonomous_stuck_task_timeout_minutes = 60
            await loop._check_stuck_tasks()

        # No DB calls — task is not stuck yet
        db.get_task.assert_not_called()
        assert not worker.cancelled()


# ============================================================
# Command Tests: /monitor
# ============================================================


def _make_handler_for_monitor():
    """Create a CoreCommandHandler with mocked autonomous manager."""
    from nightwire.commands.base import BotContext
    from nightwire.commands.core import CoreCommandHandler

    config = MagicMock()
    config.sandbox_enabled = False
    ctx = BotContext(
        config=config,
        runner=MagicMock(),
        project_manager=MagicMock(),
        memory=MagicMock(),
        memory_commands=MagicMock(),
        plugin_loader=MagicMock(),
        send_message=AsyncMock(),
        send_typing_indicator=AsyncMock(),
        task_manager=MagicMock(),
        get_memory_context=AsyncMock(return_value=None),
        nightwire_runner=None,
    )
    handler = CoreCommandHandler(ctx)
    return handler, ctx


class TestMonitorCommand:
    async def test_monitor_running(self):
        handler, ctx = _make_handler_for_monitor()
        ctx._autonomous_manager = MagicMock()
        ctx._autonomous_manager.get_loop_status = AsyncMock(
            return_value=LoopStatus(
                is_running=True,
                is_paused=False,
                max_parallel=3,
                tasks_queued=5,
                tasks_completed_today=12,
                tasks_failed_today=1,
                uptime_seconds=8100,
                worker_statuses=[
                    WorkerStatus(
                        task_id=42,
                        task_title="Add auth",
                        project_name="proj-A",
                        started_at=datetime.now(),
                        elapsed_seconds=200,
                    ),
                ],
                total_errors=3,
                error_types={"TimeoutError": 2, "GitCommitError": 1},
            ),
        )

        result = await handler.handle_monitor("+1", "")
        assert "RUNNING" in result
        assert "2h" in result
        assert "#42" in result
        assert "Add auth" in result
        assert "proj-A" in result
        assert "12 completed" in result
        assert "3 total" in result

    async def test_monitor_stopped(self):
        handler, ctx = _make_handler_for_monitor()
        ctx._autonomous_manager = MagicMock()
        ctx._autonomous_manager.get_loop_status = AsyncMock(
            return_value=LoopStatus(
                is_running=False,
                is_paused=False,
                max_parallel=3,
                uptime_seconds=0,
            ),
        )

        result = await handler.handle_monitor("+1", "")
        assert "STOPPED" in result

    async def test_monitor_paused(self):
        handler, ctx = _make_handler_for_monitor()
        ctx._autonomous_manager = MagicMock()
        ctx._autonomous_manager.get_loop_status = AsyncMock(
            return_value=LoopStatus(
                is_running=True,
                is_paused=True,
                max_parallel=3,
                uptime_seconds=600,
            ),
        )

        result = await handler.handle_monitor("+1", "")
        assert "PAUSED" in result

    async def test_monitor_with_circuit_breakers(self):
        handler, ctx = _make_handler_for_monitor()
        ctx._autonomous_manager = MagicMock()
        ctx._autonomous_manager.get_loop_status = AsyncMock(
            return_value=LoopStatus(
                is_running=True,
                max_parallel=3,
                uptime_seconds=600,
                circuit_breakers=[
                    CircuitBreakerState(
                        task_type="bug_fix",
                        consecutive_failures=3,
                        is_open=True,
                    ),
                ],
            ),
        )

        result = await handler.handle_monitor("+1", "")
        assert "Circuit Breakers" in result
        assert "bug_fix" in result

    async def test_monitor_not_available(self):
        handler, ctx = _make_handler_for_monitor()
        # autonomous_manager not set — RuntimeError
        result = await handler.handle_monitor("+1", "")
        assert "not available" in result


# ============================================================
# Command Tests: /worker
# ============================================================


class TestWorkerCommand:
    async def test_worker_no_args(self):
        handler, ctx = _make_handler_for_monitor()
        result = await handler.handle_worker("+1", "")
        assert "Usage" in result

    async def test_worker_unknown_subcommand(self):
        handler, ctx = _make_handler_for_monitor()
        result = await handler.handle_worker("+1", "foo")
        assert "Unknown subcommand" in result

    async def test_worker_list(self):
        handler, ctx = _make_handler_for_monitor()
        ctx._autonomous_manager = MagicMock()
        ctx._autonomous_manager.get_loop_status = AsyncMock(
            return_value=LoopStatus(
                is_running=True,
                max_parallel=3,
                worker_statuses=[
                    WorkerStatus(
                        task_id=42,
                        task_title="Task A",
                        project_name="proj",
                        started_at=datetime.now(),
                        elapsed_seconds=100,
                    ),
                ],
            ),
        )

        result = await handler.handle_worker("+1", "list")
        assert "#42" in result
        assert "Task A" in result

    async def test_worker_stop_success(self):
        handler, ctx = _make_handler_for_monitor()
        ctx._autonomous_manager = MagicMock()
        ctx._autonomous_manager.stop_worker = AsyncMock(return_value=True)

        result = await handler.handle_worker("+1", "stop 42")
        assert "stopped" in result

    async def test_worker_stop_not_found(self):
        handler, ctx = _make_handler_for_monitor()
        ctx._autonomous_manager = MagicMock()
        ctx._autonomous_manager.stop_worker = AsyncMock(return_value=False)

        result = await handler.handle_worker("+1", "stop 99")
        assert "No active worker" in result

    async def test_worker_stop_invalid_id(self):
        handler, ctx = _make_handler_for_monitor()
        result = await handler.handle_worker("+1", "stop abc")
        assert "Invalid task ID" in result

    async def test_worker_restart_success(self):
        handler, ctx = _make_handler_for_monitor()
        ctx._autonomous_manager = MagicMock()
        ctx._autonomous_manager.restart_task = AsyncMock(return_value=None)

        result = await handler.handle_worker("+1", "restart 42")
        assert "restarted" in result

    async def test_worker_restart_error(self):
        handler, ctx = _make_handler_for_monitor()
        ctx._autonomous_manager = MagicMock()
        ctx._autonomous_manager.restart_task = AsyncMock(
            return_value="Task #42 is in_progress",
        )

        result = await handler.handle_worker("+1", "restart 42")
        assert "in_progress" in result

    async def test_worker_stop_no_id(self):
        handler, ctx = _make_handler_for_monitor()
        result = await handler.handle_worker("+1", "stop")
        assert "Usage" in result


# ============================================================
# BUILTIN_COMMANDS
# ============================================================


class TestBuiltinCommands:
    def test_monitor_in_builtin_commands(self):
        from nightwire.commands.base import BUILTIN_COMMANDS

        assert "monitor" in BUILTIN_COMMANDS
        assert "worker" in BUILTIN_COMMANDS


# ============================================================
# Format Duration Helper
# ============================================================


class TestFormatDuration:
    def test_seconds(self):
        from nightwire.commands.core import CoreCommandHandler

        assert CoreCommandHandler._format_duration(45) == "45s"

    def test_minutes(self):
        from nightwire.commands.core import CoreCommandHandler

        assert CoreCommandHandler._format_duration(135) == "2m 15s"

    def test_hours(self):
        from nightwire.commands.core import CoreCommandHandler

        assert CoreCommandHandler._format_duration(8100) == "2h 15m"
