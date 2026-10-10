"""Pytest fixtures for the Timebutler tests.

HA-level tests need pytest-homeassistant-custom-component and run on Linux
or WSL only (Home Assistant cannot be imported on Windows).
"""
import pytest


@pytest.fixture
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA's loader find custom_components/timebutler (opt-in per module)."""
    yield


@pytest.fixture
def frozen_now(freezer):
    """Wednesday 2026-10-07, 10:00 in the test instance's time zone (US/Pacific)."""
    freezer.move_to("2026-10-07T17:00:00+00:00")
    return freezer
