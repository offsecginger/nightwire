"""Tests for resource guard module."""

import pytest
from sidechannel.resource_guard import check_resources, ResourceStatus


def test_check_resources_returns_status():
    """check_resources should return a ResourceStatus."""
    status = check_resources()
    assert isinstance(status, ResourceStatus)
    assert isinstance(status.ok, bool)
    assert isinstance(status.memory_percent, float)
    assert isinstance(status.cpu_count, int)


def test_check_resources_has_reasonable_values():
    """Resource values should be in reasonable ranges."""
    status = check_resources()
    assert 0 <= status.memory_percent <= 100
    assert status.cpu_count >= 1
