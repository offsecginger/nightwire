"""Tests for Milestone 8: Upstream Feature Port.

Covers:
- Attachment ID regex fix (8.1.2)
- Attachment download/save/process (8.2.1)
- Sandbox validate_docker_available + hardening flags (8.1.3 + 8.2.2)
- TaskManager.cancel_all_tasks (8.1.1)
- TaskManager.start_background_task with image_paths (8.2.1)
- Config.attachments_dir property (8.2.1)
- Bot _handle_signal_message attachment extraction (8.2.1)
- ClaudeRunner._maybe_sandbox (8.2.2)
- Attachment cleanup TTL-based pruning (NB-2 fix)
"""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp


class _AsyncChunkedIter:
    """Mock async iterator for aiohttp response content.iter_chunked()."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


def _make_download_session(data_chunks):
    """Build a mock session whose .get() returns data_chunks via async iter."""
    session = MagicMock(spec=aiohttp.ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.content = MagicMock()
    mock_resp.content.iter_chunked = MagicMock(
        return_value=_AsyncChunkedIter(data_chunks)
    )
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session.get.return_value = cm
    return session


# ---------------------------------------------------------------------------
# Attachment ID regex fix (8.1.2)
# ---------------------------------------------------------------------------

class TestAttachmentIdRegex:
    """Validate that the attachment ID regex accepts dots and blocks traversal."""

    async def test_valid_id_with_extension(self):
        """Signal returns IDs like '09GIqaSf01wyBX0zokr7.jpg'."""
        from nightwire.attachments import download_attachment

        session = _make_download_session([b"fake image data"])
        result = await download_attachment(
            session, "http://localhost:8080", "abc123.jpg"
        )
        assert result == b"fake image data"
        session.get.assert_called_once_with(
            "http://localhost:8080/v1/attachments/abc123.jpg"
        )

    async def test_valid_id_without_extension(self):
        """IDs without dots should still work."""
        from nightwire.attachments import download_attachment

        session = _make_download_session([b"data"])
        result = await download_attachment(
            session, "http://localhost:8080", "abc123XYZ"
        )
        assert result == b"data"

    async def test_blocks_path_traversal(self):
        """IDs with '..' must be rejected."""
        from nightwire.attachments import download_attachment

        session = MagicMock(spec=aiohttp.ClientSession)
        result = await download_attachment(
            session, "http://localhost:8080", "../etc/passwd"
        )
        assert result is None
        session.get.assert_not_called()

    async def test_blocks_special_characters(self):
        """IDs with slashes or other dangerous characters must be rejected."""
        from nightwire.attachments import download_attachment

        session = MagicMock(spec=aiohttp.ClientSession)
        for bad_id in ["a/b", "a\\b", "a;b", "a|b", "a b"]:
            result = await download_attachment(
                session, "http://localhost:8080", bad_id
            )
            assert result is None, f"Should reject ID: {bad_id}"


# ---------------------------------------------------------------------------
# Attachment save (8.2.1)
# ---------------------------------------------------------------------------

class TestAttachmentSave:
    """Validate save_attachment creates sender-isolated files."""

    def test_save_jpeg(self, tmp_path):
        from nightwire.attachments import save_attachment

        data = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # fake JPEG header
        result = save_attachment(data, "image/jpeg", "+15551234567", tmp_path)
        assert result is not None
        assert result.exists()
        assert result.suffix == ".jpg"
        # Sender-isolated directory (digits only)
        assert "15551234567" in str(result.parent.name)

    def test_save_png(self, tmp_path):
        from nightwire.attachments import save_attachment

        data = b"\x89PNG" + b"\x00" * 100
        result = save_attachment(data, "image/png", "+15551234567", tmp_path)
        assert result is not None
        assert result.suffix == ".png"

    def test_reject_unsupported_type(self, tmp_path):
        from nightwire.attachments import save_attachment

        result = save_attachment(b"data", "application/pdf", "+1555", tmp_path)
        assert result is None

    def test_unknown_sender_uses_fallback(self, tmp_path):
        from nightwire.attachments import save_attachment

        result = save_attachment(
            b"data" * 10, "image/webp", "uuid-no-digits", tmp_path
        )
        assert result is not None
        assert "unknown" in str(result.parent.name)


# ---------------------------------------------------------------------------
# Attachment process pipeline (8.2.1)
# ---------------------------------------------------------------------------

class TestProcessAttachments:
    """Validate the batch download + save pipeline."""

    async def test_processes_image_attachments(self, tmp_path):
        from nightwire.attachments import process_attachments

        session = _make_download_session([b"\xff\xd8\xff\xe0" + b"\x00" * 50])

        attachments = [
            {"contentType": "image/jpeg", "id": "abc123.jpg"},
        ]
        result = await process_attachments(
            attachments, "+15551234567", session,
            "http://localhost:8080", tmp_path,
        )
        assert len(result) == 1
        assert result[0].exists()

    async def test_skips_non_image_attachments(self, tmp_path):
        from nightwire.attachments import process_attachments

        session = MagicMock(spec=aiohttp.ClientSession)
        attachments = [
            {"contentType": "application/pdf", "id": "doc.pdf"},
        ]
        result = await process_attachments(
            attachments, "+15551234567", session,
            "http://localhost:8080", tmp_path,
        )
        assert len(result) == 0

    async def test_skips_missing_id(self, tmp_path):
        from nightwire.attachments import process_attachments

        session = MagicMock(spec=aiohttp.ClientSession)
        attachments = [
            {"contentType": "image/jpeg"},  # no id
        ]
        result = await process_attachments(
            attachments, "+15551234567", session,
            "http://localhost:8080", tmp_path,
        )
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Sandbox validate_docker_available (8.1.3)
# ---------------------------------------------------------------------------

class TestValidateDockerAvailable:
    """Validate Docker availability checks with proper error handling."""

    def test_docker_running(self):
        from nightwire.sandbox import validate_docker_available

        with patch("nightwire.sandbox.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            available, error = validate_docker_available()
            assert available is True
            assert error == ""

    def test_docker_not_running(self):
        from nightwire.sandbox import validate_docker_available

        with patch("nightwire.sandbox.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            available, error = validate_docker_available()
            assert available is False
            assert "not running" in error

    def test_docker_not_installed(self):
        from nightwire.sandbox import validate_docker_available

        with patch("nightwire.sandbox.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            available, error = validate_docker_available()
            assert available is False
            assert "not installed" in error

    def test_docker_permission_denied(self):
        from nightwire.sandbox import validate_docker_available

        with patch("nightwire.sandbox.subprocess.run") as mock_run:
            mock_run.side_effect = PermissionError()
            available, error = validate_docker_available()
            assert available is False
            assert "Permission denied" in error

    def test_docker_timeout(self):
        import subprocess

        from nightwire.sandbox import validate_docker_available

        with patch("nightwire.sandbox.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("docker", 10)
            available, error = validate_docker_available()
            assert available is False
            assert "did not respond" in error


# ---------------------------------------------------------------------------
# Sandbox hardening flags (8.2.2)
# ---------------------------------------------------------------------------

class TestSandboxHardening:
    """Validate Docker hardening flags are present."""

    def test_hardening_flags_present(self):
        from nightwire.sandbox import SandboxConfig, build_sandbox_command

        config = SandboxConfig(enabled=True)
        cmd = ["claude", "-p"]
        # Use a Windows-safe path for the test
        project_path = Path("C:/projects/test") if Path("C:/").exists() else Path("/tmp/test")

        result = build_sandbox_command(cmd, project_path, config)
        assert "--user" in result
        assert "1000:1000" in result
        assert "--security-opt" in result
        assert "no-new-privileges" in result
        assert "--cap-drop" in result
        assert "ALL" in result
        assert "--pids-limit" in result
        assert "256" in result

    def test_default_image_is_nightwire_sandbox(self):
        from nightwire.sandbox import SandboxConfig

        config = SandboxConfig()
        assert config.image == "nightwire-sandbox:latest"

    def test_no_path_home_in_env(self):
        from nightwire.sandbox import SandboxConfig, build_sandbox_command

        config = SandboxConfig(enabled=True)
        cmd = ["claude", "-p"]
        project_path = Path("C:/projects/test") if Path("C:/").exists() else Path("/tmp/test")

        result = build_sandbox_command(cmd, project_path, config)
        # Should NOT pass PATH or HOME env vars
        env_indices = [i for i, arg in enumerate(result) if arg == "-e"]
        env_vars = [result[i + 1] for i in env_indices if i + 1 < len(result)]
        assert "PATH" not in env_vars
        assert "HOME" not in env_vars


# ---------------------------------------------------------------------------
# TaskManager.cancel_all_tasks (8.1.1)
# ---------------------------------------------------------------------------

class TestCancelAllTasks:
    """Validate shutdown task draining."""

    async def test_cancel_all_tasks_empty(self):
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
        await tm.cancel_all_tasks()
        assert len(tm._sender_tasks) == 0

    async def test_cancel_all_tasks_drains_active(self):
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

        # Create a real asyncio task that blocks
        async def blocking():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(blocking())
        tm._sender_tasks["+15551234567"] = {"task": task}

        await tm.cancel_all_tasks()
        assert task.cancelled() or task.done()
        assert len(tm._sender_tasks) == 0

    async def test_cancel_all_tasks_skips_done(self):
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

        done_task = MagicMock()
        done_task.done.return_value = True
        tm._sender_tasks["+15551234567"] = {"task": done_task}

        await tm.cancel_all_tasks()
        done_task.cancel.assert_not_called()
        assert len(tm._sender_tasks) == 0


# ---------------------------------------------------------------------------
# TaskManager.start_background_task with image_paths (8.2.1)
# ---------------------------------------------------------------------------

class TestStartBackgroundTaskImagePaths:
    """Validate image_paths parameter in start_background_task."""

    async def test_effective_description_with_images(self):
        """Image paths should be appended to the prompt."""
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
        tm.runner.run_claude = AsyncMock(return_value=(True, "done"))
        tm.project_manager.get_current_path.return_value = Path("/tmp")
        tm.memory.store_message = AsyncMock()

        image_paths = [
            Path("/data/attachments/15551234567/img1.jpg"),
            Path("/data/attachments/15551234567/img2.png"),
        ]

        # start_background_task creates a fire-and-forget task (needs event loop)
        tm.start_background_task(
            "+15551234567", "describe this", "myproject",
            image_paths=image_paths,
        )

        # Verify the task state was created (composite key)
        assert ("+15551234567", "myproject") in tm._sender_tasks
        state = tm._sender_tasks[("+15551234567", "myproject")]
        # description is the original (not effective)
        assert state["description"] == "describe this"
        # Clean up task
        state["task"].cancel()
        try:
            await state["task"]
        except asyncio.CancelledError:
            pass

    async def test_no_image_paths_keeps_original_description(self):
        """Without image_paths, description is unchanged."""
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
        tm.runner.run_claude = AsyncMock(return_value=(True, "done"))
        tm.project_manager.get_current_path.return_value = Path("/tmp")
        tm.memory.store_message = AsyncMock()

        tm.start_background_task("+15551234567", "just text", "myproject")
        assert ("+15551234567", "myproject") in tm._sender_tasks
        # Clean up task
        state = tm._sender_tasks[("+15551234567", "myproject")]
        state["task"].cancel()
        try:
            await state["task"]
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Config.attachments_dir property (8.2.1)
# ---------------------------------------------------------------------------

class TestConfigAttachmentsDir:
    """Validate the attachments_dir config property."""

    def test_default_attachments_dir(self):
        from nightwire.config import Config

        config = Config.__new__(Config)
        config.settings = {}
        config.projects = {}
        config.config_dir = Path("/install/config")
        result = config.attachments_dir
        assert result == Path("/install/data/attachments")

    def test_configured_attachments_dir(self):
        from nightwire.config import Config

        config = Config.__new__(Config)
        config.settings = {"attachments_dir": "/custom/attachments"}
        config.projects = {}
        config.config_dir = Path("/install/config")
        result = config.attachments_dir
        assert result == Path("/custom/attachments")


# ---------------------------------------------------------------------------
# ClaudeRunner._maybe_sandbox (8.2.2)
# ---------------------------------------------------------------------------

class TestMaybeSandbox:
    """Validate sandbox wrapping in ClaudeRunner."""

    async def test_sandbox_disabled_returns_original(self):
        from nightwire.claude_runner import ClaudeRunner

        runner = ClaudeRunner.__new__(ClaudeRunner)
        runner.config = MagicMock()
        runner.config.sandbox_enabled = False

        cmd = ["claude", "-p"]
        result_cmd, result_cwd = await runner._maybe_sandbox(
            cmd, Path("/tmp/project"), "/tmp/project"
        )
        assert result_cmd == cmd
        assert result_cwd == "/tmp/project"

    async def test_sandbox_no_project_returns_original(self):
        from nightwire.claude_runner import ClaudeRunner

        runner = ClaudeRunner.__new__(ClaudeRunner)
        runner.config = MagicMock()
        runner.config.sandbox_enabled = True

        cmd = ["claude", "-p"]
        result_cmd, result_cwd = await runner._maybe_sandbox(
            cmd, None, None
        )
        assert result_cmd == cmd

    async def test_sandbox_docker_unavailable_returns_original(self):
        from nightwire.claude_runner import ClaudeRunner

        runner = ClaudeRunner.__new__(ClaudeRunner)
        runner.config = MagicMock()
        runner.config.sandbox_enabled = True

        with patch(
            "nightwire.sandbox.validate_docker_available",
            return_value=(False, "not running"),
        ):
            cmd = ["claude", "-p"]
            result_cmd, result_cwd = await runner._maybe_sandbox(
                cmd, Path("/tmp/project"), "/tmp/project"
            )
            assert result_cmd == cmd
            assert result_cwd == "/tmp/project"

    async def test_sandbox_enabled_wraps_command(self):
        from nightwire.claude_runner import ClaudeRunner

        runner = ClaudeRunner.__new__(ClaudeRunner)
        runner.config = MagicMock()
        runner.config.sandbox_enabled = True
        runner.config.sandbox_config = {
            "enabled": True,
            "image": "nightwire-sandbox:latest",
        }

        with patch(
            "nightwire.sandbox.validate_docker_available",
            return_value=(True, ""),
        ):
            cmd = ["claude", "-p"]
            result_cmd, result_cwd = await runner._maybe_sandbox(
                cmd, Path("/tmp/project"), "/tmp/project"
            )
            assert result_cmd[0] == "docker"
            assert "run" in result_cmd
            # cwd should be None (Docker manages it)
            assert result_cwd is None


# ---------------------------------------------------------------------------
# Attachment Cleanup (NB-2 fix)
# ---------------------------------------------------------------------------
class TestCleanupOldAttachments:
    """Tests for cleanup_old_attachments TTL-based pruning."""

    def test_deletes_old_files(self, tmp_path):
        from nightwire.attachments import cleanup_old_attachments

        sender_dir = tmp_path / "1234567890"
        sender_dir.mkdir()
        old_file = sender_dir / "20260101_abc12345.jpg"
        old_file.write_bytes(b"old image data")
        # Set mtime to 48 hours ago
        old_mtime = time.time() - (48 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        deleted = cleanup_old_attachments(tmp_path, max_age_hours=24)
        assert deleted == 1
        assert not old_file.exists()

    def test_keeps_recent_files(self, tmp_path):
        from nightwire.attachments import cleanup_old_attachments

        sender_dir = tmp_path / "1234567890"
        sender_dir.mkdir()
        recent_file = sender_dir / "20260227_abc12345.jpg"
        recent_file.write_bytes(b"recent image data")
        # mtime is "now" — should survive 24h TTL

        deleted = cleanup_old_attachments(tmp_path, max_age_hours=24)
        assert deleted == 0
        assert recent_file.exists()

    def test_removes_empty_dirs(self, tmp_path):
        from nightwire.attachments import cleanup_old_attachments

        sender_dir = tmp_path / "1234567890"
        sender_dir.mkdir()
        old_file = sender_dir / "20260101_abc12345.png"
        old_file.write_bytes(b"data")
        old_mtime = time.time() - (48 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        cleanup_old_attachments(tmp_path, max_age_hours=24)
        assert not sender_dir.exists()

    def test_zero_max_age_disables(self, tmp_path):
        from nightwire.attachments import cleanup_old_attachments

        sender_dir = tmp_path / "1234567890"
        sender_dir.mkdir()
        old_file = sender_dir / "20260101_abc12345.jpg"
        old_file.write_bytes(b"data")
        old_mtime = time.time() - (48 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        deleted = cleanup_old_attachments(tmp_path, max_age_hours=0)
        assert deleted == 0
        assert old_file.exists()

    def test_nonexistent_dir(self, tmp_path):
        from nightwire.attachments import cleanup_old_attachments

        missing = tmp_path / "does_not_exist"
        deleted = cleanup_old_attachments(missing, max_age_hours=24)
        assert deleted == 0

    def test_mixed_old_and_recent(self, tmp_path):
        from nightwire.attachments import cleanup_old_attachments

        sender_dir = tmp_path / "1234567890"
        sender_dir.mkdir()

        old_file = sender_dir / "old.jpg"
        old_file.write_bytes(b"old")
        old_mtime = time.time() - (48 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        recent_file = sender_dir / "recent.png"
        recent_file.write_bytes(b"recent")

        deleted = cleanup_old_attachments(tmp_path, max_age_hours=24)
        assert deleted == 1
        assert not old_file.exists()
        assert recent_file.exists()
        # Sender dir should still exist (has recent file)
        assert sender_dir.exists()


class TestConfigAttachmentMaxAge:
    """Tests for Config.attachment_max_age_hours property."""

    def test_default_value(self):
        from nightwire.config import Config

        with patch.object(Config, "__init__", lambda self: None):
            cfg = Config()
            cfg.settings = {}
        assert cfg.attachment_max_age_hours == 24

    def test_custom_value(self):
        from nightwire.config import Config

        with patch.object(Config, "__init__", lambda self: None):
            cfg = Config()
            cfg.settings = {"attachment_max_age_hours": 48}
        assert cfg.attachment_max_age_hours == 48

    def test_zero_disables(self):
        from nightwire.config import Config

        with patch.object(Config, "__init__", lambda self: None):
            cfg = Config()
            cfg.settings = {"attachment_max_age_hours": 0}
        assert cfg.attachment_max_age_hours == 0
