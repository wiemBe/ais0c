# SPDX-License-Identifier: Apache-2.0
"""Acceptance criterion 7: the server speaks streamable HTTP, behind a bearer token."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from qradar_mcp.fork import cli
from qradar_mcp.fork.app import create_http_app
from qradar_mcp.fork.settings import Settings

from .conftest import base_env, make_server, route_discovery_to
from .fake_qradar import MCP_TOKEN, OFFENSE_ID, QRADAR_TOKEN, FakeQRadar

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def base_url(settings: Settings, fake_qradar: FakeQRadar) -> Iterator[str]:
    app = create_http_app(make_server("qradar-read", fake_qradar, settings), settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=20)


@pytest.mark.asyncio
async def test_mcp_client_works_over_streamable_http(base_url: str) -> None:
    transport = StreamableHttpTransport(
        f"{base_url}/mcp", headers={"Authorization": f"Bearer {MCP_TOKEN}"}
    )
    async with Client(transport) as client:
        names = {tool.name for tool in await client.list_tools()}
        offense = await client.call_tool("get_offense", {"offense_id": OFFENSE_ID})
    assert "create_ariel_search" in names
    assert offense.structured_content["id"] == OFFENSE_ID


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer wrong-token",
        f"Basic {MCP_TOKEN}",
        MCP_TOKEN,
        f"Bearer {QRADAR_TOKEN}",
        "Bearer",
    ],
)
def test_requests_without_the_gateway_token_are_refused(
    base_url: str, authorization: str | None
) -> None:
    headers = dict(MCP_HEADERS)
    if authorization is not None:
        headers["Authorization"] = authorization
    response = httpx.post(f"{base_url}/mcp", json=INITIALIZE, headers=headers, timeout=10)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_gateway_token_is_accepted_on_raw_http(base_url: str) -> None:
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {MCP_TOKEN}"}
    response = httpx.post(f"{base_url}/mcp", json=INITIALIZE, headers=headers, timeout=10)
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "qradar-mcp"


def test_health_check_needs_no_token(base_url: str) -> None:
    response = httpx.get(f"{base_url}/healthz", timeout=10)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cli_serves_the_streamable_http_app(monkeypatch: pytest.MonkeyPatch) -> None:
    served: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        served["app"] = app
        served.update(kwargs)

    route_discovery_to(monkeypatch, FakeQRadar().transport())
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda *args, **kwargs: None)
    for name, value in base_env().items():
        monkeypatch.setenv(name, value)

    assert cli.main(["--profile", "qradar-note", "--host", "0.0.0.0", "--port", "5000"]) == 0
    assert served["host"] == "0.0.0.0"
    assert served["port"] == 5000
    assert served["log_config"] is None
    paths = {getattr(route, "path", None) for route in served["app"].routes}
    assert {"/mcp", "/healthz"} <= paths
