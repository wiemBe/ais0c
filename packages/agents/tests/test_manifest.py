"""Acceptance criterion 2: manifests are checked against architecture §8.1 and the registry.

An unknown model alias, or a capability the alias's model lacks in the registry (§8.4), stops
the agent with a clear error.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from ais0c_agents import (
    AgentManifest,
    Budgets,
    ManifestError,
    ModelRegistryError,
    load_manifest,
    load_model_registry,
)
from ais0c_agents.manifest import parse_manifest
from ais0c_agents.registry import parse_model_registry

from .helpers import REPO_ROOT, TRIAGE_MANIFEST, registry, registry_data

# The fields of the example manifest in architecture §8.1, and `shared_rules`, which T-015 adds:
# the manifest names the shared rules version its prompt uses.
MANIFEST_FIELDS = {
    "id",
    "version",
    "role",
    "workflow_types",
    "model_alias",
    "required_model_capabilities",
    "input_schema",
    "output_schema",
    "toolset_profile",
    "max_steps",
    "budgets",
    "autonomy",
    "can_delegate",
    "prompt",
    "shared_rules",
    "eval_suites",
}


def manifest_data() -> dict[str, Any]:
    return yaml.safe_load(TRIAGE_MANIFEST.read_text(encoding="utf-8"))


def test_manifest_model_has_exactly_the_fields_of_8_1_and_shared_rules() -> None:
    assert set(AgentManifest.model_fields) == MANIFEST_FIELDS
    assert set(Budgets.model_fields) == {"tokens", "tool_calls", "wall_clock_seconds"}


def test_triage_manifest_loads() -> None:
    manifest = load_manifest(TRIAGE_MANIFEST, registry())

    assert manifest == AgentManifest(
        id="triage",
        version="1.2.0",
        role="QRadar offense ilk değerlendirmesi",
        workflow_types=frozenset({"case"}),
        model_alias="soc-fast",
        required_model_capabilities=frozenset({"tool_calling", "structured_output"}),
        input_schema="TriageTask",
        output_schema="TriageResult",
        toolset_profile="qradar-triage-read",
        max_steps=8,
        budgets=Budgets(tokens=150000, tool_calls=12, wall_clock_seconds=420),
        autonomy="L0",
        can_delegate=False,
        prompt="prompts/triage/v3.md",
        shared_rules="prompts/_shared/rules/v2.md",
        eval_suites=frozenset(
            {
                "triage-gold",
                "prompt-injection",
                "failure-recovery",
                "trust-layers",
                "adversarial-fn",
            }
        ),
    )


@pytest.mark.parametrize("field", sorted(MANIFEST_FIELDS))
def test_missing_field_is_rejected(field: str) -> None:
    data = manifest_data()
    del data[field]

    with pytest.raises(ManifestError, match=field):
        parse_manifest(data, registry())


def test_unknown_field_is_rejected() -> None:
    data = manifest_data() | {"tools": ["add_offense_note"]}

    with pytest.raises(ManifestError, match="tools"):
        parse_manifest(data, registry())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "Triage Agent"),
        ("version", "1.0"),
        ("role", ""),
        ("workflow_types", []),
        ("workflow_types", ["offense"]),
        ("required_model_capabilities", []),
        ("required_model_capabilities", ["Tool Calling"]),
        ("input_schema", "triage_task"),
        ("toolset_profile", "qradar triage"),
        ("max_steps", 0),
        ("budgets", {"tokens": 60000, "tool_calls": 0, "wall_clock_seconds": 180}),
        ("budgets", {"tokens": 60000, "tool_calls": 12}),
        ("budgets", {"tokens": 60000, "tool_calls": 12, "wall_clock_seconds": 180, "usd": 1}),
        ("autonomy", "L1"),
        ("autonomy", "L4"),
        ("can_delegate", "sometimes"),
        ("prompt", "prompts/triage/v1.txt"),
        ("prompt", "prompts/triage/../../config/agents/triage.yaml"),
        ("prompt", "/etc/passwd"),
        ("prompt", "prompts/_shared/rules.md"),
        ("prompt", "prompts/_shared/rules/v2.md"),
        ("shared_rules", "prompts/_shared/rules.md"),
        ("shared_rules", "prompts/_shared/rules/v0.md"),
        ("shared_rules", "prompts/_shared/rules/v2.txt"),
        ("shared_rules", "prompts/_shared/rules/../../triage/v2.md"),
        ("shared_rules", "prompts/triage/v2.md"),
        ("shared_rules", None),
        ("eval_suites", []),
    ],
)
def test_invalid_value_is_rejected(field: str, value: object) -> None:
    data = manifest_data() | {field: value}

    with pytest.raises(ManifestError, match=field):
        parse_manifest(data, registry())


def test_agent_without_tools_has_a_null_profile() -> None:
    data = manifest_data() | {"toolset_profile": None}

    assert parse_manifest(data, registry()).toolset_profile is None


def test_agent_without_tools_accepts_a_zero_tool_call_budget() -> None:
    data = manifest_data()
    data["toolset_profile"] = None
    data["budgets"]["tool_calls"] = 0

    manifest = parse_manifest(data, registry())

    assert manifest.toolset_profile is None
    assert manifest.budgets.tool_calls == 0


def test_agent_with_tools_rejects_a_zero_tool_call_budget() -> None:
    data = manifest_data()
    data["budgets"]["tool_calls"] = 0

    with pytest.raises(ManifestError, match=r"budgets\.tool_calls must be positive"):
        parse_manifest(data, registry())


@pytest.mark.parametrize("profile", [None, "qradar-triage-read"])
def test_negative_tool_call_budget_is_rejected(profile: str | None) -> None:
    data = manifest_data()
    data["toolset_profile"] = profile
    data["budgets"]["tool_calls"] = -1

    with pytest.raises(ManifestError, match="tool_calls"):
        parse_manifest(data, registry())


# --- model alias and capabilities ---------------------------------------------------------


@pytest.mark.parametrize("alias", ["soc-turbo", "gpt-fast", "SOC-FAST"])
def test_unknown_alias_stops_the_agent(alias: str) -> None:
    data = manifest_data() | {"model_alias": alias}

    with pytest.raises(ManifestError, match="model_alias"):
        parse_manifest(data, registry())


def test_alias_missing_from_the_registry_stops_the_agent() -> None:
    entries = {alias: entry for alias, entry in registry_data().items() if alias != "soc-fast"}

    with pytest.raises(ManifestError, match="'soc-fast' is not in the model registry"):
        parse_manifest(manifest_data(), parse_model_registry(entries))


def test_capability_the_model_lacks_stops_the_agent() -> None:
    data = manifest_data() | {"required_model_capabilities": ["tool_calling", "vision"]}

    with pytest.raises(ManifestError, match="lacks the required capabilities vision"):
        parse_manifest(data, registry())


def test_registry_entry_without_a_required_capability_stops_the_agent() -> None:
    entries = registry_data()
    entries["soc-fast"]["capabilities"] = ["tool_calling"]

    with pytest.raises(ManifestError, match="structured_output"):
        parse_manifest(manifest_data(), parse_model_registry(entries))


# --- files ----------------------------------------------------------------------------------


def test_duplicate_key_is_rejected(tmp_path: Path) -> None:
    text = TRIAGE_MANIFEST.read_text(encoding="utf-8") + "toolset_profile: qradar-hunt-read\n"
    path = tmp_path / "triage.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ManifestError, match="duplicate key 'toolset_profile'"):
        load_manifest(path, registry())


@pytest.mark.parametrize("text", ["id: [triage", "- just\n- a list\n", ""])
def test_unreadable_manifest_is_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "triage.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ManifestError, match=r"triage\.yaml"):
        load_manifest(path, registry())


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="cannot read"):
        load_manifest(tmp_path / "absent.yaml", registry())


# --- model registry -----------------------------------------------------------------------


def test_registry_in_the_8_4_format_loads(tmp_path: Path) -> None:
    path = tmp_path / "registry.dev.yaml"
    path.write_text(yaml.safe_dump(registry_data()), encoding="utf-8")

    entries = load_model_registry(path)

    assert set(entries) == {"soc-fast", "soc-reasoning", "soc-verifier", "soc-report"}
    assert entries["soc-fast"].capabilities == {"tool_calling", "structured_output"}
    assert entries["soc-fast"].model_settings() == {"parallel_tool_calls": False}


def test_registry_entry_can_leave_out_parallel_tool_calls_and_forced_tool_choice() -> None:
    # parallel_tool_calls null: the model rejects the parameter, so it is not sent (D-39).
    entry = registry_data()["soc-fast"] | {"parallel_tool_calls": None, "forced_tool_choice": False}

    entries = parse_model_registry(registry_data() | {"soc-fast": entry})

    assert entries["soc-fast"].model_settings() == {}
    assert entries["soc-fast"].forced_tool_choice is False
    assert entries["soc-reasoning"].forced_tool_choice is True


@pytest.mark.parametrize(
    "change",
    [
        {"soc-turbo": registry_data()["soc-fast"]},
        {"soc-fast": {"parallel_tool_calls": False, "context_window": 1}},
        {"soc-fast": registry_data()["soc-fast"] | {"context_window": 0}},
        {"soc-fast": registry_data()["soc-fast"] | {"capabilities": ["Tool Calling"]}},
    ],
)
def test_invalid_registry_is_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(ModelRegistryError):
        parse_model_registry(registry_data() | change)


def test_duplicate_alias_in_registry_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.dev.yaml"
    text = yaml.safe_dump(registry_data())
    path.write_text(text + text.split("soc-reasoning:", 1)[0], encoding="utf-8")

    with pytest.raises(ModelRegistryError, match="duplicate key"):
        load_model_registry(path)


# config/models/ arrives with T-003; once it is merged, the manifest is checked against it.
@pytest.mark.parametrize("name", ["registry.dev.yaml", "registry.prod.yaml"])
def test_triage_manifest_fits_the_repository_registry(name: str) -> None:
    path = REPO_ROOT / "config/models" / name
    if not path.exists():
        pytest.skip(f"config/models/{name} is not in this checkout yet (T-003)")

    assert load_manifest(TRIAGE_MANIFEST, load_model_registry(path)).model_alias == "soc-fast"
