"""Unit tests for ClaudeRunner state management and invocation isolation.

Tests the _InvocationState pattern that isolates concurrent
run_claude() calls. Each invocation gets its own process handle
and cancelled flag so concurrent calls don't corrupt each other.
"""

from unittest.mock import MagicMock, patch

import pytest

from nightwire.claude_runner import ClaudeRunner


@pytest.fixture
def runner():
    """Create a ClaudeRunner with mocked config."""
    with patch("nightwire.claude_runner.get_config") as mock_config:
        cfg = MagicMock()
        cfg.settings = {}
        cfg.claude_model = "claude-sonnet-4-5"
        cfg.claude_path = "claude"
        cfg.claude_timeout = 60
        cfg.config_dir = MagicMock()
        cfg.config_dir.__truediv__ = MagicMock(
            return_value=MagicMock(exists=MagicMock(return_value=False))
        )
        mock_config.return_value = cfg
        r = ClaudeRunner()
        return r


class TestInvocationStateIsolation:
    """Tests for per-invocation state isolation."""

    def test_concurrent_invocations_get_separate_state(self, runner):
        """Two invocations get distinct IDs and independent state."""
        id1, state1 = runner._new_invocation()
        id2, state2 = runner._new_invocation()

        assert id1 != id2, "Invocation IDs must be unique"
        assert state1 is not state2, "States must be distinct"
        assert id1 in runner._active_invocations
        assert id2 in runner._active_invocations

        # Mutating one state does not affect the other
        state1.cancelled = True
        assert not state2.cancelled

        state2.process = MagicMock()
        assert state1.process is None

        runner._end_invocation(id1)
        runner._end_invocation(id2)
        assert len(runner._active_invocations) == 0

    async def test_cancel_broadcasts_to_all_invocations(self, runner):
        """cancel() sets cancelled=True on ALL active invocations.

        Documents the broadcast cancel behavior — no per-invocation
        targeted cancel exists. See Known Issues in roadmap.
        """
        id1, state1 = runner._new_invocation()
        id2, state2 = runner._new_invocation()

        # Give one invocation a mock process
        mock_process = MagicMock()
        mock_process.returncode = None  # Still running
        state1.process = mock_process

        await runner.cancel()

        assert state1.cancelled is True
        assert state2.cancelled is True
        mock_process.kill.assert_called_once()

        runner._end_invocation(id1)
        runner._end_invocation(id2)

    async def test_invocation_cleanup_on_exception(self, runner):
        """_end_invocation is called even when inner raises."""
        inv_id, inv_state = runner._new_invocation()
        assert inv_id in runner._active_invocations

        with pytest.raises(
            RuntimeError, match="simulated inner failure"
        ):
            try:
                raise RuntimeError("simulated inner failure")
            finally:
                runner._end_invocation(inv_id)

        assert inv_id not in runner._active_invocations
        assert len(runner._active_invocations) == 0

    def test_end_invocation_idempotent(self, runner):
        """Calling _end_invocation twice does not raise."""
        inv_id, _ = runner._new_invocation()
        runner._end_invocation(inv_id)
        runner._end_invocation(inv_id)
        assert inv_id not in runner._active_invocations


class TestBuildCommand:
    """Tests for _build_command flag assembly."""

    def test_basic_command(self, runner):
        """Basic command includes -p, --output-format, --model."""
        cmd = runner._build_command()
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        assert "--model" in cmd

    def test_json_schema_flag(self, runner):
        """--json-schema flag is added when provided."""
        schema = '{"type":"object"}'
        cmd = runner._build_command(json_schema=schema)
        idx = cmd.index("--json-schema")
        assert cmd[idx + 1] == schema

    def test_verbose_flag(self, runner):
        """--verbose flag added for streaming."""
        cmd = runner._build_command(verbose=True)
        assert "--verbose" in cmd

    def test_stream_json_format(self, runner):
        """stream-json format is passed correctly."""
        cmd = runner._build_command(
            output_format="stream-json", verbose=True,
        )
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"
        assert "--verbose" in cmd


class TestBuildPrompt:
    """Tests for _build_prompt assembly."""

    def test_prompt_only(self, runner):
        """Prompt without memory context."""
        result = runner._build_prompt("do something")
        assert "## Current Task" in result
        assert "do something" in result
        assert "---" not in result

    def test_with_memory_context(self, runner):
        """Prompt with memory context prepended."""
        result = runner._build_prompt(
            "do something", memory_context="past context"
        )
        assert "past context" in result
        assert "## Current Task" in result
        assert "---" in result
        # Memory comes before task
        assert result.index("past context") < result.index(
            "## Current Task"
        )
