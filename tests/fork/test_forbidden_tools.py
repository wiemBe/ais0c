# SPDX-License-Identifier: Apache-2.0
"""Acceptance criterion 3: write tools are not registered in the read and note profiles.

Each forbidden upstream tool is listed by name. The test also checks that the name
exists upstream, so a rename cannot make a check pass vacuously.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP

from qradar_mcp.fork.settings import Settings
from qradar_mcp.fork.tool_profiles import upstream_tool_catalog

from .conftest import make_server
from .fake_qradar import FakeQRadar

OFFENSE_CLOSE_AND_UPDATE = [
    "set_offense_status",
    "assign_offense",
    "set_offense_follow_up",
    "set_offense_protected",
]
NOTE_ADD = ["add_offense_note"]
REFERENCE_DATA_CHANGES = [
    "create_reference_set",
    "update_reference_set",
    "delete_reference_set",
    "add_to_reference_set",
    "remove_from_reference_set",
    "create_reference_map",
    "add_to_reference_map",
    "delete_reference_map",
    "remove_from_reference_map",
    "create_reference_table",
    "add_to_reference_table",
    "delete_reference_table",
    "remove_from_reference_table",
]
# Upstream has no tool that edits rules; these change configuration and data classification.
RULE_AND_CONFIGURATION_CHANGES = [
    "deploy_qradar_config",
    "add_staged_network",
    "update_staged_network",
    "delete_staged_network",
    "create_dsm_event_mapping",
    "update_dsm_event_mapping",
    "create_qid_record",
    "update_qid_record",
]
DELETES_OTHER_THAN_ARIEL_SEARCH = [
    "delete_saved_search",
    "delete_reference_set",
    "delete_reference_map",
    "delete_reference_table",
    "remove_from_reference_set",
    "remove_from_reference_map",
    "remove_from_reference_table",
    "delete_staged_network",
]

FORBIDDEN_IN_READ = sorted(
    {
        *OFFENSE_CLOSE_AND_UPDATE,
        *NOTE_ADD,
        *REFERENCE_DATA_CHANGES,
        *RULE_AND_CONFIGURATION_CHANGES,
        *DELETES_OTHER_THAN_ARIEL_SEARCH,
    }
)
FORBIDDEN_IN_NOTE = OFFENSE_CLOSE_AND_UPDATE

UPSTREAM = {tool.name: tool for tool in upstream_tool_catalog()}


async def _registered(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in await client.list_tools()}


@pytest_asyncio.fixture
async def read_tools(settings: Settings, fake_qradar: FakeQRadar) -> set[str]:
    return await _registered(make_server("qradar-read", fake_qradar, settings))


@pytest_asyncio.fixture
async def note_tools(settings: Settings, fake_qradar: FakeQRadar) -> set[str]:
    return await _registered(make_server("qradar-note", fake_qradar, settings))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", FORBIDDEN_IN_READ)
async def test_tool_is_not_registered_in_read_profile(tool: str, read_tools: set[str]) -> None:
    assert tool in UPSTREAM, f"{tool} no longer exists upstream; update this list"
    assert tool not in read_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", FORBIDDEN_IN_NOTE)
async def test_offense_close_is_not_registered_in_note_profile(
    tool: str, note_tools: set[str]
) -> None:
    assert tool in UPSTREAM, f"{tool} no longer exists upstream; update this list"
    assert tool not in note_tools


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool",
    sorted(
        n for n, t in UPSTREAM.items() if t.http_verb == "DELETE" and n != "delete_ariel_search"
    ),
)
async def test_no_upstream_delete_tool_except_ariel_search_is_in_read_profile(
    tool: str, read_tools: set[str]
) -> None:
    assert tool not in read_tools


@pytest.mark.asyncio
async def test_read_profile_writes_only_through_the_ariel_lifecycle(read_tools: set[str]) -> None:
    non_get = {name for name in read_tools if UPSTREAM[name].http_verb != "GET"}
    assert non_get == {"create_ariel_search", "delete_ariel_search"}
