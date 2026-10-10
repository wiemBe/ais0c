# SPDX-License-Identifier: Apache-2.0
"""Every exposed tool returns structured output that matches its snapshot schema.

These run against the in-process fake QRadar; tests/lab runs the same check against a
real one.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client
from jsonschema import Draft202012Validator

from qradar_mcp.fork.settings import Settings
from qradar_mcp.fork.tool_profiles import NOTE_PROFILE, READ_PROFILE

from .conftest import SNAPSHOT_DIR, make_server
from .fake_qradar import (
    LOG_SOURCE_ID,
    OFFENSE_ID,
    REFERENCE_MAP,
    REFERENCE_SET_ID,
    REFERENCE_TABLE,
    RULE_ID,
    FakeQRadar,
)

SAMPLES: dict[str, dict[str, dict[str, Any]]] = {
    READ_PROFILE: {
        "get_offense": {"offense_id": OFFENSE_ID},
        "list_offenses": {"limit": 5, "filter": "status = 'OPEN'"},
        "list_offense_types": {"limit": 5},
        "list_offense_closing_reasons": {"limit": 5},
        "list_source_addresses": {"limit": 5},
        "list_local_destination_addresses": {"limit": 5},
        "list_rules": {"limit": 5},
        "get_rule": {"rule_id": RULE_ID},
        "list_assets": {"limit": 5},
        "list_log_sources": {"limit": 5},
        "get_log_source": {"log_source_id": LOG_SOURCE_ID},
        "list_log_source_types": {"limit": 5},
        "list_reference_sets": {"limit": 5},
        "get_reference_set": {"set_id": REFERENCE_SET_ID},
        "list_reference_maps": {"limit": 5},
        "get_reference_map": {"name": REFERENCE_MAP},
        "list_reference_tables": {"limit": 5},
        "get_reference_table": {"name": REFERENCE_TABLE},
        "create_ariel_search": {"query_expression": "SELECT * FROM events LAST 5 MINUTES"},
        "get_ariel_search_status": {"search_id": "<created>"},
        "get_ariel_search_results": {"search_id": "<created>"},
        "delete_ariel_search": {"search_id": "<created>"},
    },
    NOTE_PROFILE: {
        "get_offense_notes": {"offense_id": OFFENSE_ID},
        "add_offense_note": {"offense_id": OFFENSE_ID, "note_text": "AI triage: synthetic note"},
    },
}


def _snapshot(tool: str) -> dict[str, Any]:
    return json.loads((SNAPSHOT_DIR / "tools" / f"{tool}.json").read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [READ_PROFILE, NOTE_PROFILE])
async def test_every_tool_output_matches_its_snapshot_schema(
    profile: str, settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server(profile, fake_qradar, settings)
    search_id = ""
    async with Client(server) as client:
        listed = sorted(tool.name for tool in await client.list_tools())
        assert listed == sorted(SAMPLES[profile]), "every registered tool needs a sample input"
        for tool, sample in SAMPLES[profile].items():
            arguments = {k: (search_id if v == "<created>" else v) for k, v in sample.items()}
            # The MCP client itself validates structured content against outputSchema.
            result = await client.call_tool(tool, arguments)
            Draft202012Validator(_snapshot(tool)["outputSchema"]).validate(
                result.structured_content
            )
            if tool == "create_ariel_search":
                search_id = result.structured_content["search_id"]


@pytest.mark.asyncio
async def test_list_tools_return_items_not_text_reports(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server(READ_PROFILE, fake_qradar, settings)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        rules = await client.call_tool("list_rules", {"limit": 5})
        rule = await client.call_tool("get_rule", {"rule_id": RULE_ID})

    assert "format_output" not in tools["list_rules"].inputSchema["properties"]
    assert rules.structured_content["items"][0]["id"] == RULE_ID
    assert rule.structured_content["id"] == RULE_ID
    assert "format_output" not in str(fake_qradar.api_requests()[0].url)


@pytest.mark.asyncio
async def test_upstream_error_becomes_an_mcp_error(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server(READ_PROFILE, fake_qradar, settings)
    async with Client(server) as client:
        result = await client.call_tool("get_offense", {"offense_id": 999}, raise_on_error=False)
    assert result.is_error
    assert result.content[0].text.startswith("Error executing get_offense")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_offenses", {"bogus": 1}),
        ("list_offenses", {"format_output": True}),
        ("get_offense", {}),
        ("get_offense", {"offense_id": "42; DROP"}),
        ("list_rules", {"limit": 0}),
        # T-040: no filter argument on the two reference data reads.
        ("get_reference_table", {"name": REFERENCE_TABLE, "filter": "value = 'x'"}),
        ("get_reference_map", {"name": REFERENCE_MAP, "filter": "value = 'x'"}),
    ],
)
async def test_arguments_outside_the_schema_are_rejected(
    tool: str, arguments: dict[str, Any], settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server(READ_PROFILE, fake_qradar, settings)
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    assert fake_qradar.api_requests() == []


@pytest.mark.asyncio
async def test_input_schemas_forbid_unknown_properties(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    server = make_server(READ_PROFILE, fake_qradar, settings)
    async with Client(server) as client:
        tools = await client.list_tools()
    assert all(tool.inputSchema.get("additionalProperties") is False for tool in tools)


@pytest.mark.asyncio
async def test_reference_table_and_map_reads_take_no_filter(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    # QRadar 29.0 answers a filter on GET /reference_data/tables/{name} with 422 ("The Parameter
    # 'filter' is not supported by this endpoint"); the map read lost its filter with it (T-040).
    server = make_server(READ_PROFILE, fake_qradar, settings)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        await client.call_tool("get_reference_table", {"name": REFERENCE_TABLE})
        await client.call_tool("get_reference_map", {"name": REFERENCE_MAP})

    for name in ("get_reference_table", "get_reference_map"):
        assert "filter" not in tools[name].inputSchema["properties"]
        assert "filter" not in _snapshot(name)["inputSchema"]["properties"]
    assert all("filter" not in request.url.params for request in fake_qradar.api_requests())
