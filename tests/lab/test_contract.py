# SPDX-License-Identifier: Apache-2.0
"""Every tool of every profile, called on the lab QRadar, matches its snapshot schema."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from jsonschema import Draft202012Validator

from qradar_mcp.fork.tool_profiles import NOTE_PROFILE, READ_PROFILE

from .conftest import Lab

SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "snapshots"
LAB_QUERY = (
    # AQL wants LIMIT before the time clause; "... LAST 15 MINUTES LIMIT 5" is a syntax error.
    "SELECT QIDNAME(qid) AS event_name, sourceip, starttime FROM events LIMIT 5 LAST 15 MINUTES"
)
# Turkish letters and characters a form body must escape: add_offense_note sends the note in a
# form body (T-040), and QRadar must store it exactly as it was sent.
NOTE_TEXT = (
    "ais0c contract test: this note was written by the lab test suite and can be ignored.\n"
    "Türkçe harfler: çğıöşüÇĞİÖŞÜ; form characters: a&b=c+d 100% ?#"
)
STATUS_POLLS = 6

Arguments = Callable[[Lab], dict[str, Any]]

# In run order: list tools first, so the get tools can use an ID they found.
READ_CALLS: dict[str, Arguments] = {
    "list_offenses": lambda lab: {"limit": 5},
    "get_offense": lambda lab: {"offense_id": lab.need("offense_id")},
    "list_offense_types": lambda lab: {"limit": 5},
    "list_offense_closing_reasons": lambda lab: {"limit": 5},
    "list_source_addresses": lambda lab: {"limit": 5},
    "list_local_destination_addresses": lambda lab: {"limit": 5},
    "list_rules": lambda lab: {"limit": 5},
    "get_rule": lambda lab: {"rule_id": lab.need("rule_id")},
    "list_assets": lambda lab: {"limit": 5},
    "list_log_sources": lambda lab: {"limit": 5},
    "get_log_source": lambda lab: {"log_source_id": lab.need("log_source_id")},
    "list_log_source_types": lambda lab: {"limit": 5},
    "list_reference_sets": lambda lab: {"limit": 5},
    "get_reference_set": lambda lab: {"set_id": lab.need("reference_set_id")},
    "list_reference_maps": lambda lab: {"limit": 5},
    "get_reference_map": lambda lab: {"name": lab.need("reference_map"), "limit": 5},
    "list_reference_tables": lambda lab: {"limit": 5},
    "get_reference_table": lambda lab: {"name": lab.need("reference_table"), "limit": 5},
    "create_ariel_search": lambda lab: {"query_expression": LAB_QUERY},
    "get_ariel_search_status": lambda lab: {"search_id": lab.need("search_id"), "wait_seconds": 20},
    "get_ariel_search_results": lambda lab: {"search_id": lab.need("search_id"), "limit": 5},
    "delete_ariel_search": lambda lab: {"search_id": lab.need("search_id")},
}
NOTE_CALLS: dict[str, Arguments] = {
    "get_offense_notes": lambda lab: {"offense_id": _note_offense(lab), "limit": 5},
    "add_offense_note": lambda lab: {"offense_id": _note_target(lab), "note_text": NOTE_TEXT},
}


def _note_offense(lab: Lab) -> int:
    return lab.found.get("note_offense_id") or lab.need("offense_id")


def _note_target(lab: Lab) -> int:
    """QRadar has no way to delete a note, so one is written only where the run says."""
    if "note_offense_id" not in lab.found:
        pytest.skip("set QRADAR_LAB_OFFENSE_ID to the lab offense the test note may be written to")
    return lab.found["note_offense_id"]


# tool -> (key in Lab.found, list in the output, field of the first item)
FIRST_ITEM_IDS: dict[str, tuple[str, str, str]] = {
    "list_offenses": ("offense_id", "offenses", "id"),
    "list_rules": ("rule_id", "items", "id"),
    "list_log_sources": ("log_source_id", "items", "id"),
    "list_reference_sets": ("reference_set_id", "items", "id"),
    "list_reference_maps": ("reference_map", "items", "name"),
    "list_reference_tables": ("reference_table", "items", "name"),
}


def _record(lab: Lab, tool: str, output: dict[str, Any]) -> None:
    """Remember IDs that later calls need."""
    if tool in FIRST_ITEM_IDS:
        key, collection, field = FIRST_ITEM_IDS[tool]
        items = output.get(collection) or []
        if items and field in items[0]:
            lab.found[key] = items[0][field]
    if tool == "create_ariel_search":
        lab.found["search_id"] = output["search_id"]


def _validate(tool: str, output: dict[str, Any]) -> None:
    snapshot = json.loads((SNAPSHOT_DIR / "tools" / f"{tool}.json").read_text(encoding="utf-8"))
    Draft202012Validator(snapshot["outputSchema"]).validate(output)


async def _call(lab: Lab, profile: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with Client(lab.servers[profile]) as client:
        result = await client.call_tool(tool, arguments)
    return result.structured_content


def _profile_tools(profile: str) -> list[str]:
    snapshot = json.loads(
        (SNAPSHOT_DIR / "profiles" / f"{profile}.json").read_text(encoding="utf-8")
    )
    return snapshot["tools"]


@pytest.mark.lab
@pytest.mark.parametrize("tool", list(READ_CALLS))
def test_read_profile_tool_matches_its_snapshot(lab: Lab, tool: str) -> None:
    arguments = READ_CALLS[tool](lab)
    output = lab.run(_call(lab, READ_PROFILE, tool, arguments))
    if tool == "get_ariel_search_status":
        for _ in range(STATUS_POLLS):
            if output["status"] not in ("WAIT", "EXECUTE", "SORTING"):
                break
            _validate(tool, output)
            output = lab.run(_call(lab, READ_PROFILE, tool, arguments))
        assert output["status"] == "COMPLETED", f"lab search ended as {output['status']}"
    _validate(tool, output)
    _record(lab, tool, output)


@pytest.mark.lab
@pytest.mark.parametrize("tool", list(NOTE_CALLS))
def test_note_profile_tool_matches_its_snapshot(lab: Lab, tool: str) -> None:
    output = lab.run(_call(lab, NOTE_PROFILE, tool, NOTE_CALLS[tool](lab)))
    _validate(tool, output)
    if tool == "add_offense_note":
        assert output["note_text"] == NOTE_TEXT


def test_lab_calls_cover_every_profile_tool() -> None:
    """Runs without a lab: a tool added to a profile needs a lab call here."""
    assert sorted(READ_CALLS) == _profile_tools(READ_PROFILE)
    assert sorted(NOTE_CALLS) == _profile_tools(NOTE_PROFILE)
