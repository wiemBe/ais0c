"""The registry: config/connectors/qradar.yaml and config/policies/qradar.yaml (T-011 criteria 1
and 5, T-018 criterion 3).

The real files must load; a mistake in them must stop the gateway rather than weaken a check.
"""

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from gateway_support import load_config, registry_from, write_config

from ais0c_agents import ToolsetProfile
from ais0c_mcp_gateway.registry import Registry, RegistryError, SearchStep

AGENT_PROFILES = {
    "qradar-triage-read",
    "qradar-investigate-read",
    "qradar-verify-read",
    "qradar-hunt-read",
    "qradar-inventory-read",
    "qradar-tuning-read",
}
NOTE_PROFILE = "qradar-note-write"


def test_the_real_files_define_the_agent_profiles_and_the_note_profile(registry: Registry) -> None:
    assert set(registry.profiles) == AGENT_PROFILES | {NOTE_PROFILE}
    for profile in registry.profiles.values():
        for tool in profile.tools.values():
            assert tool.entry.description.strip()
    for name in AGENT_PROFILES:
        profile = registry.profiles[name]
        assert (profile.instance, profile.caller) == ("qradar-mcp-read", None)
        assert {tool.risk for tool in profile.tools.values()} == {"read"}
    note = registry.profiles[NOTE_PROFILE]
    assert (note.instance, note.caller) == ("qradar-mcp-note", "action-executor")


def test_tool_lists_are_what_the_agents_package_expects(registry: Registry) -> None:
    for name in AGENT_PROFILES:
        profile = registry.profiles[name]
        parsed = ToolsetProfile.model_validate(profile.tool_list())
        assert parsed.name == profile.name
        assert [tool.id for tool in parsed.tools] == list(profile.tools)


def test_an_agent_cannot_load_the_note_profile(registry: Registry) -> None:
    # The list says add_offense_note is a write tool, which an agent's toolset refuses.
    listed = registry.profiles[NOTE_PROFILE].tool_list()

    assert [(tool["id"], tool["risk"]) for tool in listed["tools"]] == [  # type: ignore[index]
        ("add_offense_note", "write"),
        ("get_offense_notes", "read"),
    ]
    with pytest.raises(ValueError, match="risk"):
        ToolsetProfile.model_validate(listed)


def test_ariel_tools_carry_their_controls(registry: Registry) -> None:
    for profile in registry.profiles.values():
        for tool in profile.tools.values():
            if tool.search is SearchStep.CREATE:
                assert tool.guard_aql
                assert profile.aql is not None
            elif tool.search is not None:
                assert tool.only_own_searches


def test_only_the_verify_profile_filters_free_text(registry: Registry) -> None:
    filtered = {name for name, p in registry.profiles.items() if p.output_filter is not None}
    assert filtered == {"qradar-verify-read"}


def test_the_closing_reasons_tool_is_in_no_agent_profile(registry: Registry) -> None:
    # Agents never close offenses (D-19); the tool would only help to.
    assert all("list_offense_closing_reasons" not in p.tools for p in registry.profiles.values())


def test_the_schema_version_follows_the_schema(registry: Registry, tmp_path: Path) -> None:
    manifest, policy = load_config()
    before = registry.profiles["qradar-triage-read"].tools["get_offense"].entry.schema_version
    properties = manifest["tools"]["get_offense"]["input_schema"]["properties"]
    properties["offense_id"]["maximum"] = 10**9

    changed = registry_from(write_config(tmp_path, manifest, policy))

    after = changed.profiles["qradar-triage-read"].tools["get_offense"].entry.schema_version
    assert after != before


def tool_entry(manifest: dict[str, Any], profile: str, tool_id: str) -> dict[str, Any]:
    return next(t for t in manifest["profiles"][profile]["tools"] if t["id"] == tool_id)


Change = Callable[[dict[str, Any], dict[str, Any]], None]


def _create_without_guard(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    tool_entry(manifest, "qradar-investigate-read", "create_ariel_search").pop("guard")


def _results_without_ownership(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    tool_entry(manifest, "qradar-investigate-read", "get_ariel_search_results").pop(
        "only_own_searches"
    )


def _guard_without_aql_rules(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    policy["profiles"]["qradar-investigate-read"].pop("aql")


def _write_tool_in_an_agent_profile(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["profiles"]["qradar-triage-read"]["tools"].append(
        {"id": "add_offense_note", "risk": "write"}
    )


def _note_tool_claimed_as_read(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["profiles"]["qradar-triage-read"]["tools"].append(
        {"id": "add_offense_note", "risk": "read"}
    )


def _tool_the_server_does_not_have(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["profiles"]["qradar-triage-read"]["tools"].append(
        {"id": "close_offense", "risk": "read"}
    )


def _tool_without_registry_entry(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["profiles"]["qradar-triage-read"]["tools"].append(
        {"id": "list_offense_closing_reasons", "risk": "read"}
    )


def _profile_missing_from_policy(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    policy["profiles"].pop("qradar-verify-read")


def _unknown_output_filter(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    policy["profiles"]["qradar-verify-read"]["output_filter"] = "no-such-filter"


def _schema_open_to_extra_arguments(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["tools"]["get_offense"]["input_schema"]["additionalProperties"] = True


def _invalid_json_schema(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["tools"]["get_offense"]["input_schema"]["properties"]["offense_id"]["type"] = "int"


def _missing_hunt_pool(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["quota_pools"].pop("hunt")


def _hours_without_time_zone(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["quota_pools"]["hunt"].pop("time_zone")


def _tool_listed_twice(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    tools = manifest["profiles"]["qradar-triage-read"]["tools"]
    tools.append(copy.deepcopy(tools[0]))


def _unknown_field(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["profiles"]["qradar-triage-read"]["allow_everything"] = True


# --- the note profile (T-018) --------------------------------------------------------------


def _executor_tool_in_an_agent_profile(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["profiles"]["qradar-triage-read"]["tools"].append(
        {"id": "add_offense_note", "risk": "write", "caller": "action-executor"}
    )


def _note_profile_tool_without_its_caller(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    tool_entry(manifest, NOTE_PROFILE, "get_offense_notes").pop("caller")


def _agent_profile_on_the_note_server(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    # Only a read tool, but the note instance's QRadar token can write.
    manifest["profiles"]["qradar-inventory-read"] = {
        "server_profile": "qradar-note",
        "tools": [{"id": "get_offense_notes", "risk": "read"}],
    }


def _unknown_caller(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    for tool in manifest["profiles"][NOTE_PROFILE]["tools"]:
        tool["caller"] = "triage"


def _note_profile_on_the_read_server(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["profiles"][NOTE_PROFILE]["server_profile"] = "qradar-read"


def _note_text_without_a_text_rule(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    policy["profiles"][NOTE_PROFILE].pop("text_arguments")


def _note_tool_with_more_free_text(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    properties = manifest["tools"]["add_offense_note"]["input_schema"]["properties"]
    properties["fields"] = {"type": "string", "maxLength": 1000}


def _text_rule_no_tool_applies_to(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    policy["profiles"][NOTE_PROFILE]["text_arguments"]["comment"] = {"max_length": 100}


def _text_rule_above_the_schema(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    policy["profiles"][NOTE_PROFILE]["text_arguments"]["note_text"]["max_length"] = 20000


def _text_rule_of_no_length(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    policy["profiles"][NOTE_PROFILE]["text_arguments"]["note_text"]["max_length"] = 0


def _two_server_profiles_on_one_instance(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["server_profiles"]["qradar-note"]["instance"] = "qradar-mcp-read"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_create_without_guard, "needs guard: aql"),
        (_results_without_ownership, "needs only_own_searches"),
        (_guard_without_aql_rules, "needs guard: aql and aql rules"),
        (_write_tool_in_an_agent_profile, "a risk: write tool needs a caller"),
        (_executor_tool_in_an_agent_profile, "every tool of a profile names the same caller"),
        (_note_profile_tool_without_its_caller, "every tool of a profile names the same caller"),
        (_agent_profile_on_the_note_server, "agent profile: it may not use qradar-note"),
        (_unknown_caller, "Input should be 'action-executor'"),
        (_note_profile_on_the_read_server, "add_offense_note is not a write tool of qradar-read"),
        (_note_text_without_a_text_rule, "writes the free text note_text: needs a text rule"),
        (_note_tool_with_more_free_text, "writes the free text fields: needs a text rule"),
        (_text_rule_no_tool_applies_to, "text rule for comment, which no tool"),
        (_text_rule_above_the_schema, r"allows more than its schema's maxLength \(10000\)"),
        (_text_rule_of_no_length, "greater than or equal to 1"),
        (_two_server_profiles_on_one_instance, "every server profile runs on its own instance"),
        (_note_tool_claimed_as_read, "is not a read tool of qradar-read"),
        (_tool_the_server_does_not_have, "is not a read tool of qradar-read"),
        (_tool_without_registry_entry, "has no registry entry"),
        (_profile_missing_from_policy, "profiles must be in both files"),
        (_unknown_output_filter, "unknown output filter"),
        (_schema_open_to_extra_arguments, "additionalProperties: false"),
        (_invalid_json_schema, "invalid JSON Schema"),
        (_missing_hunt_pool, "missing quota pools: hunt"),
        (_hours_without_time_zone, "go together"),
        (_tool_listed_twice, "listed twice"),
        (_unknown_field, "Extra inputs are not permitted"),
    ],
)
def test_an_inconsistent_configuration_stops_the_gateway(
    tmp_path: Path, change: Change, message: str
) -> None:
    manifest, policy = load_config()
    change(manifest, policy)

    with pytest.raises(RegistryError, match=message):
        registry_from(write_config(tmp_path, manifest, policy))
