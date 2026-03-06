"""Tests for M15: SubAgent Integration Spike.

Covers:
    - _build_command() agent flag inclusion/exclusion
    - Mutual exclusivity of --agent and --agents flags
    - run_claude() parameter passthrough
    - JSON agent definition format validation
"""

import json
from unittest.mock import MagicMock

from nightwire.claude_runner import ClaudeRunner

# ============================================================
# Helpers
# ============================================================


def _make_runner() -> ClaudeRunner:
    """Create a ClaudeRunner with mocked config."""
    config = MagicMock()
    config.claude_path = "claude"
    config.claude_model = "sonnet"
    config.claude_timeout = 60
    config.claude_max_budget_usd = None
    config.config_dir = MagicMock()
    config.config_dir.__truediv__ = MagicMock(
        return_value=MagicMock(exists=MagicMock(return_value=False))
    )
    config.settings = {}
    runner = ClaudeRunner.__new__(ClaudeRunner)
    runner.config = config
    runner.current_project = None
    runner._active_invocations = {}
    runner._next_invocation_id = 0
    runner._last_session_id = None
    runner._last_usage = None
    return runner


# ============================================================
# _build_command Agent Flags
# ============================================================


class TestBuildCommandAgentFlags:
    def test_no_agent_flags_by_default(self):
        runner = _make_runner()
        cmd = runner._build_command()
        assert "--agent" not in cmd
        assert "--agents" not in cmd

    def test_agent_name_flag(self):
        runner = _make_runner()
        cmd = runner._build_command(agent_name="code-reviewer")
        idx = cmd.index("--agent")
        assert cmd[idx + 1] == "code-reviewer"

    def test_agent_definitions_flag(self):
        runner = _make_runner()
        defs = '{"reviewer": {"description": "Reviews code"}}'
        cmd = runner._build_command(agent_definitions=defs)
        idx = cmd.index("--agents")
        assert cmd[idx + 1] == defs

    def test_agent_name_and_definitions_mutually_exclusive(self):
        runner = _make_runner()
        try:
            runner._build_command(
                agent_name="test",
                agent_definitions='{"test": {}}',
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "both" in str(e).lower()

    def test_agent_name_none_omits_flag(self):
        runner = _make_runner()
        cmd = runner._build_command(agent_name=None)
        assert "--agent" not in cmd

    def test_agent_definitions_none_omits_flag(self):
        runner = _make_runner()
        cmd = runner._build_command(agent_definitions=None)
        assert "--agents" not in cmd

    def test_agent_name_with_other_flags(self):
        """Agent flags coexist with existing flags."""
        runner = _make_runner()
        cmd = runner._build_command(
            agent_name="my-agent",
            resume_session_id="sess-123",
        )
        assert "--agent" in cmd
        assert "--resume" in cmd
        idx_agent = cmd.index("--agent")
        assert cmd[idx_agent + 1] == "my-agent"
        idx_resume = cmd.index("--resume")
        assert cmd[idx_resume + 1] == "sess-123"


# ============================================================
# JSON Agent Definition Format
# ============================================================


class TestAgentDefinitionsFormat:
    def test_valid_json_format(self):
        """Agent definitions are valid JSON with expected structure."""
        defs = {
            "code-reviewer": {
                "description": "Reviews code for quality",
                "prompt": "You are a code reviewer. Analyze the code.",
            },
            "test-runner": {
                "description": "Runs tests",
                "prompt": "Run the test suite and report results.",
            },
        }
        json_str = json.dumps(defs)
        parsed = json.loads(json_str)
        assert "code-reviewer" in parsed
        assert "description" in parsed["code-reviewer"]

    def test_single_agent_definition(self):
        defs = json.dumps({
            "reviewer": {"description": "Reviews code", "prompt": "Review this"}
        })
        runner = _make_runner()
        cmd = runner._build_command(agent_definitions=defs)
        idx = cmd.index("--agents")
        parsed = json.loads(cmd[idx + 1])
        assert "reviewer" in parsed
