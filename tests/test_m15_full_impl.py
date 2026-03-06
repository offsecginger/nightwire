"""Tests for M15.2: SubAgent Full Implementation.

Covers:
    - AgentSpec migration (prompt field, no handler_fn)
    - get_agent_definitions_json() generation
    - TaskManager agent definitions wiring
    - Executor threading of agent definitions
    - run_claude_structured() agent parameter threading
    - Integration / edge cases
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from nightwire.claude_runner import ClaudeRunner
from nightwire.plugin_base import AgentSpec, NightwirePlugin, PluginContext
from nightwire.plugin_loader import PluginLoader


# ============================================================
# Helpers
# ============================================================


def _make_loader() -> PluginLoader:
    return PluginLoader(
        plugins_dir=Path("/tmp/nonexistent"),
        settings={},
        send_message=AsyncMock(),
        allowed_numbers=["+1234"],
        data_dir=Path("/tmp/data"),
    )


def _make_runner() -> ClaudeRunner:
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
# AgentSpec Migration
# ============================================================


class TestAgentSpecMigration:
    def test_agent_spec_with_prompt(self):
        spec = AgentSpec(
            name="reviewer",
            description="Reviews code",
            prompt="You are a code reviewer.",
        )
        assert spec.name == "reviewer"
        assert spec.description == "Reviews code"
        assert spec.prompt == "You are a code reviewer."

    def test_agent_spec_prompt_default_empty(self):
        spec = AgentSpec(name="a", description="b")
        assert spec.prompt == ""

    def test_agent_spec_no_handler_fn(self):
        """handler_fn was removed in M15.2."""
        spec = AgentSpec(name="a", description="b")
        assert not hasattr(spec, "handler_fn")

    def test_agent_spec_backward_compat(self):
        """AgentSpec with just name and description still works."""
        spec = AgentSpec(name="test", description="test agent")
        assert spec.name == "test"
        assert spec.prompt == ""


# ============================================================
# get_agent_definitions_json
# ============================================================


class TestAgentDefinitionsJson:
    def test_definitions_json_with_agents(self):
        loader = _make_loader()
        loader._agents["reviewer"] = AgentSpec(
            name="reviewer",
            description="Reviews code",
            prompt="You review code.",
        )
        result = loader.get_agent_definitions_json()
        assert result is not None
        parsed = json.loads(result)
        assert "reviewer" in parsed

    def test_definitions_json_no_agents_returns_none(self):
        loader = _make_loader()
        assert loader.get_agent_definitions_json() is None

    def test_definitions_json_includes_description(self):
        loader = _make_loader()
        loader._agents["a"] = AgentSpec(name="a", description="Does A")
        parsed = json.loads(loader.get_agent_definitions_json())
        assert parsed["a"]["description"] == "Does A"

    def test_definitions_json_includes_prompt(self):
        loader = _make_loader()
        loader._agents["a"] = AgentSpec(
            name="a", description="D", prompt="You are A.",
        )
        parsed = json.loads(loader.get_agent_definitions_json())
        assert parsed["a"]["prompt"] == "You are A."

    def test_definitions_json_omits_empty_prompt(self):
        loader = _make_loader()
        loader._agents["a"] = AgentSpec(name="a", description="D")
        parsed = json.loads(loader.get_agent_definitions_json())
        assert "prompt" not in parsed["a"]


# ============================================================
# TaskManager Wiring
# ============================================================


class TestTaskManagerDefinitions:
    def test_task_manager_accepts_definitions_callback(self):
        from nightwire.task_manager import TaskManager

        defs_fn = MagicMock(return_value='{"a": {"description": "A"}}')
        tm = TaskManager(
            runner=MagicMock(),
            project_manager=MagicMock(),
            memory=MagicMock(),
            config=MagicMock(),
            send_message=AsyncMock(),
            send_typing_indicator=AsyncMock(),
            get_memory_context=AsyncMock(),
            get_agent_definitions=defs_fn,
        )
        assert tm._get_agent_definitions is defs_fn

    def test_task_manager_default_definitions_none(self):
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
        assert tm._get_agent_definitions() is None

    def test_definitions_callback_returns_json(self):
        from nightwire.task_manager import TaskManager

        tm = TaskManager(
            runner=MagicMock(),
            project_manager=MagicMock(),
            memory=MagicMock(),
            config=MagicMock(),
            send_message=AsyncMock(),
            send_typing_indicator=AsyncMock(),
            get_memory_context=AsyncMock(),
            get_agent_definitions=lambda: '{"x": {"description": "X"}}',
        )
        result = tm._get_agent_definitions()
        parsed = json.loads(result)
        assert "x" in parsed


# ============================================================
# Executor Threading
# ============================================================


class TestExecutorThreading:
    def test_execute_accepts_agent_definitions(self):
        """TaskExecutor.execute() has agent_definitions parameter."""
        from nightwire.autonomous.executor import TaskExecutor

        sig = TaskExecutor.execute.__code__.co_varnames
        assert "agent_definitions" in sig

    def test_loop_has_agent_definitions_callback(self):
        """AutonomousLoop accepts get_agent_definitions."""
        from nightwire.autonomous.loop import AutonomousLoop

        loop = AutonomousLoop(
            db=MagicMock(),
            executor=MagicMock(),
            get_agent_definitions=lambda: '{"a": {}}',
        )
        assert loop._get_agent_definitions() == '{"a": {}}'

    def test_loop_default_definitions_none(self):
        from nightwire.autonomous.loop import AutonomousLoop

        loop = AutonomousLoop(db=MagicMock(), executor=MagicMock())
        assert loop._get_agent_definitions() is None

    def test_manager_threads_definitions_callback(self):
        """AutonomousManager threads get_agent_definitions to loop."""
        import sqlite3
        from nightwire.autonomous.manager import AutonomousManager

        conn = sqlite3.connect(":memory:")
        defs_fn = lambda: '{"test": {}}'
        mgr = AutonomousManager(
            db_connection=conn,
            get_agent_definitions=defs_fn,
        )
        assert mgr.loop._get_agent_definitions is defs_fn
        conn.close()


# ============================================================
# run_claude_structured Threading
# ============================================================


class TestStructuredAgentParams:
    def test_structured_accepts_agent_params(self):
        """run_claude_structured has agent_name and agent_definitions."""
        runner = _make_runner()
        sig = runner.run_claude_structured.__code__.co_varnames
        assert "agent_name" in sig
        assert "agent_definitions" in sig

    def test_structured_build_command_with_agents(self):
        """_build_command in structured path includes agent flags."""
        runner = _make_runner()
        cmd = runner._build_command(
            json_schema='{"type":"object"}',
            agent_definitions='{"a":{"description":"A"}}',
        )
        assert "--agents" in cmd
        assert "--json-schema" in cmd

    def test_structured_mutual_exclusivity(self):
        """ValueError when both agent_name and agent_definitions set."""
        runner = _make_runner()
        try:
            runner._build_command(
                agent_name="test",
                agent_definitions='{"test":{}}',
            )
            assert False, "Should raise ValueError"
        except ValueError:
            pass


# ============================================================
# Integration / Edge Cases
# ============================================================


class TestIntegration:
    def test_end_to_end_plugin_to_json(self):
        """Plugin → PluginLoader → JSON definitions → parseable."""
        ctx = PluginContext(
            plugin_name="test",
            send_message=AsyncMock(),
            settings={},
            allowed_numbers=[],
            data_dir=Path("/tmp"),
        )
        plugin = CodeReviewPlugin(ctx)
        loader = _make_loader()
        for name, spec in plugin.agents().items():
            loader._agents[name] = spec
        result = loader.get_agent_definitions_json()
        assert result is not None
        parsed = json.loads(result)
        assert "code-reviewer" in parsed
        assert "description" in parsed["code-reviewer"]
        assert "prompt" in parsed["code-reviewer"]

    def test_empty_prompt_omitted_from_json(self):
        """Agents with prompt="" don't include prompt key."""
        loader = _make_loader()
        loader._agents["basic"] = AgentSpec(
            name="basic", description="Basic agent",
        )
        parsed = json.loads(loader.get_agent_definitions_json())
        assert "prompt" not in parsed["basic"]
        assert parsed["basic"]["description"] == "Basic agent"

    def test_definitions_json_is_parseable(self):
        loader = _make_loader()
        loader._agents["a"] = AgentSpec(name="a", description="A", prompt="P")
        loader._agents["b"] = AgentSpec(name="b", description="B")
        result = loader.get_agent_definitions_json()
        parsed = json.loads(result)
        assert len(parsed) == 2


class CodeReviewPlugin(NightwirePlugin):
    """Test-local copy for integration tests."""

    name = "code_review"

    def agents(self):
        return {
            "code-reviewer": AgentSpec(
                name="code-reviewer",
                description="Reviews code",
                prompt="Review this code.",
            ),
        }
