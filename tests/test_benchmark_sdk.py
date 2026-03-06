"""CLI subprocess runner validation tests.

Behavioral tests validate the Claude CLI subprocess runner:
non-streaming and structured output happy paths, streaming batch
intervals, buffer flush, cancel responsiveness, progress heartbeats,
callback error resilience, and classify_error() coverage.

Optional real-CLI tests run when NIGHTWIRE_BENCHMARK=1 is set:
    NIGHTWIRE_BENCHMARK=1 pytest tests/test_benchmark_sdk.py -v
"""

import asyncio
import json
import os
import time
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from nightwire.claude_runner import (
    STREAM_SEND_INTERVAL,
    ClaudeRunner,
    classify_error,
)
from nightwire.exceptions import ErrorCategory

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


async def _mock_subprocess(
    stdout_bytes, stderr_bytes=b"", returncode=0,
):
    """Create a mock async subprocess with given outputs."""
    mock = AsyncMock()
    mock.communicate = AsyncMock(
        return_value=(stdout_bytes, stderr_bytes),
    )
    mock.returncode = returncode
    mock.kill = MagicMock()
    mock.wait = AsyncMock()
    mock.stdin = MagicMock()
    mock.stdin.write = MagicMock()
    mock.stdin.drain = AsyncMock()
    mock.stdin.close = MagicMock()
    mock.stdout = AsyncMock()
    mock.stderr = AsyncMock()
    mock.stderr.read = AsyncMock(return_value=stderr_bytes)
    return mock


def _make_cli_response(
    result: str = "Hello!",
    is_error: bool = False,
    input_tokens: int = 10,
    output_tokens: int = 34,
    structured_output: dict = None,
) -> bytes:
    """Build a CLI JSON response (non-streaming format)."""
    resp = {
        "type": "result",
        "subtype": "success" if not is_error else "error",
        "is_error": is_error,
        "duration_ms": 4791,
        "num_turns": 1,
        "result": result,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "modelUsage": {
            "claude-sonnet-4-5": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "costUSD": 0.017,
            }
        },
    }
    if structured_output is not None:
        resp["structured_output"] = structured_output
    return json.dumps(resp).encode("utf-8")


def _make_ndjson_lines(
    text_chunks: List[str],
    final_result: str = None,
) -> List[bytes]:
    """Build NDJSON lines for streaming output.

    Returns list of newline-terminated byte lines matching the
    ``--output-format stream-json --verbose`` CLI format.
    """
    lines = []
    # Init event
    lines.append(
        json.dumps({
            "type": "system",
            "subtype": "init",
            "session_id": "test-session",
        }).encode() + b"\n"
    )
    # Text chunks as assistant messages
    for chunk in text_chunks:
        lines.append(
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": chunk}
                    ]
                },
            }).encode() + b"\n"
        )
    # Final result event
    full_text = (
        final_result if final_result is not None
        else "".join(text_chunks)
    )
    lines.append(
        json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": full_text,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 34,
            },
            "modelUsage": {
                "claude-sonnet-4-5": {
                    "inputTokens": 10,
                    "outputTokens": 34,
                    "costUSD": 0.017,
                }
            },
        }).encode() + b"\n"
    )
    return lines


class MockStreamingStdout:
    """Simulates async readline() from a subprocess stdout pipe.

    Yields pre-built NDJSON lines with optional inter-line delay,
    then returns b"" to signal EOF.
    """

    def __init__(
        self,
        lines: List[bytes],
        line_delay: float = 0.0,
    ):
        self._lines = list(lines)
        self._line_delay = line_delay
        self._index = 0

    async def readline(self):
        if self._index >= len(self._lines):
            return b""
        if self._line_delay > 0:
            await asyncio.sleep(self._line_delay)
        line = self._lines[self._index]
        self._index += 1
        return line


# ---------------------------------------------------------------------------
# Test Pydantic model for structured output test
# ---------------------------------------------------------------------------


class SimpleResponse(BaseModel):
    """Simple model for testing run_claude_structured."""

    answer: str
    confidence: float


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_config(tmp_path):
    """Create a mock config object with CLI-relevant settings."""
    cfg = MagicMock()
    cfg.claude_path = "claude"
    cfg.claude_model = "claude-sonnet-4-5"
    cfg.claude_timeout = 60
    cfg.settings = {}
    # config_dir / "CLAUDE.md" must not exist to skip
    # --append-system-prompt-file flag
    cfg.config_dir = tmp_path / "config"
    cfg.config_dir.mkdir(exist_ok=True)
    return cfg


@pytest.fixture
def runner(tmp_path):
    """Create a ClaudeRunner with mocked config."""
    with patch(
        "nightwire.claude_runner.get_config"
    ) as mock_get_config:
        cfg = _make_mock_config(tmp_path)
        mock_get_config.return_value = cfg

        r = ClaudeRunner()
        r.current_project = tmp_path
        yield r


@pytest.fixture(autouse=True)
def mock_cooldown():
    """Mock cooldown manager as inactive for all tests."""
    with patch(
        "nightwire.rate_limit_cooldown.get_cooldown_manager"
    ) as mock:
        mgr = MagicMock()
        mgr.is_active = False
        mock.return_value = mgr
        yield mgr


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------


@patch("asyncio.create_subprocess_exec")
async def test_nonstreaming_happy_path(
    mock_exec, runner,
):
    """Non-streaming run_claude returns (True, result text)."""
    stdout = _make_cli_response(result="The answer is 42.")
    proc = await _mock_subprocess(stdout)
    mock_exec.return_value = proc

    success, output = await runner.run_claude(
        "What is the answer?",
        stream=False,
        timeout=30,
    )

    assert success is True
    assert output == "The answer is 42."
    mock_exec.assert_called_once()


@patch("asyncio.create_subprocess_exec")
async def test_structured_output_happy_path(
    mock_exec, runner,
):
    """run_claude_structured parses structured_output field."""
    structured = {"answer": "Paris", "confidence": 0.95}
    stdout = _make_cli_response(
        result=json.dumps(structured),
        structured_output=structured,
    )
    proc = await _mock_subprocess(stdout)
    mock_exec.return_value = proc

    success, result = await runner.run_claude_structured(
        "What is the capital of France?",
        response_model=SimpleResponse,
        timeout=30,
    )

    assert success is True
    assert isinstance(result, SimpleResponse)
    assert result.answer == "Paris"
    assert result.confidence == 0.95


@patch("asyncio.create_subprocess_exec")
async def test_structured_output_fallback_to_result_text(
    mock_exec, runner,
):
    """run_claude_structured falls back to parsing result text
    when structured_output is missing."""
    data = {"answer": "Berlin", "confidence": 0.88}
    stdout = _make_cli_response(
        result=json.dumps(data),
        structured_output=None,
    )
    proc = await _mock_subprocess(stdout)
    mock_exec.return_value = proc

    success, result = await runner.run_claude_structured(
        "What is the capital of Germany?",
        response_model=SimpleResponse,
        timeout=30,
    )

    assert success is True
    assert isinstance(result, SimpleResponse)
    assert result.answer == "Berlin"
    assert result.confidence == 0.88


# ---------------------------------------------------------------------------
# Streaming behavioral tests
# ---------------------------------------------------------------------------


@patch("asyncio.create_subprocess_exec")
async def test_streaming_batch_timing(mock_exec, runner):
    """Streaming batches fire at approximately STREAM_SEND_INTERVAL.

    60 chunks at 100ms apart = ~6s total. Expect batched callbacks
    near 2s, 4s, 6s (plus final flush).
    """
    chunks = ["abcdefghij"] * 60
    ndjson = _make_ndjson_lines(chunks)
    stdout_mock = MockStreamingStdout(
        ndjson, line_delay=0.1,
    )

    proc = AsyncMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdout = stdout_mock
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b"")
    proc.returncode = 0
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    mock_exec.return_value = proc

    callback_times = []
    start = time.monotonic()

    async def progress_cb(msg: str):
        callback_times.append(time.monotonic() - start)

    success, output = await runner.run_claude(
        "test prompt",
        progress_callback=progress_cb,
        stream=True,
        timeout=30,
    )

    assert success is True
    assert output == "".join(chunks)
    # Expect multiple batched callbacks
    assert len(callback_times) >= 2
    # First batch should fire near STREAM_SEND_INTERVAL
    assert callback_times[0] >= STREAM_SEND_INTERVAL - 1.0
    assert callback_times[0] <= STREAM_SEND_INTERVAL + 1.5


@patch("asyncio.create_subprocess_exec")
async def test_streaming_flush_on_complete(
    mock_exec, runner,
):
    """Remaining buffer is flushed when stream ends."""
    chunks = ["0123456789", "abcdefghij", "ABCDEFGHIJ"]
    ndjson = _make_ndjson_lines(chunks)
    stdout_mock = MockStreamingStdout(ndjson, line_delay=0.0)

    proc = AsyncMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdout = stdout_mock
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b"")
    proc.returncode = 0
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    mock_exec.return_value = proc

    received = []

    async def progress_cb(msg: str):
        received.append(msg)

    success, output = await runner.run_claude(
        "test prompt",
        progress_callback=progress_cb,
        stream=True,
        timeout=30,
    )

    assert success is True
    assert output == "".join(chunks)
    # Buffer should have been flushed at least once
    assert len(received) >= 1
    assert "".join(received) == "".join(chunks)


@patch("asyncio.create_subprocess_exec")
async def test_cancel_responsiveness(mock_exec, runner):
    """cancel() interrupts a streaming request promptly."""

    class InfiniteStdout:
        """Stdout that yields chunks forever until killed."""

        def __init__(self):
            self._stopped = False

        async def readline(self):
            if self._stopped:
                return b""
            await asyncio.sleep(0.05)
            line = json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "x" * 10}
                    ]
                },
            })
            return line.encode() + b"\n"

        def stop(self):
            self._stopped = True

    infinite_stdout = InfiniteStdout()

    proc = AsyncMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdout = infinite_stdout
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b"")
    proc.returncode = None
    proc.wait = AsyncMock()

    def fake_kill():
        proc.returncode = -9
        infinite_stdout.stop()

    proc.kill = MagicMock(side_effect=fake_kill)
    mock_exec.return_value = proc

    async def progress_cb(msg: str):
        pass

    cancel_start = None

    async def run_and_cancel():
        nonlocal cancel_start
        task = asyncio.create_task(
            runner.run_claude(
                "test prompt",
                progress_callback=progress_cb,
                stream=True,
                timeout=30,
            )
        )
        await asyncio.sleep(0.3)  # let stream start
        cancel_start = time.monotonic()
        await runner.cancel()
        return await task

    success, output = await run_and_cancel()
    cancel_elapsed = time.monotonic() - cancel_start

    assert success is False
    assert "cancelled" in output.lower()
    assert cancel_elapsed < 2.0
    proc.kill.assert_called()


@patch("asyncio.create_subprocess_exec")
async def test_progress_heartbeat_timing(
    mock_exec, runner,
):
    """Non-streaming heartbeat fires at PROGRESS_UPDATE_INTERVAL."""
    stdout = _make_cli_response(result="Done")

    async def slow_communicate(input=None):
        await asyncio.sleep(5.0)
        return (stdout, b"")

    proc = AsyncMock()
    proc.communicate = slow_communicate
    proc.returncode = 0
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    mock_exec.return_value = proc

    heartbeats = []
    start = time.monotonic()

    async def progress_cb(msg: str):
        heartbeats.append(
            (time.monotonic() - start, msg)
        )

    with patch(
        "nightwire.claude_runner.PROGRESS_UPDATE_INTERVAL",
        2,
    ):
        success, output = await runner.run_claude(
            "test prompt",
            progress_callback=progress_cb,
            stream=False,
            timeout=30,
        )

    assert success is True
    assert output == "Done"
    # With 5s delay and 2s interval, expect ~2 heartbeats
    assert len(heartbeats) >= 1
    assert any(
        "still working" in msg.lower()
        for _, msg in heartbeats
    )


@patch("asyncio.create_subprocess_exec")
async def test_streaming_callback_error_resilience(
    mock_exec, runner,
):
    """Stream completes even if progress_callback raises."""
    chunks = ["hello ", "world ", "test "]
    ndjson = _make_ndjson_lines(chunks)
    stdout_mock = MockStreamingStdout(ndjson, line_delay=0.0)

    proc = AsyncMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdout = stdout_mock
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b"")
    proc.returncode = 0
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    mock_exec.return_value = proc

    async def failing_cb(msg: str):
        raise ConnectionError("Signal send failed")

    success, output = await runner.run_claude(
        "test prompt",
        progress_callback=failing_cb,
        stream=True,
        timeout=30,
    )

    assert success is True
    assert output == "".join(chunks)


# ---------------------------------------------------------------------------
# classify_error() tests
# ---------------------------------------------------------------------------


class TestClassifyError:
    """Tests for CLI exit code + output text error classification."""

    def test_rate_limit_with_usage_limit(self):
        """'rate limit' + 'usage limit' -> RATE_LIMITED."""
        cat = classify_error(
            1,
            "rate limit exceeded - usage limit reached",
            "",
        )
        assert cat == ErrorCategory.RATE_LIMITED

    def test_rate_limit_with_quota_exceeded(self):
        """'rate limit' + 'quota exceeded' -> RATE_LIMITED."""
        cat = classify_error(
            1,
            "429 rate limit - quota exceeded",
            "",
        )
        assert cat == ErrorCategory.RATE_LIMITED

    def test_rate_limit_bare_is_transient(self):
        """'rate limit' alone (no subscription pattern) -> TRANSIENT."""
        cat = classify_error(
            1, "rate limit exceeded", "",
        )
        assert cat == ErrorCategory.TRANSIENT

    def test_authentication_error(self):
        """'invalid api key' -> PERMANENT."""
        cat = classify_error(
            1, "", "invalid api key provided",
        )
        assert cat == ErrorCategory.PERMANENT

    def test_authentication_keyword(self):
        """'authentication' in output -> PERMANENT."""
        cat = classify_error(
            1, "authentication failed", "",
        )
        assert cat == ErrorCategory.PERMANENT

    def test_permission_denied(self):
        """'permission denied' -> PERMANENT."""
        cat = classify_error(
            1, "permission denied for resource", "",
        )
        assert cat == ErrorCategory.PERMANENT

    def test_prompt_too_long(self):
        """'prompt is too long' -> PERMANENT."""
        cat = classify_error(
            1, "prompt is too long", "",
        )
        assert cat == ErrorCategory.PERMANENT

    def test_timed_out(self):
        """'timed out' -> TRANSIENT."""
        cat = classify_error(
            1, "request timed out", "",
        )
        assert cat == ErrorCategory.TRANSIENT

    def test_timeout_keyword(self):
        """'timeout' in stderr -> TRANSIENT."""
        cat = classify_error(
            1, "", "connection timeout",
        )
        assert cat == ErrorCategory.TRANSIENT

    def test_server_error_500(self):
        """'500' in output -> TRANSIENT."""
        cat = classify_error(
            1, "server error 500", "",
        )
        assert cat == ErrorCategory.TRANSIENT

    def test_server_error_502(self):
        """'502' in output -> TRANSIENT."""
        cat = classify_error(
            1, "bad gateway 502", "",
        )
        assert cat == ErrorCategory.TRANSIENT

    def test_connection_reset(self):
        """'connection' + 'reset' -> TRANSIENT."""
        cat = classify_error(
            1, "connection reset by peer", "",
        )
        assert cat == ErrorCategory.TRANSIENT

    def test_infrastructure_exit_127(self):
        """Exit code 127 (command not found) -> INFRASTRUCTURE."""
        cat = classify_error(127, "", "command not found")
        assert cat == ErrorCategory.INFRASTRUCTURE

    def test_nonzero_empty_stderr_is_permanent(self):
        """Non-zero exit + empty stderr -> PERMANENT (nothing to retry)."""
        cat = classify_error(1, "", "")
        assert cat == ErrorCategory.PERMANENT

    def test_signal_killed_is_permanent(self):
        """Exit code -9 (SIGKILL) -> PERMANENT (process was killed)."""
        cat = classify_error(-9, "", "")
        assert cat == ErrorCategory.PERMANENT

    def test_signal_terminated_is_permanent(self):
        """Exit code 137 (128+SIGKILL) -> PERMANENT (process was killed)."""
        cat = classify_error(137, "", "")
        assert cat == ErrorCategory.PERMANENT

    def test_unknown_error_is_permanent(self):
        """Unknown error with descriptive stderr -> PERMANENT."""
        cat = classify_error(
            1, "", "some unknown error occurred",
        )
        assert cat == ErrorCategory.PERMANENT


# ---------------------------------------------------------------------------
# Optional real-CLI benchmarks (NIGHTWIRE_BENCHMARK=1)
# ---------------------------------------------------------------------------

SKIP_BENCHMARK = not os.environ.get("NIGHTWIRE_BENCHMARK")
SKIP_REASON = (
    "Set NIGHTWIRE_BENCHMARK=1 to run real CLI benchmarks"
)


@pytest.mark.skipif(SKIP_BENCHMARK, reason=SKIP_REASON)
async def test_real_cli_nonstreaming_latency(tmp_path):
    """Measure real CLI non-streaming latency."""
    runner = ClaudeRunner()
    runner.current_project = tmp_path

    start = time.monotonic()
    success, output = await runner.run_claude(
        "Respond with exactly one word: hello",
        timeout=30,
        max_retries=0,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    print(
        f"\n[BENCHMARK] Non-streaming: {elapsed_ms}ms,"
        f" success={success}"
    )
    print(f"[BENCHMARK] Output: {output[:100]}")


@pytest.mark.skipif(SKIP_BENCHMARK, reason=SKIP_REASON)
async def test_real_cli_streaming_latency(tmp_path):
    """Measure real CLI streaming latency."""
    runner = ClaudeRunner()
    runner.current_project = tmp_path

    first_chunk_time = None
    start = time.monotonic()

    async def cb(msg: str):
        nonlocal first_chunk_time
        if first_chunk_time is None:
            first_chunk_time = time.monotonic()

    success, output = await runner.run_claude(
        "Respond with exactly one word: hello",
        progress_callback=cb,
        stream=True,
        timeout=30,
        max_retries=0,
    )
    total_ms = int((time.monotonic() - start) * 1000)
    ttfc_ms = (
        int((first_chunk_time - start) * 1000)
        if first_chunk_time else None
    )

    print(
        f"\n[BENCHMARK] Streaming: total={total_ms}ms,"
        f" ttfc={ttfc_ms}ms, success={success}"
    )
    print(f"[BENCHMARK] Output: {output[:100]}")
