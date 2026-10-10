# SPDX-License-Identifier: Apache-2.0
"""Lab contract tests: every tool against a real lab QRadar (acceptance criterion 5).

Required: QRADAR_LAB_URL (console host or https URL) and QRADAR_LAB_TOKEN (authorized
service token). Without both, every test marked ``lab`` is skipped. Optional:
QRADAR_LAB_VERIFY_SSL (default true), REQUESTS_CA_BUNDLE, QRADAR_API_VERSIONS and
QRADAR_LAB_OFFENSE_ID (the offense of the note tools; QRadar cannot delete a note, so without it
no note is written, and get_offense_notes reads the first offense found).

Run in order and without xdist: later tools reuse IDs found by earlier ones.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Coroutine, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
import pytest
from fastmcp import FastMCP

from qradar_mcp.fork.api_version import ApiVersion, discover_api_version
from qradar_mcp.fork.app import create_server
from qradar_mcp.fork.settings import Settings, load_settings
from qradar_mcp.fork.tool_profiles import NOTE_PROFILE, READ_PROFILE

LAB_VARIABLES = ("QRADAR_LAB_URL", "QRADAR_LAB_TOKEN")
T = TypeVar("T")
LAB_REPORT: dict[str, str] = {}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    if all(os.environ.get(name) for name in LAB_VARIABLES):
        return
    skip = pytest.mark.skip(
        reason="lab QRadar not configured: set QRADAR_LAB_URL and QRADAR_LAB_TOKEN"
    )
    for item in items:
        if item.get_closest_marker("lab"):
            item.add_marker(skip)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if LAB_REPORT:
        terminalreporter.section("lab QRadar")
        for key, value in LAB_REPORT.items():
            terminalreporter.write_line(f"{key}: {value}")


@dataclass
class Lab:
    settings: Settings
    api_version: ApiVersion
    servers: dict[str, FastMCP]
    loop: asyncio.AbstractEventLoop
    found: dict[str, Any] = field(default_factory=dict)

    def run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        """Run on the session's single event loop, as the server does in production.

        Each server keeps one httpx client for its whole life. A fresh loop per call
        (asyncio.run) would hand it keep-alive connections bound to a closed loop.
        """
        return self.loop.run_until_complete(coroutine)

    def need(self, key: str) -> int | str:
        if key not in self.found:
            pytest.skip(f"no {key} available: the lab has none or an earlier step failed")
        return self.found[key]


@pytest.fixture(scope="session")
def lab() -> Iterator[Lab]:
    settings = load_settings(
        {
            "QRADAR_CONSOLE_FQDN": os.environ["QRADAR_LAB_URL"],
            "QRADAR_AUTH_TOKEN": os.environ["QRADAR_LAB_TOKEN"],
            # Not used: tests talk to the servers in memory, not over HTTP.
            "MCP_AUTH_TOKEN": secrets.token_hex(32),
            "QRADAR_VERIFY_SSL": os.environ.get("QRADAR_LAB_VERIFY_SSL", "true"),
            "REQUESTS_CA_BUNDLE": os.environ.get("REQUESTS_CA_BUNDLE", ""),
            "QRADAR_API_VERSIONS": os.environ.get("QRADAR_API_VERSIONS", ""),
        }
    )
    api_version = discover_api_version(settings)
    LAB_REPORT["API version"] = api_version.version + (
        " (deprecated)" if api_version.deprecated else ""
    )
    LAB_REPORT["QRadar"] = _qradar_release(settings, api_version.version)
    lab = Lab(
        settings=settings,
        api_version=api_version,
        servers={
            profile: create_server(profile, settings, api_version.version)
            for profile in (READ_PROFILE, NOTE_PROFILE)
        },
        loop=asyncio.new_event_loop(),
    )
    if os.environ.get("QRADAR_LAB_OFFENSE_ID"):
        lab.found["note_offense_id"] = int(os.environ["QRADAR_LAB_OFFENSE_ID"])
    try:
        yield lab
    finally:
        lab.loop.close()


def _qradar_release(settings: Settings, version: str) -> str:
    """Release name from /api/system/about, for the PR description."""
    with httpx.Client(verify=settings.ssl_verify(), timeout=settings.timeout_seconds) as client:
        response = client.get(
            f"https://{settings.console_host}/api/system/about",
            headers={
                "SEC": settings.qradar_token.get_secret_value(),
                "Version": version,
                "Accept": "application/json",
            },
        )
    if response.status_code != 200:
        return f"unknown (/api/system/about returned HTTP {response.status_code})"
    about = response.json()
    parts = [about.get("release_name") or about.get("external_version"), about.get("build_version")]
    return " / ".join(str(part) for part in parts if part)
