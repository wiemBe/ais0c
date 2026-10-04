"""Acceptance criterion 1: the manifest has the fields of architecture §7, unknown fields are
refused, and fields that read like a grant of tools or permissions get their own error."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from ais0c_knowledge.skills import (
    AGENT_RESULT_SCHEMAS,
    SkillError,
    SkillManifest,
    SkillPermissionError,
    parse_manifest,
)

from .skill_helpers import manifest_data

# architecture §7, "Manifest alanları".
ARCHITECTURE_FIELDS = {
    "id",
    "version",
    "status",
    "owner",
    "allowed_agent_roles",
    "triggers",
    "required_telemetry",
    "required_evidence",
    "budgets",
    "output_schema",
    "eval_suites",
    "expires_at",
    "content_hash",
    "approved_by",
}
SHA = "sha256:" + "ab" * 32


def refused(data: dict[str, Any]) -> SkillError:
    with pytest.raises(SkillError) as info:
        parse_manifest(data)
    return info.value


def test_a_valid_manifest_parses() -> None:
    manifest = parse_manifest(manifest_data())
    assert manifest.id == "test-skill"
    assert manifest.version == "1.0.0"
    assert manifest.triggers.rule_ids == {100001}
    assert manifest.allowed_agent_roles == {"investigation"}
    assert manifest.expires_at == date(2027, 10, 1)


def test_the_manifest_has_exactly_the_architecture_fields() -> None:
    assert set(SkillManifest.model_fields) == ARCHITECTURE_FIELDS


@pytest.mark.parametrize("field", sorted(ARCHITECTURE_FIELDS))
def test_every_field_is_required(field: str) -> None:
    data = manifest_data()
    del data[field]
    error = refused(data)
    assert field in str(error)


@pytest.mark.parametrize("field", ["description", "title", "notes", "model_alias", "Id"])
def test_an_unknown_field_is_refused(field: str) -> None:
    error = refused(manifest_data(**{field: "x"}))
    assert not isinstance(error, SkillPermissionError)
    assert field in str(error)


@pytest.mark.parametrize(
    "field",
    [
        "tools",
        "allowed_tools",
        "allowedTools",
        "Tools",
        "tool_allowlist",
        "toolset",
        "toolset_profile",
        "toolsets",
        "permissions",
        "capabilities",
        "scopes",
        "grants",
        "privileges",
        "access",
        "actions",
        "allowed_actions",
        "profile",
        "connectors",
        "mcp_servers",
        "policy",
        "autonomy",
        "roles",
        "write",
        "write_access",
        "exec",
        "script",
        "scripts",
        "commands",
        "sudo",
        "admin",
    ],
)
def test_a_grant_field_is_refused_with_its_own_error(field: str) -> None:
    with pytest.raises(SkillPermissionError, match="grants nothing"):
        parse_manifest(manifest_data(**{field: ["add_offense_note"]}))


@pytest.mark.parametrize(
    ("section", "value"),
    [
        ("triggers", {"tools": ["create_ariel_search"]}),
        ("budgets", {"allowed_tools": ["add_offense_note"]}),
        ("required_telemetry", [{"permissions": ["write"]}]),
        ("required_evidence", [{"actions": ["close_offense"]}]),
    ],
)
def test_a_grant_field_inside_a_section_is_refused_too(section: str, value: object) -> None:
    data = manifest_data()
    if isinstance(value, dict):
        data[section] = {**data[section], **value}
    else:
        assert isinstance(value, list)
        data[section] = [{**data[section][0], **value[0]}]
    with pytest.raises(SkillPermissionError, match=section):
        parse_manifest(data)


def test_the_budgets_tool_calls_is_not_a_grant() -> None:
    manifest = parse_manifest(manifest_data())
    assert manifest.budgets.tool_calls == 12


@pytest.mark.parametrize("data", [None, [], "id: x", 42])
def test_a_manifest_must_be_a_mapping(data: object) -> None:
    with pytest.raises(SkillError, match="mapping"):
        parse_manifest(data)


# --- field values --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "Windows-DCSync"),
        ("id", "windows_dcsync"),
        ("id", "-dcsync"),
        ("id", "dcsync-"),
        ("id", ""),
        ("version", "1.0"),
        ("version", "01.0.0"),
        ("version", "1.0.0-rc1"),
        pytest.param("version", 1.0, id="version-float"),
        ("status", "approved-pending"),
        ("status", "deprecated"),
        ("owner", ""),
        ("owner", " soc"),
        ("owner", "soc\nengineering"),
        ("allowed_agent_roles", []),
        ("allowed_agent_roles", ["investigator"]),
        ("allowed_agent_roles", ["executor"]),
        ("output_schema", "NoSuchResult"),
        ("output_schema", "OffenseSnapshot"),
        ("output_schema", "AgentResult"),
        ("eval_suites", []),
        ("eval_suites", ["Skill Suite"]),
        ("expires_at", "next year"),
        ("content_hash", "sha256:abc"),
        ("budgets", {"tokens": 0, "tool_calls": 1, "wall_clock_seconds": 1}),
        ("budgets", {"tokens": 1, "tool_calls": -1, "wall_clock_seconds": 1}),
        ("required_telemetry", []),
        ("required_evidence", []),
    ],
)
def test_an_invalid_value_is_refused(field: str, value: object) -> None:
    refused(manifest_data(**{field: value}))


def test_output_schema_names_an_agent_result() -> None:
    assert {"InvestigationResult", "VerificationResult"} <= AGENT_RESULT_SCHEMAS
    assert "AgentResult" not in AGENT_RESULT_SCHEMAS
    for schema in AGENT_RESULT_SCHEMAS:
        assert parse_manifest(manifest_data(output_schema=schema)).output_schema == schema


@pytest.mark.parametrize(
    "triggers",
    [
        {"rule_ids": [0], "log_source_types": [], "attack_techniques": []},
        {"rule_ids": [-5], "log_source_types": [], "attack_techniques": []},
        {"rule_ids": [], "log_source_types": [""], "attack_techniques": []},
        {"rule_ids": [], "log_source_types": [" Windows"], "attack_techniques": []},
        {"rule_ids": [], "log_source_types": [], "attack_techniques": ["t1003"]},
        {"rule_ids": [], "log_source_types": [], "attack_techniques": ["T1003.6"]},
        {"rule_ids": [], "log_source_types": [], "attack_techniques": ["T10030"]},
        {"rule_ids": [], "log_source_types": [], "attack_techniques": ["TA0006"]},
        {"rule_ids": [], "log_source_types": []},
    ],
)
def test_invalid_triggers_are_refused(triggers: dict[str, object]) -> None:
    refused(manifest_data(triggers=triggers))


def test_a_skill_needs_at_least_one_trigger() -> None:
    error = refused(
        manifest_data(triggers={"rule_ids": [], "log_source_types": [], "attack_techniques": []})
    )
    assert "at least one trigger" in str(error)


@pytest.mark.parametrize(
    "triggers",
    [
        {"rule_ids": [100001], "log_source_types": [], "attack_techniques": []},
        {"rule_ids": [], "log_source_types": ["Cisco ASA"], "attack_techniques": []},
        {"rule_ids": [], "log_source_types": [], "attack_techniques": ["T1003"]},
    ],
)
def test_one_trigger_of_any_kind_is_enough(triggers: dict[str, object]) -> None:
    parse_manifest(manifest_data(triggers=triggers))


def test_some_telemetry_must_be_required() -> None:
    telemetry = [{"log_source_type": "Cisco ASA", "events": ["VPN logins"], "required": False}]
    error = refused(manifest_data(required_telemetry=telemetry))
    assert "required: true" in str(error)


def test_telemetry_needs_events() -> None:
    telemetry = [{"log_source_type": "Cisco ASA", "events": [], "required": True}]
    refused(manifest_data(required_telemetry=telemetry))


def test_evidence_ids_are_unique() -> None:
    evidence = [
        {"id": "failures", "description": "The failed logons"},
        {"id": "failures", "description": "The successful logons"},
    ]
    error = refused(manifest_data(required_evidence=evidence))
    assert "failures" in str(error)


# --- approval fields -----------------------------------------------------------------------


def test_an_approved_skill_has_a_hash_and_an_approver() -> None:
    manifest = parse_manifest(
        manifest_data(status="approved", content_hash=SHA, approved_by="reviewer-a")
    )
    assert manifest.content_hash == SHA


@pytest.mark.parametrize(
    "changes",
    [
        {"content_hash": None, "approved_by": "reviewer-a"},
        {"content_hash": SHA, "approved_by": None},
        {"content_hash": None, "approved_by": None},
    ],
)
def test_an_approved_skill_without_hash_or_approver_is_refused(changes: dict[str, object]) -> None:
    error = refused(manifest_data(status="approved", **changes))
    assert "content_hash and approved_by" in str(error)


@pytest.mark.parametrize("changes", [{"content_hash": SHA}, {"approved_by": "reviewer-a"}])
def test_a_draft_has_no_approval_fields(changes: dict[str, object]) -> None:
    error = refused(manifest_data(**changes))
    assert "draft" in str(error)


# --- expiry --------------------------------------------------------------------------------


def test_a_skill_expires_at_midnight_utc_on_its_expiry_date() -> None:
    manifest = parse_manifest(manifest_data(expires_at=date(2027, 10, 1)))
    midnight = datetime(2027, 10, 1, tzinfo=UTC)
    assert manifest.expiry == midnight
    assert not manifest.is_expired(midnight - timedelta(microseconds=1))
    assert manifest.is_expired(midnight)
    # 02:59 in Istanbul is 23:59 UTC the day before.
    istanbul = timezone(timedelta(hours=3))
    assert not manifest.is_expired(datetime(2027, 10, 1, 2, 59, tzinfo=istanbul))
    assert manifest.is_expired(datetime(2027, 10, 1, 3, 0, tzinfo=istanbul))


def test_expiry_needs_an_aware_time() -> None:
    manifest = parse_manifest(manifest_data())
    with pytest.raises(ValueError, match="timezone-aware"):
        manifest.is_expired(datetime(2026, 10, 4, 12, 0))  # noqa: DTZ001 - the naive time is the test
