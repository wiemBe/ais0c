# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the fork's unit tests."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastmcp import FastMCP

from qradar_mcp.fork import api_version as api_version_module
from qradar_mcp.fork.app import create_server
from qradar_mcp.fork.settings import Settings, load_settings

from .fake_qradar import CONSOLE_HOST, MCP_TOKEN, QRADAR_TOKEN, FakeQRadar

SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "snapshots"


def base_env(**overrides: str) -> dict[str, str]:
    env = {
        "QRADAR_CONSOLE_FQDN": CONSOLE_HOST,
        "QRADAR_AUTH_TOKEN": QRADAR_TOKEN,
        "MCP_AUTH_TOKEN": MCP_TOKEN,
    }
    env.update(overrides)
    return env


def make_server(
    profile: str, fake: FakeQRadar, settings: Settings, api_version: str = "29.0"
) -> FastMCP:
    return create_server(profile, settings, api_version, transport=fake.transport())


def route_discovery_to(monkeypatch: pytest.MonkeyPatch, transport: httpx.BaseTransport) -> None:
    """Send the CLI's API version discovery to ``transport`` instead of the network."""
    real_client = httpx.Client

    def client_with_transport(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(api_version_module.httpx, "Client", client_with_transport)


@pytest.fixture
def fake_qradar() -> FakeQRadar:
    return FakeQRadar()


@pytest.fixture
def settings() -> Settings:
    return load_settings(base_env())
