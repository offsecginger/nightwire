"""Tests for Milestone 14 Story 14.5: Signal ACI Binary Protocol Fix Infrastructure.

Validates infrastructure artifacts (compose files, Dockerfile, patch script,
checksums) and updater hook integration for the signal-cli ACI binary patches.
"""

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Repo root — tests/ is one level below
REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Docker compose file validation
# ---------------------------------------------------------------------------

class TestDockerComposePrepackaged:
    """Validate docker-compose.prepackaged.yml (DEFAULT path)."""

    @pytest.fixture
    def compose(self):
        path = REPO_ROOT / "docker-compose.prepackaged.yml"
        assert path.exists(), "docker-compose.prepackaged.yml must exist"
        with open(path) as f:
            return yaml.safe_load(f)

    def test_valid_yaml(self, compose):
        assert "services" in compose

    def test_uses_local_image(self, compose):
        image = compose["services"]["signal-api"]["image"]
        assert image == "nightwire-signal:latest"

    def test_healthcheck_present(self, compose):
        hc = compose["services"]["signal-api"]["healthcheck"]
        assert "test" in hc
        test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
        assert "curl" in test_cmd
        assert "/v1/about" in test_cmd

    def test_port_binding(self, compose):
        ports = compose["services"]["signal-api"]["ports"]
        assert "127.0.0.1:8080:8080" in ports


class TestDockerComposeUnpatched:
    """Validate docker-compose.unpatched.yml (vanilla fallback)."""

    @pytest.fixture
    def compose(self):
        path = REPO_ROOT / "docker-compose.unpatched.yml"
        assert path.exists(), "docker-compose.unpatched.yml must exist"
        with open(path) as f:
            return yaml.safe_load(f)

    def test_valid_yaml(self, compose):
        assert "services" in compose

    def test_uses_upstream_image(self, compose):
        image = compose["services"]["signal-api"]["image"]
        assert image == "bbernhard/signal-cli-rest-api:latest"

    def test_healthcheck_present(self, compose):
        hc = compose["services"]["signal-api"]["healthcheck"]
        test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
        assert "curl" in test_cmd

    def test_no_volume_mount_patch(self, compose):
        volumes = compose["services"]["signal-api"].get("volumes", [])
        for v in volumes:
            assert "signal-cli-0.13.24" not in str(v)


class TestDockerComposeVolumMount:
    """Validate docker-compose.yml (volume-mount fallback with healthcheck)."""

    @pytest.fixture
    def compose(self):
        path = REPO_ROOT / "docker-compose.yml"
        assert path.exists()
        with open(path) as f:
            return yaml.safe_load(f)

    def test_valid_yaml(self, compose):
        assert "services" in compose

    def test_has_volume_mount(self, compose):
        volumes = compose["services"]["signal-api"]["volumes"]
        volume_strs = [str(v) for v in volumes]
        assert any("signal-cli-0.13.24" in v for v in volume_strs)

    def test_has_java_opts(self, compose):
        env = compose["services"]["signal-api"]["environment"]
        env_strs = [str(e) for e in env]
        assert any("JAVA_OPTS" in e for e in env_strs)

    def test_healthcheck_present(self, compose):
        hc = compose["services"]["signal-api"]["healthcheck"]
        test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
        assert "curl" in test_cmd
        assert "/v1/about" in test_cmd


# ---------------------------------------------------------------------------
# Infrastructure file existence and content
# ---------------------------------------------------------------------------

class TestInfrastructureFiles:
    """Validate presence and content of infrastructure artifacts."""

    def test_dockerfile_signal_exists(self):
        path = REPO_ROOT / "Dockerfile.signal"
        assert path.exists()

    def test_dockerfile_signal_base_image(self):
        content = (REPO_ROOT / "Dockerfile.signal").read_text()
        assert "FROM bbernhard/signal-cli-rest-api" in content

    def test_dockerfile_signal_multiarch(self):
        content = (REPO_ROOT / "Dockerfile.signal").read_text()
        assert "aarch64" in content or "arm64" in content
        assert "x86_64" in content or "amd64" in content

    def test_patch_script_exists(self):
        path = REPO_ROOT / "scripts" / "apply-signal-patches.sh"
        assert path.exists()

    def test_patch_script_has_checksum_verification(self):
        content = (REPO_ROOT / "scripts" / "apply-signal-patches.sh").read_text()
        assert "sha256sum" in content or "shasum" in content
        assert "checksums.sha256" in content

    def test_patches_readme_exists(self):
        path = REPO_ROOT / "patches" / "signal-cli" / "README"
        assert path.exists()

    def test_provisioning_api_source_exists(self):
        path = REPO_ROOT / "patches" / "signal-cli" / "ProvisioningApi.java"
        assert path.exists()

    def test_provisioning_api_class_exists(self):
        path = REPO_ROOT / "patches" / "signal-cli" / "ProvisioningApi.class"
        assert path.exists()


# ---------------------------------------------------------------------------
# JAR checksum validation
# ---------------------------------------------------------------------------

class TestJarChecksums:
    """Validate bundled Turasa JAR checksums (SDL requirement)."""

    JAR_DIR = REPO_ROOT / "patches" / "signal-cli" / "jars"

    def test_checksums_file_exists(self):
        path = self.JAR_DIR / "checksums.sha256"
        assert path.exists()

    def test_checksums_file_format(self):
        content = (self.JAR_DIR / "checksums.sha256").read_text().strip()
        for line in content.splitlines():
            parts = line.split()
            assert len(parts) == 2, f"Invalid checksum line: {line}"
            sha, filename = parts
            assert len(sha) == 64, f"SHA256 must be 64 hex chars: {sha}"
            # filename may have leading * (binary mode indicator)
            assert filename.lstrip("*").endswith(".jar")

    def test_all_jars_have_checksums(self):
        content = (self.JAR_DIR / "checksums.sha256").read_text()
        for name in [
            "models-jvm-2.15.3_unofficial_138.jar",
            "signal-service-java-2.15.3_unofficial_138.jar",
            "util-jvm-2.15.3_unofficial_138.jar",
        ]:
            assert name in content, f"Missing checksum for {name}"

    def test_jar_checksums_match(self):
        checksum_file = self.JAR_DIR / "checksums.sha256"
        for line in checksum_file.read_text().strip().splitlines():
            expected_sha, filename = line.split()
            filename = filename.lstrip("*")
            jar_path = self.JAR_DIR / filename
            assert jar_path.exists(), f"JAR file missing: {filename}"
            actual_sha = hashlib.sha256(jar_path.read_bytes()).hexdigest()
            assert actual_sha == expected_sha, (
                f"Checksum mismatch for {filename}: "
                f"expected {expected_sha}, got {actual_sha}"
            )


# ---------------------------------------------------------------------------
# Updater hook integration
# ---------------------------------------------------------------------------

class TestUpdaterHooks:
    """Validate _run_post_update_hooks() integration in updater.py."""

    @pytest.fixture
    def updater(self, tmp_path):
        from nightwire.updater import AutoUpdater

        config = MagicMock()
        config.auto_update_branch = "main"
        config.auto_update_check_interval = 3600
        config.allowed_numbers = ["+1234567890"]

        send_msg = AsyncMock()
        return AutoUpdater(
            config, send_msg, repo_dir=tmp_path,
            shutdown_callback=MagicMock(),
        )

    async def test_hook_calls_script(self, updater, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        script = script_dir / "apply-signal-patches.sh"
        script.write_text("#!/bin/bash\nexit 0\n")

        with patch("nightwire.updater.asyncio.to_thread") as mock_thread:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_thread.return_value = mock_result
            await updater._run_post_update_hooks()

        mock_thread.assert_called_once()
        call_args = mock_thread.call_args
        assert call_args[0][0] == subprocess.run
        cmd = call_args[0][1]
        assert "bash" in cmd[0]
        assert "apply-signal-patches.sh" in cmd[1]

    async def test_hook_nonfatal_on_failure(self, updater, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        (script_dir / "apply-signal-patches.sh").write_text("#!/bin/bash\nexit 1\n")

        with patch("nightwire.updater.asyncio.to_thread") as mock_thread:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "patch failed"
            mock_thread.return_value = mock_result
            # Should not raise
            await updater._run_post_update_hooks()

    async def test_hook_skipped_when_no_script(self, updater):
        # repo_dir/scripts/apply-signal-patches.sh does not exist
        await updater._run_post_update_hooks()  # Should return silently

    async def test_hook_timeout(self, updater, tmp_path):
        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        (script_dir / "apply-signal-patches.sh").write_text("#!/bin/bash\nexit 0\n")

        with patch("nightwire.updater.asyncio.to_thread") as mock_thread:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_thread.return_value = mock_result
            await updater._run_post_update_hooks()

        # asyncio.to_thread(subprocess.run, cmd, **kwargs)
        # Verify timeout=120 is passed in the kwargs to subprocess.run
        call_args = mock_thread.call_args
        # Positional: (subprocess.run, ["bash", ...])
        # Keyword: capture_output=True, text=True, timeout=120
        assert call_args.kwargs.get("timeout") == 120

    async def test_shutdown_callback_called(self):
        from nightwire.updater import AutoUpdater

        config = MagicMock()
        config.auto_update_branch = "main"
        config.auto_update_check_interval = 3600
        config.allowed_numbers = ["+1234567890"]

        callback = MagicMock()
        updater = AutoUpdater(config, AsyncMock(), shutdown_callback=callback)
        assert updater._shutdown_callback is callback

    async def test_update_applied_flag(self):
        from nightwire.updater import AutoUpdater

        config = MagicMock()
        config.auto_update_branch = "main"
        config.auto_update_check_interval = 3600
        config.allowed_numbers = ["+1234567890"]

        updater = AutoUpdater(config, AsyncMock())
        assert updater.update_applied is False
