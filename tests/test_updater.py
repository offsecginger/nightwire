"""Tests for auto-update feature."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path


class TestAutoUpdateConfig:
    """Tests for auto_update configuration properties."""

    def test_auto_update_disabled_by_default(self):
        from sidechannel.config import Config
        with patch.object(Config, '__init__', lambda self, **kw: None):
            config = Config.__new__(Config)
            config.settings = {}
            assert config.auto_update_enabled is False

    def test_auto_update_enabled_from_settings(self):
        from sidechannel.config import Config
        with patch.object(Config, '__init__', lambda self, **kw: None):
            config = Config.__new__(Config)
            config.settings = {"auto_update": {"enabled": True}}
            assert config.auto_update_enabled is True

    def test_auto_update_check_interval_default(self):
        from sidechannel.config import Config
        with patch.object(Config, '__init__', lambda self, **kw: None):
            config = Config.__new__(Config)
            config.settings = {}
            assert config.auto_update_check_interval == 21600

    def test_auto_update_check_interval_from_settings(self):
        from sidechannel.config import Config
        with patch.object(Config, '__init__', lambda self, **kw: None):
            config = Config.__new__(Config)
            config.settings = {"auto_update": {"check_interval": 3600}}
            assert config.auto_update_check_interval == 3600

    def test_auto_update_branch_default(self):
        from sidechannel.config import Config
        with patch.object(Config, '__init__', lambda self, **kw: None):
            config = Config.__new__(Config)
            config.settings = {}
            assert config.auto_update_branch == "main"

    def test_auto_update_branch_from_settings(self):
        from sidechannel.config import Config
        with patch.object(Config, '__init__', lambda self, **kw: None):
            config = Config.__new__(Config)
            config.settings = {"auto_update": {"branch": "develop"}}
            assert config.auto_update_branch == "develop"
