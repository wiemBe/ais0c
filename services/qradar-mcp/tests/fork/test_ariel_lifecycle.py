# SPDX-License-Identifier: Apache-2.0
"""Acceptance criterion 4: Ariel search lifecycle, paging and ownership."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client

from qradar_mcp.fork.ariel import SearchOwnership
from qradar_mcp.fork.settings import Settings

from .conftest import make_server
from .fake_qradar import ARIEL_ROWS, REFERENCE_MAP, FakeQRadar

QUERY = "SELECT sourceip, username, qid FROM events LAST 15 MINUTES LIMIT 10"
FOREIGN_SEARCH_ID = "6f1c2d3e-4b5a-4c6d-8e7f-90a1b2c3d4e5"


def _requests(fake: FakeQRadar, method: str, path_prefix: str) -> list[Any]:
    return [
        r for r in fake.api_requests() if r.method == method and r.url.path.startswith(path_prefix)
    ]


@pytest.mark.asyncio
async def test_create_status_paged_results_delete(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server("qradar-read", fake_qradar, settings)
    async with Client(server) as client:
        created = await client.call_tool("create_ariel_search", {"query_expression": QUERY})
        search_id = created.structured_content["search_id"]

        status = await client.call_tool(
            "get_ariel_search_status", {"search_id": search_id, "wait_seconds": 5}
        )
        assert status.structured_content["status"] == "COMPLETED"

        first_page = await client.call_tool(
            "get_ariel_search_results", {"search_id": search_id, "start": 0, "limit": 2}
        )
        second_page = await client.call_tool(
            "get_ariel_search_results", {"search_id": search_id, "start": 2, "limit": 2}
        )
        assert first_page.structured_content == {"events": ARIEL_ROWS[:2]}
        assert second_page.structured_content == {"events": ARIEL_ROWS[2:]}

        deleted = await client.call_tool("delete_ariel_search", {"search_id": search_id})
        assert deleted.structured_content["search_id"] == search_id

    [create] = _requests(fake_qradar, "POST", "/api/ariel/searches")
    assert create.url.params["query_expression"] == QUERY
    [status_request] = _requests(fake_qradar, "GET", f"/api/ariel/searches/{search_id}")[:1]
    assert status_request.headers["Prefer"] == "wait=5"
    results = [r for r in fake_qradar.api_requests() if r.url.path.endswith("/results")]
    assert [r.headers["Range"] for r in results] == ["items=0-1", "items=2-3"]
    assert len(_requests(fake_qradar, "DELETE", "/api/ariel/searches")) == 1


@pytest.mark.asyncio
async def test_results_are_always_requested_with_a_range(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server("qradar-read", fake_qradar, settings)
    async with Client(server) as client:
        created = await client.call_tool("create_ariel_search", {"query_expression": QUERY})
        search_id = created.structured_content["search_id"]
        await client.call_tool("get_ariel_search_results", {"search_id": search_id})
        await client.call_tool("get_ariel_search_results", {"search_id": search_id, "start": 5})

    results = [r for r in fake_qradar.api_requests() if r.url.path.endswith("/results")]
    assert [r.headers.get("Range") for r in results] == ["items=0-99", "items=5-104"]


@pytest.mark.asyncio
async def test_delete_refuses_a_search_this_server_did_not_create(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server("qradar-read", fake_qradar, settings)
    async with Client(server) as client:
        result = await client.call_tool(
            "delete_ariel_search", {"search_id": FOREIGN_SEARCH_ID}, raise_on_error=False
        )

    assert result.is_error
    assert "not created by this server" in result.content[0].text
    assert _requests(fake_qradar, "DELETE", "/api/") == []


@pytest.mark.asyncio
async def test_a_deleted_search_cannot_be_deleted_again(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server("qradar-read", fake_qradar, settings)
    async with Client(server) as client:
        created = await client.call_tool("create_ariel_search", {"query_expression": QUERY})
        search_id = created.structured_content["search_id"]
        await client.call_tool("delete_ariel_search", {"search_id": search_id})
        again = await client.call_tool(
            "delete_ariel_search", {"search_id": search_id}, raise_on_error=False
        )

    assert again.is_error
    assert len(_requests(fake_qradar, "DELETE", "/api/ariel/searches")) == 1


@pytest.mark.asyncio
async def test_servers_do_not_share_ownership(settings: Settings, fake_qradar: FakeQRadar) -> None:
    first = make_server("qradar-read", fake_qradar, settings)
    second = make_server("qradar-read", fake_qradar, settings)
    async with Client(first) as client:
        created = await client.call_tool("create_ariel_search", {"query_expression": QUERY})
    async with Client(second) as client:
        result = await client.call_tool(
            "delete_ariel_search",
            {"search_id": created.structured_content["search_id"]},
            raise_on_error=False,
        )
    assert result.is_error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("create_ariel_search", {"saved_search_id": 7}),
        ("create_ariel_search", {"query_expression": QUERY, "saved_search_id": 7}),
        ("create_ariel_search", {"query_expression": ""}),
        ("get_ariel_search_status", {"search_id": "../../siem/offenses"}),
        ("get_ariel_search_status", {"search_id": "abc/def"}),
        ("get_ariel_search_status", {"search_id": FOREIGN_SEARCH_ID, "wait_seconds": 300}),
        ("get_ariel_search_results", {"search_id": "..", "start": 0}),
        ("get_ariel_search_results", {"search_id": FOREIGN_SEARCH_ID, "limit": 10_001}),
        ("delete_ariel_search", {"search_id": "x?y=1"}),
        ("get_reference_map", {"name": "../../config/access/users"}),
        ("get_reference_map", {"name": ".."}),
        ("get_reference_table", {"name": f"{REFERENCE_MAP}#fragment"}),
    ],
)
async def test_invalid_arguments_are_rejected_before_any_request(
    tool: str, arguments: dict[str, Any], settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server("qradar-read", fake_qradar, settings)
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    assert "Invalid arguments" in result.content[0].text
    assert fake_qradar.api_requests() == []


def test_ownership_registry_is_bounded() -> None:
    ownership = SearchOwnership(capacity=2)
    for search_id in ("a", "b", "c"):
        ownership.remember(search_id)
    assert not ownership.owns("a")
    assert ownership.owns("b")
    assert ownership.owns("c")
    assert len(ownership) == 2
