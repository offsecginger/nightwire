"""Tests for M10: Signal UX Improvements.

Covers:
    - MessageQueue: enqueue, rate limiting, retry, consumer lifecycle, shutdown
    - Typing indicators: send/clear via MessageQueue
    - Bot integration: queue wiring, typing indicator callbacks
    - Autonomous debounce: buffered notifications, flush, timer reset
    - Config properties: signal_send_*, signal_notification_debounce_seconds
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nightwire.autonomous.loop import AutonomousLoop
from nightwire.config import Config
from nightwire.message_queue import MessageQueue


# ============================================================
# Helpers
# ============================================================


def _make_config(**overrides) -> Config:
    """Create a Config with sensible defaults for testing."""
    c = Config.__new__(Config)
    c.settings = {
        "signal_send_rate_per_second": 100.0,
        "signal_send_timeout_seconds": 5,
        "signal_send_max_retries": 3,
        "signal_notification_debounce_seconds": 2.0,
        **overrides,
    }
    return c


def _make_session(status: int = 201, text: str = "") -> MagicMock:
    """Create a mock aiohttp session returning given status."""
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    session.put = MagicMock(return_value=ctx)
    session.delete = MagicMock(return_value=ctx)
    return session


def _make_loop(
    debounce_seconds: float = 0.1,
    progress_callback=None,
) -> AutonomousLoop:
    """Create an AutonomousLoop with mocks for debounce testing."""
    db = MagicMock()
    executor = MagicMock()
    cb = progress_callback or AsyncMock()
    loop = AutonomousLoop(
        db=db,
        executor=executor,
        progress_callback=cb,
        debounce_seconds=debounce_seconds,
    )
    return loop


# ============================================================
# MessageQueue — Enqueue & Consumer
# ============================================================


class TestMessageQueueEnqueue:
    async def test_enqueue_creates_consumer(self):
        session = _make_session()
        mq = MessageQueue(session, _make_config(), "http://localhost:8080", "+1234")
        await mq.enqueue("+5678", "Hello")
        # Consumer should have been created
        assert "+5678" in mq._consumers
        assert not mq._consumers["+5678"].done()
        await mq.close()

    async def test_enqueue_delivers_message(self):
        session = _make_session()
        mq = MessageQueue(session, _make_config(), "http://localhost:8080", "+1234")
        await mq.enqueue("+5678", "Hello")
        await asyncio.sleep(0.1)
        session.post.assert_called()
        call_kwargs = session.post.call_args
        assert call_kwargs[1]["json"]["message"] == "Hello"
        assert call_kwargs[1]["json"]["recipients"] == ["+5678"]
        await mq.close()

    async def test_enqueue_multiple_recipients_isolated(self):
        session = _make_session()
        mq = MessageQueue(session, _make_config(), "http://localhost:8080", "+1234")
        await mq.enqueue("+5678", "Msg1")
        await mq.enqueue("+9999", "Msg2")
        assert "+5678" in mq._consumers
        assert "+9999" in mq._consumers
        assert mq._consumers["+5678"] is not mq._consumers["+9999"]
        await mq.close()

    async def test_dead_consumer_replaced(self):
        session = _make_session()
        mq = MessageQueue(session, _make_config(), "http://localhost:8080", "+1234")
        await mq.enqueue("+5678", "First")
        await asyncio.sleep(0.1)
        # Wait for consumer to idle out (30s timeout — but we'll close and re-enqueue)
        first_consumer = mq._consumers.get("+5678")
        # Force consumer to be done by cancelling
        if first_consumer and not first_consumer.done():
            first_consumer.cancel()
            try:
                await first_consumer
            except asyncio.CancelledError:
                pass
        # Re-enqueue should create new consumer
        await mq.enqueue("+5678", "Second")
        assert "+5678" in mq._consumers
        assert mq._consumers["+5678"] is not first_consumer
        await mq.close()


# ============================================================
# MessageQueue — Rate Limiting
# ============================================================


class TestMessageQueueRateLimiting:
    async def test_rate_limit_enforces_interval(self):
        """With low rate, second message should be delayed."""
        config = _make_config(signal_send_rate_per_second=2.0)
        session = _make_session()
        mq = MessageQueue(session, config, "http://localhost:8080", "+1234")
        await mq.enqueue("+5678", "Msg1")
        await mq.enqueue("+5678", "Msg2")
        # Wait for both to be processed
        await asyncio.sleep(1.0)
        assert session.post.call_count >= 2
        await mq.close()

    async def test_high_rate_no_delay(self):
        """High rate limit should process quickly."""
        config = _make_config(signal_send_rate_per_second=1000.0)
        session = _make_session()
        mq = MessageQueue(session, config, "http://localhost:8080", "+1234")
        await mq.enqueue("+5678", "Msg1")
        await mq.enqueue("+5678", "Msg2")
        await mq.enqueue("+5678", "Msg3")
        await asyncio.sleep(0.2)
        assert session.post.call_count == 3
        await mq.close()


# ============================================================
# MessageQueue — Retry
# ============================================================


class TestMessageQueueRetry:
    async def test_retry_on_non_201(self):
        """All retries exhausted on persistent failure."""
        session = _make_session(status=500, text="Server Error")
        config = _make_config(signal_send_max_retries=2)
        mq = MessageQueue(session, config, "http://localhost:8080", "+1234")
        result = await mq._send_with_retry("+5678", "Hello")
        assert result is False
        assert session.post.call_count == 2

    async def test_retry_success_on_second_attempt(self):
        """First attempt fails, second succeeds."""
        resp_fail = AsyncMock()
        resp_fail.status = 500
        resp_fail.text = AsyncMock(return_value="error")
        resp_ok = AsyncMock()
        resp_ok.status = 201

        responses = [resp_fail, resp_ok]

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=responses)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=ctx)

        config = _make_config(signal_send_max_retries=3)
        mq = MessageQueue(session, config, "http://localhost:8080", "+1234")
        result = await mq._send_with_retry("+5678", "Hello")
        assert result is True
        assert session.post.call_count == 2

    async def test_retry_exhaustion_returns_false(self):
        session = _make_session(status=500, text="error")
        config = _make_config(signal_send_max_retries=2)
        mq = MessageQueue(session, config, "http://localhost:8080", "+1234")
        result = await mq._send_with_retry("+5678", "Hello")
        assert result is False

    async def test_retry_on_timeout_exception(self):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=ctx)

        config = _make_config(signal_send_max_retries=2)
        mq = MessageQueue(session, config, "http://localhost:8080", "+1234")
        result = await mq._send_with_retry("+5678", "Hello")
        assert result is False
        assert session.post.call_count == 2


# ============================================================
# MessageQueue — Typing Indicators
# ============================================================


class TestTypingIndicators:
    async def test_send_typing_start(self):
        session = _make_session()
        mq = MessageQueue(session, _make_config(), "http://localhost:8080", "+1234")
        await mq.send_typing_indicator("+5678", typing=True)
        session.put.assert_called_once()
        url = session.put.call_args[0][0]
        assert "/v1/typing-indicator/" in url

    async def test_send_typing_clear(self):
        session = _make_session()
        mq = MessageQueue(session, _make_config(), "http://localhost:8080", "+1234")
        await mq.send_typing_indicator("+5678", typing=False)
        session.delete.assert_called_once()
        url = session.delete.call_args[0][0]
        assert "/v1/typing-indicator/" in url

    async def test_typing_indicator_swallows_exceptions(self):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("connection failed"))
        ctx.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.put = MagicMock(return_value=ctx)

        mq = MessageQueue(session, _make_config(), "http://localhost:8080", "+1234")
        # Should not raise
        await mq.send_typing_indicator("+5678", typing=True)


# ============================================================
# MessageQueue — Shutdown
# ============================================================


class TestMessageQueueShutdown:
    async def test_close_drains_and_stops(self):
        session = _make_session()
        mq = MessageQueue(session, _make_config(), "http://localhost:8080", "+1234")
        await mq.enqueue("+5678", "Hello")
        await asyncio.sleep(0.1)
        await mq.close()
        assert mq._running is False

    async def test_close_sets_running_false(self):
        """close() sets _running to False so consumers exit."""
        session = _make_session()
        mq = MessageQueue(session, _make_config(), "http://localhost:8080", "+1234")
        await mq.enqueue("+5678", "Hello")
        await asyncio.sleep(0.1)
        assert mq._running is True
        await mq.close()
        assert mq._running is False


# ============================================================
# Bot Integration
# ============================================================


class TestBotIntegration:
    def test_bot_context_has_typing_indicator(self):
        """BotContext accepts send_typing_indicator field."""
        from nightwire.commands.base import BotContext

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
        assert ctx.send_typing_indicator is not None

    def test_task_manager_accepts_typing_indicator(self):
        """TaskManager constructor takes send_typing_indicator."""
        from nightwire.task_manager import TaskManager

        tm = TaskManager(
            runner=MagicMock(),
            project_manager=MagicMock(),
            memory=MagicMock(),
            config=MagicMock(),
            send_message=AsyncMock(),
            send_typing_indicator=AsyncMock(),
            get_memory_context=AsyncMock(),
        )
        assert tm._send_typing_indicator is not None

    async def test_task_manager_typing_on_task_start(self):
        """TaskManager sends typing indicator when background task starts."""
        from nightwire.task_manager import TaskManager

        typing_mock = AsyncMock()
        runner = MagicMock()
        runner.run_claude = AsyncMock(return_value=(True, "Result"))
        runner.last_session_id = None
        runner.last_usage = None

        pm = MagicMock()
        pm.get_current_path.return_value = "/tmp/proj"

        memory = MagicMock()
        memory.store_message = AsyncMock()

        config = MagicMock()
        config.claude_timeout = 60

        tm = TaskManager(
            runner=runner,
            project_manager=pm,
            memory=memory,
            config=config,
            send_message=AsyncMock(),
            send_typing_indicator=typing_mock,
            get_memory_context=AsyncMock(return_value=None),
        )

        tm.start_background_task(
            sender="+1234",
            task_description="test prompt",
            project_name="proj",
        )
        # Give the background task time to start and finish
        await asyncio.sleep(0.5)

        # Typing indicator should have been called with True (start)
        typing_mock.assert_any_call("+1234", True)
        # Typing indicator should have been called with False (clear)
        typing_mock.assert_any_call("+1234", False)


# ============================================================
# Autonomous Debounce — Buffering
# ============================================================


class TestAutonomousDebounce:
    async def test_debounced_notification_buffered(self):
        """Debounced messages are not sent immediately."""
        cb = AsyncMock()
        loop = _make_loop(debounce_seconds=1.0, progress_callback=cb)
        await loop._notify_debounced("+1234", "Starting task")
        # Should NOT have been sent yet
        cb.assert_not_called()
        assert "+1234" in loop._notification_buffer
        assert len(loop._notification_buffer["+1234"]) == 1

        # Cancel timer to prevent test cleanup issues
        for timer in loop._notification_timers.values():
            timer.cancel()

    async def test_debounced_flushes_after_delay(self):
        """Buffered messages flush after debounce window."""
        cb = AsyncMock()
        loop = _make_loop(debounce_seconds=0.1, progress_callback=cb)
        await loop._notify_debounced("+1234", "Starting task")
        # Wait for debounce window
        await asyncio.sleep(0.3)
        cb.assert_called_once()
        assert "Starting task" in cb.call_args[0][1]
        assert "+1234" not in loop._notification_buffer

    async def test_debounced_batches_multiple_messages(self):
        """Multiple debounced messages combined into one."""
        cb = AsyncMock()
        loop = _make_loop(debounce_seconds=0.2, progress_callback=cb)
        await loop._notify_debounced("+1234", "Starting task A")
        await loop._notify_debounced("+1234", "Starting task B")
        await loop._notify_debounced("+1234", "Task deferred: C")
        await asyncio.sleep(0.4)
        cb.assert_called_once()
        combined = cb.call_args[0][1]
        assert "Starting task A" in combined
        assert "Starting task B" in combined
        assert "Task deferred: C" in combined
        assert "\n---\n" in combined

    async def test_debounce_timer_resets_on_new_message(self):
        """Adding a message resets the debounce timer."""
        cb = AsyncMock()
        loop = _make_loop(debounce_seconds=0.2, progress_callback=cb)
        await loop._notify_debounced("+1234", "Msg1")
        await asyncio.sleep(0.1)  # Half the window
        await loop._notify_debounced("+1234", "Msg2")  # Reset timer
        await asyncio.sleep(0.15)  # Past original window but not reset
        cb.assert_not_called()  # Timer was reset, so not yet flushed
        await asyncio.sleep(0.15)  # Now past reset window
        cb.assert_called_once()
        combined = cb.call_args[0][1]
        assert "Msg1" in combined
        assert "Msg2" in combined

    async def test_different_recipients_independent(self):
        """Debounce buffers are per-recipient."""
        cb = AsyncMock()
        loop = _make_loop(debounce_seconds=0.1, progress_callback=cb)
        await loop._notify_debounced("+1234", "Msg for A")
        await loop._notify_debounced("+5678", "Msg for B")
        await asyncio.sleep(0.3)
        assert cb.call_count == 2
        recipients = {call[0][0] for call in cb.call_args_list}
        assert "+1234" in recipients
        assert "+5678" in recipients

    async def test_critical_notify_bypasses_debounce(self):
        """_notify() sends immediately, not buffered."""
        cb = AsyncMock()
        loop = _make_loop(debounce_seconds=10.0, progress_callback=cb)
        await loop._notify("+1234", "Task COMPLETED")
        cb.assert_called_once_with("+1234", "Task COMPLETED")

    async def test_flush_all_on_stop(self):
        """stop() flushes all pending notifications."""
        cb = AsyncMock()
        loop = _make_loop(debounce_seconds=10.0, progress_callback=cb)
        loop._running = True
        await loop._notify_debounced("+1234", "Pending A")
        await loop._notify_debounced("+5678", "Pending B")
        # Nothing sent yet (10s debounce)
        cb.assert_not_called()
        # Stop flushes all
        await loop.stop()
        assert cb.call_count == 2

    async def test_flush_empty_buffer_noop(self):
        """Flushing an empty buffer doesn't send anything."""
        cb = AsyncMock()
        loop = _make_loop(progress_callback=cb)
        await loop._flush_notifications("+1234")
        cb.assert_not_called()


# ============================================================
# Config Properties
# ============================================================


class TestM10ConfigProperties:
    def test_signal_send_rate_default(self):
        config = _make_config()
        del config.settings["signal_send_rate_per_second"]
        assert config.signal_send_rate_per_second == 1.0

    def test_signal_send_rate_custom(self):
        config = _make_config(signal_send_rate_per_second=5.0)
        assert config.signal_send_rate_per_second == 5.0

    def test_signal_send_timeout_default(self):
        config = _make_config()
        del config.settings["signal_send_timeout_seconds"]
        assert config.signal_send_timeout_seconds == 10

    def test_signal_send_max_retries_default(self):
        config = _make_config()
        del config.settings["signal_send_max_retries"]
        assert config.signal_send_max_retries == 3

    def test_signal_notification_debounce_default(self):
        config = _make_config()
        del config.settings["signal_notification_debounce_seconds"]
        assert config.signal_notification_debounce_seconds == 5.0

    def test_signal_notification_debounce_custom(self):
        config = _make_config(signal_notification_debounce_seconds=10.0)
        assert config.signal_notification_debounce_seconds == 10.0
