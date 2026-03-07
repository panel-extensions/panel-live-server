"""Test configuration for panel-live-server."""

import pytest


def pytest_addoption(parser):
    """Add custom CLI options for pytest."""
    parser.addoption("--ui", action="store_true", default=False, help="Run UI tests")
    parser.addoption("--slow", action="store_true", default=False, help="Run slow tests")


def pytest_collection_modifyitems(config, items):
    """Skip UI and slow tests unless explicitly enabled."""
    if not config.getoption("--ui"):
        skip_ui = pytest.mark.skip(reason="Need --ui option to run")
        for item in items:
            if "ui" in item.keywords:
                item.add_marker(skip_ui)

    if not config.getoption("--slow"):
        skip_slow = pytest.mark.skip(reason="Need --slow option to run")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)
