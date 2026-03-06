"""Tests for Milestone 9: Configuration & Diagnostics.

Covers:
- Story 9.1: CLI config options (budget, session tracking, max-turns)
- Story 9.2: Diagnostics module (/diagnose, startup checks)
- Story 9.3: Enhanced help (HelpMetadata, /help <command>, setup status)
"""

from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Story 9.1 — CLI Config Options
# ---------------------------------------------------------------------------


class TestBudgetConfig:
    """Tests for claude_max_budget_usd config property."""

    def test_budget_default_none(self, tmp_path):
        from nightwire.config import Config

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "settings.yaml").write_text("{}")
        config = Config(config_dir=cfg_dir)
        assert config.claude_max_budget_usd is None

    def test_budget_custom_value(self, tmp_path):
        from nightwire.config import Config

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "settings.yaml").write_text(
            "claude_max_budget_usd: 5.0"
        )
        config = Config(config_dir=cfg_dir)
        assert config.claude_max_budget_usd == 5.0

    def test_budget_invalid_returns_none(self, tmp_path):
        from nightwire.config import Config

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "settings.yaml").write_text(
            'claude_max_budget_usd: "not_a_number"'
        )
        config = Config(config_dir=cfg_dir)
        assert config.claude_max_budget_usd is None


class TestBuildCommand:
    """Tests for _build_command() flag generation."""

    def _make_runner(self, tmp_path, settings=None):
        import yaml

        from nightwire.config import Config

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        yaml_content = yaml.dump(settings) if settings else "{}"
        (cfg_dir / "settings.yaml").write_text(yaml_content)

        with patch("nightwire.claude_runner.get_config") as mock_cfg:
            config = Config(config_dir=cfg_dir)
            mock_cfg.return_value = config
            from nightwire.claude_runner import ClaudeRunner

            runner = ClaudeRunner()
            runner.config = config
        return runner

    def test_budget_flag_included(self, tmp_path):
        runner = self._make_runner(
            tmp_path, {"claude_max_budget_usd": 5.0}
        )
        cmd = runner._build_command()
        assert "--max-budget-usd" in cmd
        idx = cmd.index("--max-budget-usd")
        assert cmd[idx + 1] == "5.0"

    def test_budget_flag_absent_when_none(self, tmp_path):
        runner = self._make_runner(tmp_path, {})
        cmd = runner._build_command()
        assert "--max-budget-usd" not in cmd

    def test_resume_flag_included(self, tmp_path):
        runner = self._make_runner(tmp_path, {})
        cmd = runner._build_command(resume_session_id="abc-123")
        assert "--resume" in cmd
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "abc-123"

    def test_no_resume_when_none(self, tmp_path):
        runner = self._make_runner(tmp_path, {})
        cmd = runner._build_command(resume_session_id=None)
        assert "--resume" not in cmd

    def test_max_turns_included(self, tmp_path):
        runner = self._make_runner(
            tmp_path, {"claude_max_turns": 10}
        )
        cmd = runner._build_command()
        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "10"

    def test_max_turns_absent_when_not_set(self, tmp_path):
        runner = self._make_runner(tmp_path, {})
        cmd = runner._build_command()
        assert "--max-turns" not in cmd


class TestSessionTracking:
    """Tests for session_id extraction and last_session_id property."""

    def _make_runner(self, tmp_path):
        from nightwire.config import Config

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        (cfg_dir / "settings.yaml").write_text("{}")

        with patch("nightwire.claude_runner.get_config") as mock_cfg:
            config = Config(config_dir=cfg_dir)
            mock_cfg.return_value = config
            from nightwire.claude_runner import ClaudeRunner

            runner = ClaudeRunner()
            runner.config = config
        return runner

    async def test_last_session_id_after_success(self, tmp_path):
        """Session ID extracted from response after successful run."""
        runner = self._make_runner(tmp_path)
        runner.current_project = tmp_path

        response_json = (
            '{"result":"hello","session_id":"test-uuid-123",'
            '"is_error":false,"usage":{},"modelUsage":{}}'
        )
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(response_json.encode(), b"")
        )
        mock_proc.returncode = 0

        cooldown_mock = MagicMock(is_active=False)
        with (
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch(
                "nightwire.rate_limit_cooldown.get_cooldown_manager",
                return_value=cooldown_mock,
            ),
            patch.object(
                runner, "_maybe_sandbox", new_callable=AsyncMock
            ) as mock_sb,
        ):
            mock_sb.side_effect = lambda cmd, proj, cwd: (cmd, cwd)
            success, _output = await runner.run_claude(
                "test prompt", timeout=10,
            )

        assert success
        assert runner.last_session_id == "test-uuid-123"

    async def test_last_session_id_none_after_failure(self, tmp_path):
        """Session ID is None after a failed run."""
        runner = self._make_runner(tmp_path)
        runner.current_project = tmp_path

        response_json = (
            '{"result":"error message","is_error":true,'
            '"usage":{},"modelUsage":{}}'
        )
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(response_json.encode(), b"")
        )
        mock_proc.returncode = 1

        cooldown_mock = MagicMock(is_active=False)
        with (
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch(
                "nightwire.rate_limit_cooldown.get_cooldown_manager",
                return_value=cooldown_mock,
            ),
            patch.object(
                runner, "_maybe_sandbox", new_callable=AsyncMock
            ) as mock_sb,
        ):
            mock_sb.side_effect = lambda cmd, proj, cwd: (cmd, cwd)
            success, _output = await runner.run_claude(
                "test prompt", timeout=10,
            )

        assert not success
        assert runner.last_session_id is None

    async def test_session_id_from_streaming(self, tmp_path):
        """Session ID extracted after streaming via _last_response."""
        runner = self._make_runner(tmp_path)
        runner.current_project = tmp_path

        ndjson_lines = (
            '{"type":"message","subtype":"assistant",'
            '"session_id":"stream-uuid"}\n'
            '{"type":"content_block_delta","delta":'
            '{"type":"text_delta","text":"hello"}}\n'
            '{"type":"result","subtype":"success",'
            '"session_id":"stream-uuid","result":"hello",'
            '"is_error":false,"usage":{},"modelUsage":{},'
            '"duration_ms":100,"total_cost_usd":0.001,'
            '"num_turns":1}\n'
        )
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        lines = ndjson_lines.encode().split(b"\n")
        lines = [ln for ln in lines if ln]
        line_iter = iter(lines)

        async def mock_readline():
            try:
                return next(line_iter) + b"\n"
            except StopIteration:
                return b""

        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = mock_readline
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")

        progress_cb = AsyncMock()
        cooldown_mock = MagicMock(is_active=False)

        with (
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch(
                "nightwire.rate_limit_cooldown.get_cooldown_manager",
                return_value=cooldown_mock,
            ),
            patch.object(
                runner, "_maybe_sandbox", new_callable=AsyncMock
            ) as mock_sb,
        ):
            mock_sb.side_effect = lambda cmd, proj, cwd: (cmd, cwd)
            success, _output = await runner.run_claude(
                "test prompt", timeout=30,
                progress_callback=progress_cb,
                stream=True,
            )

        assert success
        assert runner.last_session_id == "stream-uuid"


class TestTaskManagerSessions:
    """Tests for TaskManager session ID storage."""

    def test_session_ids_dict_exists(self):
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
        assert hasattr(tm, "_session_ids")
        assert isinstance(tm._session_ids, dict)
        assert len(tm._session_ids) == 0


# ---------------------------------------------------------------------------
# Story 9.2 — Configuration Diagnostics
# ---------------------------------------------------------------------------


class TestCheckClaudeCli:
    """Tests for check_claude_cli diagnostic."""

    async def test_claude_cli_available(self):
        from nightwire.diagnostics import check_claude_cli

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"claude v1.0.24\n", b"")
        )
        mock_proc.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            ok, detail, hint = await check_claude_cli("claude")

        assert ok is True
        assert "1.0.24" in detail
        assert hint == ""

    async def test_claude_cli_missing(self):
        from nightwire.diagnostics import check_claude_cli

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            ok, detail, hint = await check_claude_cli("claude")

        assert ok is False
        assert "not found" in detail
        assert "npm install" in hint


class TestCheckSignalApi:
    """Tests for check_signal_api diagnostic."""

    async def test_signal_api_reachable(self):
        from nightwire.diagnostics import check_signal_api

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"versions": ["v2"]}
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(
            return_value=mock_session,
        )
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "aiohttp.ClientSession", return_value=mock_session,
        ):
            ok, detail, hint = await check_signal_api(
                "http://127.0.0.1:8080"
            )

        assert ok is True
        assert "v2" in detail

    async def test_signal_api_unreachable(self):
        from nightwire.diagnostics import check_signal_api

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            side_effect=Exception("Connection refused")
        )
        mock_session.__aenter__ = AsyncMock(
            return_value=mock_session,
        )
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "aiohttp.ClientSession", return_value=mock_session,
        ):
            ok, detail, hint = await check_signal_api(
                "http://127.0.0.1:8080"
            )

        assert ok is False
        assert "docker compose" in hint


class TestCheckSqliteVec:
    """Tests for check_sqlite_vec diagnostic."""

    def test_sqlite_vec_available(self):
        """Mocked successful import."""
        mock_mod = MagicMock(__version__="0.1.3")
        with patch.dict("sys.modules", {"sqlite_vec": mock_mod}):
            # Call the function directly — it does a fresh import
            ok, detail, hint = _run_check_sqlite_vec()
        assert ok is True
        assert "sqlite-vec" in detail

    def test_sqlite_vec_missing(self):
        """Simulate missing sqlite_vec."""
        with patch.dict("sys.modules", {"sqlite_vec": None}):
            ok, detail, hint = _run_check_sqlite_vec()
        assert ok is False
        assert "pip install" in hint


def _run_check_sqlite_vec():
    """Helper: reimport diagnostics and call check_sqlite_vec."""
    import importlib

    import nightwire.diagnostics

    importlib.reload(nightwire.diagnostics)
    return nightwire.diagnostics.check_sqlite_vec()


class TestCheckEmbeddings:
    """Tests for check_embeddings diagnostic."""

    def test_embeddings_available(self):
        """Mocked successful import."""
        mock_mod = MagicMock(__version__="2.5.0")
        with patch.dict(
            "sys.modules", {"sentence_transformers": mock_mod},
        ):
            ok, detail, hint = _run_check_embeddings()
        assert ok is True
        assert "sentence-transformers" in detail

    def test_embeddings_missing(self):
        """Simulate missing sentence_transformers."""
        with patch.dict(
            "sys.modules", {"sentence_transformers": None},
        ):
            ok, detail, hint = _run_check_embeddings()
        assert ok is False
        assert "pip install" in hint


def _run_check_embeddings():
    """Helper: reimport diagnostics and call check_embeddings."""
    import importlib

    import nightwire.diagnostics

    importlib.reload(nightwire.diagnostics)
    return nightwire.diagnostics.check_embeddings()


class TestCheckDocker:
    """Tests for check_docker diagnostic."""

    async def test_docker_available(self):
        from nightwire.diagnostics import check_docker

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"Server Version: 24.0.7\n", b"")
        )
        mock_proc.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            ok, detail, hint = await check_docker()

        assert ok is True
        assert "24.0.7" in detail

    async def test_docker_missing(self):
        from nightwire.diagnostics import check_docker

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            ok, detail, hint = await check_docker()

        assert ok is False
        assert "docker" in hint.lower()


class TestRunAllChecks:
    """Tests for run_all_checks aggregation."""

    async def test_run_all_checks_aggregates(self):
        from nightwire.diagnostics import run_all_checks

        config = MagicMock()
        config.claude_path = "claude"
        config.signal_api_url = "http://127.0.0.1:8080"

        with (
            patch(
                "nightwire.diagnostics.check_claude_cli",
                new_callable=AsyncMock,
                return_value=(True, "claude v1.0", ""),
            ),
            patch(
                "nightwire.diagnostics.check_signal_api",
                new_callable=AsyncMock,
                return_value=(True, "Signal API v2", ""),
            ),
            patch(
                "nightwire.diagnostics.check_sqlite_vec",
                return_value=(
                    False, "not installed", "pip install sqlite-vec",
                ),
            ),
            patch(
                "nightwire.diagnostics.check_embeddings",
                return_value=(
                    True, "sentence-transformers 2.5", "",
                ),
            ),
            patch(
                "nightwire.diagnostics.check_docker",
                new_callable=AsyncMock,
                return_value=(False, "not found", "Install Docker"),
            ),
        ):
            results = await run_all_checks(config)

        assert len(results) == 5
        assert results["Claude CLI"][0] is True
        assert results["sqlite-vec"][0] is False
        assert results["Docker"][0] is False


class TestDiagnoseCommand:
    """Tests for the /diagnose command handler."""

    async def test_diagnose_formats_output(self):
        from nightwire.commands.core import CoreCommandHandler

        ctx = MagicMock()
        ctx.config = MagicMock()
        handler = CoreCommandHandler(ctx)

        mock_results = {
            "Claude CLI": (True, "claude v1.0", ""),
            "Signal API": (
                False, "unreachable", "docker compose up -d",
            ),
        }

        async def mock_run_all(cfg):
            return mock_results

        with patch(
            "nightwire.diagnostics.run_all_checks",
            side_effect=mock_run_all,
        ):
            result = await handler.handle_diagnose("+1234", "")

        assert "Diagnostics:" in result
        assert "+ Claude CLI: claude v1.0" in result
        assert "- Signal API: unreachable" in result
        assert "Hint: docker compose up" in result

    def test_diagnose_in_builtin_commands(self):
        from nightwire.commands.base import BUILTIN_COMMANDS

        assert "diagnose" in BUILTIN_COMMANDS


# ---------------------------------------------------------------------------
# Story 9.3 — Enhanced Help
# ---------------------------------------------------------------------------


class TestHelpMetadata:
    """Tests for HelpMetadata and get_help_metadata."""

    def test_help_metadata_for_known_command(self):
        from nightwire.commands.core import CoreCommandHandler

        ctx = MagicMock()
        handler = CoreCommandHandler(ctx)
        meta = handler.get_help_metadata()
        assert "select" in meta
        assert meta["select"].usage == "/select <project_name>"
        assert len(meta["select"].examples) > 0

    def test_help_metadata_completeness(self):
        from nightwire.commands.core import CoreCommandHandler

        ctx = MagicMock()
        handler = CoreCommandHandler(ctx)
        meta = handler.get_help_metadata()
        commands = handler.get_commands()
        for cmd_name in commands:
            if cmd_name == "sidechannel":
                continue  # alias for nightwire
            assert cmd_name in meta, (
                f"Missing metadata for /{cmd_name}"
            )


class TestHelpCommand:
    """Tests for /help with and without arguments."""

    def _setup_handler(self):
        from nightwire.commands.base import HandlerRegistry
        from nightwire.commands.core import CoreCommandHandler

        ctx = MagicMock()
        ctx.nightwire_runner = None
        ctx.plugin_loader = MagicMock()
        ctx.plugin_loader.get_all_help.return_value = []
        ctx.config = MagicMock()
        ctx.config.claude_path = "claude"
        ctx.config.sandbox_enabled = False

        registry = HandlerRegistry()
        ctx.registry = registry
        handler = CoreCommandHandler(ctx)
        registry.register(handler)
        return handler, registry, ctx

    async def test_help_no_args_returns_general(self):
        handler, _, _ = self._setup_handler()
        with patch("importlib.util.find_spec", return_value=None):
            result = await handler.handle_help("+1234", "")
        assert "nightwire Commands:" in result
        assert "Status:" in result

    async def test_help_with_known_command(self):
        handler, _, _ = self._setup_handler()
        result = await handler.handle_help("+1234", "select")
        assert "/select" in result
        assert "Usage:" in result
        assert "Examples:" in result

    async def test_help_with_unknown_command(self):
        handler, _, _ = self._setup_handler()
        result = await handler.handle_help("+1234", "nonexistent")
        assert "Unknown command" in result

    async def test_help_external_command_fallback(self):
        handler, registry, _ = self._setup_handler()
        registry.register_external({"remember": AsyncMock()})
        result = await handler.handle_help("+1234", "remember")
        assert "/remember" in result
        assert "full command list" in result


class TestSetupStatus:
    """Tests for setup status section in /help output."""

    async def test_help_includes_setup_status(self):
        from nightwire.commands.base import HandlerRegistry
        from nightwire.commands.core import CoreCommandHandler

        ctx = MagicMock()
        ctx.nightwire_runner = None
        ctx.plugin_loader = MagicMock()
        ctx.plugin_loader.get_all_help.return_value = []
        ctx.config = MagicMock()
        ctx.config.claude_path = "claude"
        ctx.config.sandbox_enabled = False
        registry = HandlerRegistry()
        ctx.registry = registry
        handler = CoreCommandHandler(ctx)
        registry.register(handler)

        with patch("importlib.util.find_spec", return_value=None):
            result = await handler.handle_help("+1234", "")
        assert "Status:" in result
        assert "Claude CLI:" in result
        assert "Embeddings:" in result

    async def test_setup_status_reflects_nightwire_runner(self):
        from nightwire.commands.base import HandlerRegistry
        from nightwire.commands.core import CoreCommandHandler

        ctx = MagicMock()
        ctx.nightwire_runner = MagicMock()  # Enabled
        ctx.plugin_loader = MagicMock()
        ctx.plugin_loader.get_all_help.return_value = []
        ctx.config = MagicMock()
        ctx.config.claude_path = "claude"
        ctx.config.sandbox_enabled = True
        registry = HandlerRegistry()
        ctx.registry = registry
        handler = CoreCommandHandler(ctx)
        registry.register(handler)

        # Mock find_spec to simulate both libs available
        with patch(
            "importlib.util.find_spec", return_value=MagicMock(),
        ):
            result = await handler.handle_help("+1234", "")
        assert "+ Nightwire Assistant: enabled" in result
        assert "+ Docker Sandbox: enabled" in result
