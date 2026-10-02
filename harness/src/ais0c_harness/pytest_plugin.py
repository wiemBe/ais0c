"""Pytest plugin for every test suite in the repository.

The root ``pyproject.toml`` loads it with ``-p ais0c_harness.pytest_plugin``. It registers
the ``lab`` marker for tests that talk to the lab QRadar and skips those tests unless the
lab connection settings are set in the environment. The settings are never written to
the repository.
"""

import os

import pytest

LAB_MARKER = "lab"
LAB_ENV_VARS = ("QRADAR_LAB_URL", "QRADAR_LAB_TOKEN")


def missing_lab_env_vars() -> list[str]:
    """Return the lab settings that are unset or empty."""
    return [name for name in LAB_ENV_VARS if not os.environ.get(name)]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{LAB_MARKER}: talks to the lab QRadar; skipped unless {' and '.join(LAB_ENV_VARS)} are set",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    missing = missing_lab_env_vars()
    if not missing:
        return
    skip = pytest.mark.skip(reason=f"lab test: {', '.join(missing)} not set")
    for item in items:
        if item.get_closest_marker(LAB_MARKER) is not None:
            item.add_marker(skip)
