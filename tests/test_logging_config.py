"""Tests for nightwire logging configuration.

Tests cover:
- Secret sanitization processor (API keys, phone numbers, Bearer tokens)
- setup_logging() with and without config
- Subsystem file handler routing
- Log directory creation and fallback
"""

import logging
import logging.handlers
from pathlib import Path

import structlog

from nightwire.logging_config import (
    LOGGER_PREFIX,
    SUBSYSTEMS,
    _scrub_value,
    sanitize_secrets,
    setup_logging,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockConfig:
    """Minimal Config mock for setup_logging() tests."""

    def __init__(
        self,
        log_dir: Path = None,
        logging_level: str = "INFO",
        logging_subsystem_levels: dict = None,
        logging_max_file_size_mb: int = 10,
        logging_backup_count: int = 5,
    ):
        self.log_dir = log_dir or Path("/tmp/nightwire-test-logs")
        self.logging_level = logging_level
        self.logging_subsystem_levels = logging_subsystem_levels or {}
        self.logging_max_file_size_mb = logging_max_file_size_mb
        self.logging_backup_count = logging_backup_count


# ---------------------------------------------------------------------------
# sanitize_secrets processor tests
# ---------------------------------------------------------------------------

class TestScrubValue:
    """Test the internal _scrub_value helper."""

    def test_scrubs_anthropic_api_key(self):
        val = "key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        result = _scrub_value(val)
        assert "sk-ant" not in result
        assert "***REDACTED***" in result

    def test_scrubs_openai_api_key(self):
        val = "sk-proj-abcdefghijklmnopqrstuvwxyz1234"
        result = _scrub_value(val)
        assert "***REDACTED***" in result

    def test_scrubs_xai_grok_key(self):
        val = "xai-AbCdEfGhIjKlMnOpQrStUvWx"
        result = _scrub_value(val)
        assert "***REDACTED***" in result

    def test_scrubs_bearer_token(self):
        val = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload"
        result = _scrub_value(val)
        assert "eyJhbG" not in result
        assert "***REDACTED***" in result

    def test_masks_phone_number_e164(self):
        val = "caller: +12125551234"
        result = _scrub_value(val)
        assert "+12125551234" not in result
        assert "...1234" in result

    def test_masks_long_international_phone(self):
        val = "+447911123456 called"
        result = _scrub_value(val)
        assert "+447911123456" not in result
        assert "...3456" in result

    def test_preserves_already_masked_phone(self):
        val = "phone: ...1234"
        result = _scrub_value(val)
        assert result == "phone: ...1234"

    def test_preserves_short_sk_prefix(self):
        """sk- followed by <20 chars is NOT an API key."""
        val = "sk-short"
        result = _scrub_value(val)
        assert result == "sk-short"

    def test_preserves_normal_text(self):
        val = "Hello world, task completed successfully"
        result = _scrub_value(val)
        assert result == val

    def test_preserves_short_numbers(self):
        """Short numbers (+1234) are not phone numbers."""
        val = "code: +1234"
        result = _scrub_value(val)
        assert result == val


class TestSanitizeSecrets:
    """Test the structlog sanitize_secrets processor."""

    def test_scrubs_string_values(self):
        event = {
            "event": "api_call",
            "key": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        }
        result = sanitize_secrets(None, None, event)
        assert "***REDACTED***" in result["key"]
        assert result["event"] == "api_call"

    def test_scrubs_phone_in_event(self):
        event = {"event": "test", "phone": "+12125551234"}
        result = sanitize_secrets(None, None, event)
        assert result["phone"] == "...1234"

    def test_preserves_non_string_values(self):
        event = {"event": "test", "count": 42, "flag": True, "data": None}
        result = sanitize_secrets(None, None, event)
        assert result["count"] == 42
        assert result["flag"] is True
        assert result["data"] is None

    def test_scrubs_strings_in_lists(self):
        event = {
            "event": "test",
            "values": ["sk-ant-api03-secret1234567890abcdef", "safe"],
        }
        result = sanitize_secrets(None, None, event)
        assert "***REDACTED***" in result["values"][0]
        assert result["values"][1] == "safe"

    def test_scrubs_strings_in_tuples(self):
        event = {
            "event": "test",
            "values": ("+12125551234", "ok"),
        }
        result = sanitize_secrets(None, None, event)
        assert isinstance(result["values"], tuple)
        assert "...1234" in result["values"][0]

    def test_scrubs_secrets_in_nested_dicts(self):
        event = {
            "event": "test",
            "headers": {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload",
                "Content-Type": "application/json",
            },
        }
        result = sanitize_secrets(None, None, event)
        assert "eyJhbG" not in str(result["headers"])
        assert "***REDACTED***" in result["headers"]["Authorization"]
        assert result["headers"]["Content-Type"] == "application/json"

    def test_scrubs_phone_in_nested_dict(self):
        event = {
            "event": "test",
            "context": {"sender": "+12125551234", "count": 5},
        }
        result = sanitize_secrets(None, None, event)
        assert result["context"]["sender"] == "...1234"
        assert result["context"]["count"] == 5

    def test_multiple_secrets_in_one_string(self):
        val = "key=sk-ant-api03-abcdefghijklmnopqrst phone=+12125551234"
        event = {"event": "test", "msg": val}
        result = sanitize_secrets(None, None, event)
        assert "sk-ant" not in result["msg"]
        assert "+1212" not in result["msg"]
        assert "***REDACTED***" in result["msg"]
        assert "...1234" in result["msg"]


# ---------------------------------------------------------------------------
# setup_logging() tests
# ---------------------------------------------------------------------------

class TestSetupLogging:
    """Test logging infrastructure setup."""

    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "logs"
        config = MockConfig(log_dir=log_dir)
        setup_logging(config)
        assert log_dir.exists()

    def test_creates_combined_log_file(self, tmp_path):
        log_dir = tmp_path / "logs"
        config = MockConfig(log_dir=log_dir)
        setup_logging(config)

        # Log an event to trigger file creation
        stdlib_logger = logging.getLogger(f"{LOGGER_PREFIX}.bot")
        stdlib_logger.info("test_combined")

        combined = log_dir / "nightwire.log"
        assert combined.exists()
        content = combined.read_text()
        assert "test_combined" in content

    def test_creates_subsystem_log_files(self, tmp_path):
        log_dir = tmp_path / "logs"
        config = MockConfig(log_dir=log_dir)
        setup_logging(config)

        for subsystem in SUBSYSTEMS:
            stdlib_logger = logging.getLogger(
                f"{LOGGER_PREFIX}.{subsystem}"
            )
            stdlib_logger.info(f"test_{subsystem}")

        for subsystem in SUBSYSTEMS:
            log_file = log_dir / f"{subsystem}.log"
            assert log_file.exists(), f"{subsystem}.log not created"
            content = log_file.read_text()
            assert f"test_{subsystem}" in content

    def test_subsystem_events_propagate_to_combined(self, tmp_path):
        log_dir = tmp_path / "logs"
        config = MockConfig(log_dir=log_dir)
        setup_logging(config)

        stdlib_logger = logging.getLogger(f"{LOGGER_PREFIX}.bot")
        stdlib_logger.info("bot_event_123")

        stdlib_logger2 = logging.getLogger(f"{LOGGER_PREFIX}.claude")
        stdlib_logger2.info("claude_event_456")

        combined = (log_dir / "nightwire.log").read_text()
        assert "bot_event_123" in combined
        assert "claude_event_456" in combined

    def test_subsystem_level_override(self, tmp_path):
        log_dir = tmp_path / "logs"
        config = MockConfig(
            log_dir=log_dir,
            logging_subsystem_levels={"autonomous": "DEBUG"},
        )
        setup_logging(config)

        auto_logger = logging.getLogger(f"{LOGGER_PREFIX}.autonomous")
        auto_logger.debug("debug_event_auto")

        content = (log_dir / "autonomous.log").read_text()
        assert "debug_event_auto" in content

    def test_default_level_filters_debug(self, tmp_path):
        """With default INFO level, debug events should not appear in files."""
        log_dir = tmp_path / "logs"
        config = MockConfig(log_dir=log_dir, logging_level="INFO")
        setup_logging(config)

        bot_logger = logging.getLogger(f"{LOGGER_PREFIX}.bot")
        bot_logger.debug("should_not_appear")
        bot_logger.info("should_appear")

        content = (log_dir / "bot.log").read_text()
        assert "should_not_appear" not in content
        assert "should_appear" in content

    def test_setup_without_config_uses_defaults(self, tmp_path, monkeypatch):
        """setup_logging() without config should not crash."""
        # Monkeypatch the default log_dir to use tmp_path
        monkeypatch.setattr(
            "nightwire.logging_config.Path",
            lambda *a: tmp_path / "logs" if not a else Path(*a),
        )
        # Just verify it doesn't raise
        setup_logging()

    def test_fallback_on_bad_log_dir(self, tmp_path, capsys):
        """If log_dir creation fails, fall back to console-only."""
        # Use a path that can't be created (file exists where dir should be)
        blocker = tmp_path / "blocker"
        blocker.write_text("I'm a file")
        bad_dir = blocker / "subdir"  # Can't mkdir under a file

        config = MockConfig(log_dir=bad_dir)
        # Should not raise
        setup_logging(config)

        stderr = capsys.readouterr().err
        assert "WARNING" in stderr or "Cannot create" in stderr

    def test_structlog_configured_with_sanitize_secrets(self, tmp_path):
        """sanitize_secrets should be in the structlog processor chain."""
        log_dir = tmp_path / "logs"
        config = MockConfig(log_dir=log_dir)
        setup_logging(config)

        # Verify by checking structlog's configuration
        cfg = structlog.get_config()
        processor_names = [p.__name__ if hasattr(p, '__name__') else str(p)
                          for p in cfg["processors"]]
        assert "sanitize_secrets" in processor_names

    def test_cache_logger_false_without_config(self):
        """First call (no config) should set cache_logger_on_first_use=False."""
        setup_logging()
        cfg = structlog.get_config()
        assert cfg["cache_logger_on_first_use"] is False

    def test_cache_logger_true_with_config(self, tmp_path):
        """Second call (with config) should set cache_logger_on_first_use=True."""
        log_dir = tmp_path / "logs"
        config = MockConfig(log_dir=log_dir)
        setup_logging(config)
        cfg = structlog.get_config()
        assert cfg["cache_logger_on_first_use"] is True
