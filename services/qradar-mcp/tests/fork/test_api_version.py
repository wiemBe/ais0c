# SPDX-License-Identifier: Apache-2.0
"""Acceptance criterion 1: API version discovery and the Version header."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastmcp import Client

from qradar_mcp.fork import api_version, cli
from qradar_mcp.fork.api_version import ApiVersion, ApiVersionError, select_api_version
from qradar_mcp.fork.settings import Settings, load_settings

from .conftest import base_env, make_server, route_discovery_to
from .fake_qradar import OFFENSE_ID, QRADAR_TOKEN, FakeQRadar

KNOWN = ("27.0", "29.0")


def _versions(*entries: tuple[str, bool, bool]) -> list[dict[str, Any]]:
    return [
        {"id": i, "version": v, "deprecated": deprecated, "removed": removed}
        for i, (v, deprecated, removed) in enumerate(entries)
    ]


def test_selects_highest_known_version_qradar_offers() -> None:
    offered = _versions(
        ("26.0", True, False),
        ("27.0", False, False),
        ("29.0", False, False),
        ("30.0", False, False),
    )
    assert select_api_version(offered, KNOWN) == ApiVersion("29.0", deprecated=False)


def test_falls_back_to_lower_known_version() -> None:
    offered = _versions(("26.0", False, False), ("27.0", False, False), ("28.0", False, False))
    assert select_api_version(offered, KNOWN).version == "27.0"


def test_removed_versions_are_not_used() -> None:
    offered = _versions(("27.0", False, False), ("29.0", True, True))
    assert select_api_version(offered, KNOWN).version == "27.0"


def test_deprecated_version_is_used_and_reported() -> None:
    offered = _versions(("27.0", True, False))
    assert select_api_version(offered, KNOWN) == ApiVersion("27.0", deprecated=True)


def test_known_list_order_does_not_matter() -> None:
    offered = _versions(("27.0", False, False), ("29.0", False, False))
    assert select_api_version(offered, ("29.0", "27.0")).version == "29.0"


def test_refuses_when_no_known_version_is_offered() -> None:
    offered = _versions(("25.0", False, False), ("26.0", False, False), ("30.0", False, False))
    with pytest.raises(ApiVersionError, match="none of the known API versions"):
        select_api_version(offered, KNOWN)


@pytest.mark.parametrize(
    "body",
    [{"version": "29.0"}, "29.0", None, [], [{"id": 1}], [{"version": 29.0}], ["29.0"]],
)
def test_refuses_malformed_version_lists(body: object) -> None:
    with pytest.raises(ApiVersionError):
        select_api_version(body, KNOWN)


def _transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_discovery_sends_token_and_no_version_header(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_versions(("27.0", False, False), ("29.0", False, False)))

    selected = api_version.discover_api_version(settings, transport=_transport(handler))

    assert selected.version == "29.0"
    [request] = seen
    assert request.method == "GET"
    assert str(request.url) == "https://qradar.example.com/api/help/versions"
    assert request.headers["SEC"] == QRADAR_TOKEN
    assert "Version" not in request.headers


@pytest.mark.parametrize("status", [401, 403])
def test_discovery_reports_rejected_token_without_echoing_it(
    settings: Settings, status: int
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "unauthorized"})

    with pytest.raises(ApiVersionError, match=f"HTTP {status}") as raised:
        api_version.discover_api_version(settings, transport=_transport(handler))
    assert QRADAR_TOKEN not in str(raised.value)


def test_discovery_reports_unreachable_console(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ApiVersionError, match="cannot reach QRadar"):
        api_version.discover_api_version(settings, transport=_transport(handler))


def test_discovery_reports_non_json_body(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>login</html>")

    with pytest.raises(ApiVersionError, match="did not return JSON"):
        api_version.discover_api_version(settings, transport=_transport(handler))


def test_known_versions_come_from_configuration() -> None:
    settings = load_settings(base_env(QRADAR_API_VERSIONS="28.0"))
    offered = _versions(("27.0", False, False), ("28.0", False, False), ("29.0", False, False))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=offered)

    assert (
        api_version.discover_api_version(settings, transport=_transport(handler)).version == "28.0"
    )


@pytest.mark.asyncio
async def test_every_qradar_request_carries_the_negotiated_version(settings: Settings) -> None:
    fake = FakeQRadar()
    selected = api_version.discover_api_version(settings, transport=fake.transport())
    server = make_server("qradar-read", fake, settings, api_version=selected.version)

    async with Client(server) as client:
        await client.call_tool("get_offense", {"offense_id": OFFENSE_ID})
        await client.call_tool("list_rules", {"limit": 5})
        search = await client.call_tool(
            "create_ariel_search", {"query_expression": "SELECT * FROM events LAST 5 MINUTES"}
        )
        search_id = search.structured_content["search_id"]
        await client.call_tool("get_ariel_search_status", {"search_id": search_id})
        await client.call_tool("get_ariel_search_results", {"search_id": search_id})
        await client.call_tool("delete_ariel_search", {"search_id": search_id})

    requests = fake.api_requests()
    assert {r.method for r in requests} == {"GET", "POST", "DELETE"}
    assert [r.headers.get("Version") for r in requests] == ["29.0"] * len(requests)
    assert all(r.headers.get("SEC") == QRADAR_TOKEN for r in requests)


def test_cli_refuses_to_start_without_a_known_version(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    fake = FakeQRadar(versions=_versions(("25.0", False, False), ("26.0", False, False)))
    route_discovery_to(monkeypatch, fake.transport())
    for name, value in base_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli.uvicorn, "run", _must_not_serve)

    assert cli.main(["--profile", "qradar-read"]) == 1
    assert "none of the known API versions" in capfd.readouterr().err


def _must_not_serve(*args: object, **kwargs: object) -> None:
    raise AssertionError("the server must not start")
