# SPDX-License-Identifier: Apache-2.0
"""Acceptance criterion 2: profile based tool registration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP

from qradar_mcp.fork import cli
from qradar_mcp.fork.settings import Settings
from qradar_mcp.fork.tool_profiles import (
    PROFILE_NAMES,
    ProfileError,
    load_profile_toggles,
    select_profile_tools,
    upstream_tool_catalog,
)
from qradar_mcp.fork.tool_specs import TOOL_SPECS
from qradar_mcp.tools.base import MCPTool
from qradar_mcp.tools.fastmcp_adapter import register_all_tools
from qradar_mcp.utils.feature_toggle_manager import FeatureToggleManager

from .conftest import SNAPSHOT_DIR, make_server
from .fake_qradar import FakeQRadar


async def _registered(server: FastMCP) -> list[str]:
    async with Client(server) as client:
        return sorted(tool.name for tool in await client.list_tools())


def _snapshot_tools(profile: str) -> list[str]:
    snapshot = json.loads((SNAPSHOT_DIR / "profiles" / f"{profile}.json").read_text())
    return snapshot["tools"]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILE_NAMES)
async def test_registered_tools_match_the_profile_snapshot(
    profile: str, settings: Settings, fake_qradar: FakeQRadar
) -> None:
    registered = await _registered(make_server(profile, fake_qradar, settings))
    assert registered == _snapshot_tools(profile), (
        "The tool list changed. If intended, run `python -m qradar_mcp.fork.schema_export` "
        "and review snapshots/profiles/ in the PR."
    )


def test_note_profile_holds_only_note_tools() -> None:
    assert _snapshot_tools("qradar-note") == ["add_offense_note", "get_offense_notes"]


@pytest.mark.parametrize(
    "profile", ["qradar-admin", "", "QRADAR-READ", "../feature_toggles", "qradar-read.json"]
)
def test_unknown_profile_is_refused(profile: str) -> None:
    with pytest.raises(ProfileError, match="unknown profile"):
        load_profile_toggles(profile)


@pytest.mark.parametrize(
    "argv", [["--profile", "qradar-admin"], ["--profile", "../feature_toggles"], []]
)
def test_cli_does_not_start_without_a_known_profile(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_settings", Mock(side_effect=AssertionError("must not get here")))
    with pytest.raises(SystemExit) as exited:
        cli.main(argv)
    assert exited.value.code == 2
    assert "--profile" in capsys.readouterr().err


def _toggles(tmp_path: Path, **config: object) -> FeatureToggleManager:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"verb_toggles": {}, "group_toggles": {}, **config}))
    return FeatureToggleManager(str(path))


@pytest.mark.parametrize(
    ("profile", "per_tool", "reason"),
    [
        ("qradar-read", {"GetOffenseTool": True, "SetOffenseStatusTool": True}, "offense-changing"),
        (
            "qradar-note",
            {"AddOffenseNoteTool": True, "SetOffenseStatusTool": True},
            "offense-changing",
        ),
        ("qradar-read", {"GetOffenseTool": True, "AddOffenseNoteTool": True}, "non-read"),
        ("qradar-read", {"GetOffenseTool": True, "AddToReferenceSetTool": True}, "non-read"),
        ("qradar-read", {"GetOffenseTool": True, "DeleteSavedSearchTool": True}, "non-read"),
        ("qradar-note", {"AddOffenseNoteTool": True, "GetOffenseTool": True}, "besides notes"),
    ],
)
def test_startup_checks_refuse_forbidden_tools_even_if_a_profile_file_enables_them(
    tmp_path: Path, profile: str, per_tool: dict[str, bool], reason: str
) -> None:
    toggles = _toggles(tmp_path, per_tool_toggles=per_tool)
    with pytest.raises(ProfileError, match=reason):
        select_profile_tools(profile, toggles, upstream_tool_catalog())


def test_verb_and_group_toggles_cannot_widen_the_read_profile(tmp_path: Path) -> None:
    toggles = _toggles(
        tmp_path,
        verb_toggles={"GET": True, "POST": True},
        group_toggles={"offense": True},
    )
    with pytest.raises(ProfileError):
        select_profile_tools("qradar-read", toggles, upstream_tool_catalog())


def test_profile_naming_a_missing_tool_class_is_refused(tmp_path: Path) -> None:
    toggles = _toggles(tmp_path, per_tool_toggles={"GetOffenseTool": True, "NoSuchTool": True})
    with pytest.raises(ProfileError, match="NoSuchTool"):
        select_profile_tools("qradar-read", toggles, upstream_tool_catalog())


def test_profile_enabling_nothing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="enables no tools"):
        select_profile_tools("qradar-read", _toggles(tmp_path), upstream_tool_catalog())


def test_every_profile_tool_has_a_spec_and_every_spec_is_used() -> None:
    profile_tools = {name for profile in PROFILE_NAMES for name in _snapshot_tools(profile)}
    assert profile_tools == set(TOOL_SPECS)


def test_catalog_matches_upstream_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fork enumerates tools through tools.__all__; upstream registers its own list.

    If an upstream sync adds a tool to one list only, this test fails.
    """
    monkeypatch.setattr(MCPTool, "_shared_qradar_client", MCPTool._shared_qradar_client)
    allow_all = Mock(spec=FeatureToggleManager)
    allow_all.is_tool_enabled.return_value = True
    registered, skipped = register_all_tools(FastMCP("probe"), allow_all, Mock())
    assert skipped == []
    assert sorted(t.name for t in registered) == sorted(t.name for t in upstream_tool_catalog())
