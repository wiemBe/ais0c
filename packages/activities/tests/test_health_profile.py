"""The inventory profile reads offenses for the health check, and no other profile changed
(T-032 criterion 10)."""

from catalog_gateway import CONFIG_DIR

from ais0c_activities.health import HEALTH_TOOLS
from ais0c_mcp_gateway.registry import load_registry

# How many tools each profile of config/connectors/qradar.yaml has: only the inventory profile
# grew, by `list_offenses`.
TOOL_COUNTS = {
    "qradar-triage-read": 11,
    "qradar-investigate-read": 11,
    "qradar-verify-read": 4,
    "qradar-hunt-read": 10,
    "qradar-inventory-read": 6,
    "qradar-tuning-read": 6,
    "qradar-note-write": 2,
}


def test_the_inventory_profile_lists_offenses_and_only_reads() -> None:
    profiles = load_registry(CONFIG_DIR, ["qradar"]).profiles

    inventory = profiles["qradar-inventory-read"]
    assert set(inventory.tools) == {
        "list_offenses",
        "list_rules",
        "get_rule",
        "list_log_sources",
        "get_log_source",
        "list_log_source_types",
    }
    assert HEALTH_TOOLS <= set(inventory.tools)
    assert {tool.risk for tool in inventory.tools.values()} == {"read"}
    assert inventory.aql is None


def test_no_other_profile_changed() -> None:
    profiles = load_registry(CONFIG_DIR, ["qradar"]).profiles

    assert {name: len(profile.tools) for name, profile in profiles.items()} == TOOL_COUNTS
    # The profiles that did not have `list_offenses` still do not.
    without = {name for name, profile in profiles.items() if "list_offenses" not in profile.tools}
    assert without == {
        "qradar-verify-read",
        "qradar-hunt-read",
        "qradar-tuning-read",
        "qradar-note-write",
    }
