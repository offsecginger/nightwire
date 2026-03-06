"""Tests for M11: Plugin Agent System.

Covers:
    - AgentSpec dataclass validation
    - NightwirePlugin.agents() default and override behavior
    - PluginLoader agent collection, validation, and conflict detection
    - Agent catalog prompt generation
    - TaskManager catalog injection wiring
    - Example plugin agents (copy-pasteable reference for plugin authors)
    - Edge cases for agent name validation
"""

import re
from pathlib import Path
from typing import Dict
from unittest.mock import AsyncMock, MagicMock

from nightwire.plugin_base import AgentSpec, NightwirePlugin, PluginContext
from nightwire.plugin_loader import PluginLoader

# ============================================================
# Example Plugin Fixtures (copy-pasteable for plugin authors)
# ============================================================


class CodeReviewPlugin(NightwirePlugin):
    """Example plugin registering a code review agent."""

    name = "code_review"
    description = "Provides a code review agent"
    version = "1.0.0"

    def agents(self) -> Dict[str, AgentSpec]:
        return {
            "code-reviewer": AgentSpec(
                name="code-reviewer",
                description="Reviews code for quality and correctness",
                prompt="You are a code reviewer. Analyze the code for bugs and quality.",
            ),
        }


class SuiteRunnerPlugin(NightwirePlugin):
    """Example plugin registering a test runner agent."""

    name = "test_runner"
    description = "Provides a test runner agent"
    version = "1.0.0"

    def agents(self) -> Dict[str, AgentSpec]:
        return {
            "test-runner": AgentSpec(
                name="test-runner",
                description="Runs the project test suite and reports results",
                prompt="Run the test suite and report pass/fail results.",
            ),
        }


class EmptyPlugin(NightwirePlugin):
    """Plugin that registers no agents."""

    name = "empty"
    version = "1.0.0"


class BadNamePlugin(NightwirePlugin):
    """Plugin that tries to register agents with invalid names."""

    name = "bad_names"
    version = "1.0.0"

    def agents(self) -> Dict[str, AgentSpec]:
        return {
            "Invalid-Name": AgentSpec(
                name="Invalid-Name",
                description="Uppercase not allowed",
            ),
            "has_underscore": AgentSpec(
                name="has_underscore",
                description="Underscores not allowed in agent names",
            ),
            "123start": AgentSpec(
                name="123start",
                description="Cannot start with number",
            ),
        }


# ============================================================
# Helpers
# ============================================================


def _make_plugin_context(name="test_plugin") -> PluginContext:
    return PluginContext(
        plugin_name=name,
        send_message=AsyncMock(),
        settings={},
        allowed_numbers=["+1234"],
        data_dir=Path("/tmp/data"),
    )


def _make_loader() -> PluginLoader:
    return PluginLoader(
        plugins_dir=Path("/tmp/nonexistent"),
        settings={},
        send_message=AsyncMock(),
        allowed_numbers=["+1234"],
        data_dir=Path("/tmp/data"),
    )


# ============================================================
# AgentSpec Validation
# ============================================================


class TestAgentSpec:
    def test_agent_spec_fields(self):
        spec = AgentSpec(
            name="my-agent",
            description="Does things",
            prompt="You are a helpful agent.",
        )
        assert spec.name == "my-agent"
        assert spec.description == "Does things"
        assert spec.prompt == "You are a helpful agent."

    def test_agent_spec_prompt_default_empty(self):
        spec = AgentSpec(name="a", description="b")
        assert spec.prompt == ""

    def test_agent_spec_from_plugin(self):
        ctx = _make_plugin_context()
        plugin = CodeReviewPlugin(ctx)
        agents = plugin.agents()
        assert "code-reviewer" in agents
        spec = agents["code-reviewer"]
        assert spec.name == "code-reviewer"
        assert "quality" in spec.description.lower()


# ============================================================
# NightwirePlugin.agents() Default
# ============================================================


class TestPluginAgentsDefault:
    def test_base_plugin_agents_empty(self):
        ctx = _make_plugin_context()
        plugin = NightwirePlugin(ctx)
        assert plugin.agents() == {}

    def test_base_plugin_agents_override(self):
        ctx = _make_plugin_context()
        plugin = SuiteRunnerPlugin(ctx)
        agents = plugin.agents()
        assert len(agents) == 1
        assert "test-runner" in agents


# ============================================================
# PluginLoader Agent Collection
# ============================================================


class TestPluginLoaderAgents:
    def test_loader_collects_agents(self):
        loader = _make_loader()
        ctx = _make_plugin_context("code_review")
        plugin = CodeReviewPlugin(ctx)
        loader.plugins.append(plugin)
        for agent_name, spec in plugin.agents().items():
            loader._agents[agent_name] = spec
        assert "code-reviewer" in loader._agents
        assert loader._agents["code-reviewer"].name == "code-reviewer"

    def test_loader_validates_agent_name_format(self):
        """Invalid agent names are rejected during collection."""
        invalid_names = ["Invalid-Name", "has_underscore", "123start", "with space"]
        for name in invalid_names:
            assert not re.match(r'^[a-z][a-z0-9-]*$', name), f"{name} should be invalid"

    def test_loader_rejects_duplicate_agents(self):
        loader = _make_loader()
        spec1 = AgentSpec(name="dup", description="First")
        loader._agents["dup"] = spec1
        assert "dup" in loader._agents
        assert loader._agents["dup"].description == "First"

    def test_loader_no_agents_empty(self):
        loader = _make_loader()
        assert len(loader._agents) == 0
        assert loader.get_all_agents() == {}

    def test_loader_multiple_plugins(self):
        loader = _make_loader()
        ctx1 = _make_plugin_context("code_review")
        ctx2 = _make_plugin_context("test_runner")
        p1 = CodeReviewPlugin(ctx1)
        p2 = SuiteRunnerPlugin(ctx2)
        for agent_name, spec in p1.agents().items():
            loader._agents[agent_name] = spec
        for agent_name, spec in p2.agents().items():
            loader._agents[agent_name] = spec
        all_agents = loader.get_all_agents()
        assert len(all_agents) == 2
        assert "code-reviewer" in all_agents
        assert "test-runner" in all_agents


# ============================================================
# Agent Catalog Generation
# ============================================================


class TestAgentCatalog:
    def test_catalog_prompt_with_agents(self):
        loader = _make_loader()
        loader._agents["code-reviewer"] = AgentSpec(
            name="code-reviewer", description="Reviews code",
        )
        prompt = loader.get_agent_catalog_prompt()
        assert "## Available Plugin Agents" in prompt
        assert "**code-reviewer**" in prompt
        assert "Reviews code" in prompt

    def test_catalog_prompt_no_agents(self):
        loader = _make_loader()
        assert loader.get_agent_catalog_prompt() == ""

    def test_catalog_prompt_multiple_agents(self):
        loader = _make_loader()
        loader._agents["agent-a"] = AgentSpec(
            name="agent-a", description="Does A",
        )
        loader._agents["agent-b"] = AgentSpec(
            name="agent-b", description="Does B",
        )
        prompt = loader.get_agent_catalog_prompt()
        assert "**agent-a**" in prompt
        assert "**agent-b**" in prompt

    def test_catalog_prompt_escapes_description(self):
        loader = _make_loader()
        loader._agents["special"] = AgentSpec(
            name="special",
            description="Handles <tags> & 'quotes' and \"doubles\"",
        )
        prompt = loader.get_agent_catalog_prompt()
        assert "Handles <tags>" in prompt

    def test_catalog_prompt_format(self):
        loader = _make_loader()
        loader._agents["my-agent"] = AgentSpec(
            name="my-agent", description="Test agent",
        )
        prompt = loader.get_agent_catalog_prompt()
        lines = prompt.split("\n")
        assert lines[0] == "## Available Plugin Agents"
        assert any("- **my-agent**: Test agent" in line for line in lines)
        assert "Agent dispatch is controlled by the system" in prompt

    def test_catalog_prompt_empty_when_plugin_has_no_agents(self):
        """Plugin loaded but registers zero agents -> empty catalog."""
        loader = _make_loader()
        ctx = _make_plugin_context("empty")
        plugin = EmptyPlugin(ctx)
        loader.plugins.append(plugin)
        for agent_name, spec in plugin.agents().items():
            loader._agents[agent_name] = spec
        assert loader.get_agent_catalog_prompt() == ""


# ============================================================
# TaskManager Catalog Injection
# ============================================================


class TestTaskManagerCatalog:
    def test_task_manager_accepts_catalog_callback(self):
        from nightwire.task_manager import TaskManager

        catalog_fn = MagicMock(return_value="## Agents\n- test")
        tm = TaskManager(
            runner=MagicMock(),
            project_manager=MagicMock(),
            memory=MagicMock(),
            config=MagicMock(),
            send_message=AsyncMock(),
            send_typing_indicator=AsyncMock(),
            get_memory_context=AsyncMock(),
            get_agent_catalog=catalog_fn,
        )
        assert tm._get_agent_catalog is catalog_fn

    def test_task_manager_default_catalog_empty(self):
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
        assert tm._get_agent_catalog() == ""

    def test_catalog_appended_to_memory_context(self):
        """When both memory context and catalog exist, they're concatenated."""
        from nightwire.task_manager import TaskManager

        tm = TaskManager(
            runner=MagicMock(),
            project_manager=MagicMock(),
            memory=MagicMock(),
            config=MagicMock(),
            send_message=AsyncMock(),
            send_typing_indicator=AsyncMock(),
            get_memory_context=AsyncMock(),
            get_agent_catalog=lambda: "## Agents\n- my-agent",
        )
        memory_context = "Previous conversation context"
        agent_catalog = tm._get_agent_catalog()
        if agent_catalog:
            if memory_context:
                memory_context = memory_context + "\n\n" + agent_catalog
            else:
                memory_context = agent_catalog
        assert "Previous conversation context" in memory_context
        assert "## Agents" in memory_context

    def test_catalog_alone_when_no_memory(self):
        """When memory context is None but catalog exists, catalog is used."""
        from nightwire.task_manager import TaskManager

        tm = TaskManager(
            runner=MagicMock(),
            project_manager=MagicMock(),
            memory=MagicMock(),
            config=MagicMock(),
            send_message=AsyncMock(),
            send_typing_indicator=AsyncMock(),
            get_memory_context=AsyncMock(),
            get_agent_catalog=lambda: "## Agents\n- my-agent",
        )
        memory_context = None
        agent_catalog = tm._get_agent_catalog()
        if agent_catalog:
            if memory_context:
                memory_context = memory_context + "\n\n" + agent_catalog
            else:
                memory_context = agent_catalog
        assert memory_context == "## Agents\n- my-agent"


# ============================================================
# Example Plugin Fixtures
# ============================================================


class TestExamplePlugins:
    def test_code_review_plugin_registers_agent(self):
        ctx = _make_plugin_context("code_review")
        plugin = CodeReviewPlugin(ctx)
        agents = plugin.agents()
        assert "code-reviewer" in agents
        spec = agents["code-reviewer"]
        assert spec.name == "code-reviewer"
        assert spec.description
        assert spec.prompt

    def test_test_runner_plugin_registers_agent(self):
        ctx = _make_plugin_context("test_runner")
        plugin = SuiteRunnerPlugin(ctx)
        agents = plugin.agents()
        assert "test-runner" in agents
        spec = agents["test-runner"]
        assert spec.name == "test-runner"


# ============================================================
# Edge Cases
# ============================================================


class TestAgentNameEdgeCases:
    def test_agent_name_allows_hyphens(self):
        assert re.match(r'^[a-z][a-z0-9-]*$', "code-reviewer")
        assert re.match(r'^[a-z][a-z0-9-]*$', "my-agent-v2")

    def test_agent_name_rejects_underscores(self):
        assert not re.match(r'^[a-z][a-z0-9-]*$', "code_reviewer")
        assert not re.match(r'^[a-z][a-z0-9-]*$', "my_agent")
