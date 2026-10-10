# SPDX-License-Identifier: Apache-2.0
"""Profile based tool registration (architecture §11.1, §11.2).

A profile is a feature toggle file in upstream's ``feature_toggles.json`` format, kept in
``fork/profiles/<profile>.json``. Its verb and group toggles are empty, so upstream's
``FeatureToggleManager`` enables nothing except the tool classes listed in
``per_tool_toggles``: an allowlist.

The files are not the only guard. Before any tool is registered, the selection is checked
against rules written in code, and the server refuses to start if a rule is broken:

- Tools that change an offense are never registered, in any profile (D-19).
- ``qradar-read`` holds only HTTP GET tools plus Ariel search create and delete.
- ``qradar-note`` holds only adding and reading offense notes.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import qradar_mcp.tools as upstream_tools
from qradar_mcp.tools.base import MCPTool
from qradar_mcp.utils.feature_toggle_manager import FeatureToggleConfigError, FeatureToggleManager

READ_PROFILE = "qradar-read"
NOTE_PROFILE = "qradar-note"
PROFILE_NAMES: tuple[str, ...] = (READ_PROFILE, NOTE_PROFILE)
PROFILE_DIR = Path(__file__).with_name("profiles")

NEVER_REGISTERED = frozenset(
    {"set_offense_status", "assign_offense", "set_offense_follow_up", "set_offense_protected"}
)
READ_PROFILE_NON_GET_TOOLS = frozenset({"create_ariel_search", "delete_ariel_search"})
NOTE_PROFILE_TOOLS = frozenset({"add_offense_note", "get_offense_notes"})


class ProfileError(RuntimeError):
    """The requested profile does not exist or would register a forbidden tool."""


def load_profile_toggles(profile: str) -> FeatureToggleManager:
    """Load ``fork/profiles/<profile>.json``; unknown profile names are refused."""
    if profile not in PROFILE_NAMES:
        raise ProfileError(
            f"unknown profile {profile!r}; expected one of {', '.join(PROFILE_NAMES)}"
        )
    try:
        return FeatureToggleManager(str(PROFILE_DIR / f"{profile}.json"))
    except FeatureToggleConfigError as exc:
        raise ProfileError(f"profile {profile!r} cannot be loaded: {exc}") from None


def upstream_tool_catalog() -> list[MCPTool]:
    """One instance of every tool class upstream exports."""
    catalog: list[MCPTool] = []
    for name in upstream_tools.__all__:
        candidate = getattr(upstream_tools, name)
        if (
            isinstance(candidate, type)
            and issubclass(candidate, MCPTool)
            and candidate is not MCPTool
        ):
            catalog.append(candidate())
    return catalog


def select_profile_tools(
    profile: str, toggles: FeatureToggleManager, catalog: Iterable[MCPTool]
) -> list[MCPTool]:
    """Tools the profile enables, after the safety rules are checked."""
    tools = list(catalog)
    known_classes = {type(tool).__name__ for tool in tools}
    unknown = sorted(set(toggles.per_tool_toggles) - known_classes)
    if unknown:
        # An upstream rename would otherwise drop a tool silently.
        raise ProfileError(
            f"profile {profile!r} names tool classes upstream does not have: {unknown}"
        )
    selected = [tool for tool in tools if toggles.is_tool_enabled(tool)]
    if not selected:
        raise ProfileError(f"profile {profile!r} enables no tools")
    check_profile_invariants(profile, selected)
    return selected


def check_profile_invariants(profile: str, tools: Iterable[MCPTool]) -> None:
    """Raise ``ProfileError`` if the tools break a rule of the profile."""
    tools = list(tools)
    names = {tool.name for tool in tools}
    forbidden = sorted(names & NEVER_REGISTERED)
    if forbidden:
        raise ProfileError(
            f"profile {profile!r} would register offense-changing tools: {forbidden}"
        )
    if profile == READ_PROFILE:
        writes = sorted(
            tool.name
            for tool in tools
            if tool.http_verb != "GET" and tool.name not in READ_PROFILE_NON_GET_TOOLS
        )
        if writes:
            raise ProfileError(f"profile {profile!r} would register non-read tools: {writes}")
    elif profile == NOTE_PROFILE:
        extra = sorted(names - NOTE_PROFILE_TOOLS)
        if extra:
            raise ProfileError(f"profile {profile!r} would register tools besides notes: {extra}")
    else:
        raise ProfileError(f"unknown profile {profile!r}")
