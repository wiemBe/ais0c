"""Acceptance criterion 4: candidate_skills is deterministic, looks at the offense's rule IDs,
log source types and ATT&CK techniques, and returns only approved, unexpired skills that allow
the agent's role, as SkillRefs in a fixed order.

The log source types and techniques come from the offense's Analysis Catalog entries in its
EnrichmentContext (decision T-26)."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ais0c_contracts import EnrichmentContext, SkillRef
from ais0c_knowledge.skills import (
    AgentRole,
    SkillRegistry,
    candidate_skills,
    load_skill,
    load_skills,
)

from .skill_helpers import (
    FORTIGATE,
    NOW,
    WINDOWS_SECURITY,
    enrichment,
    manifest_data,
    offense,
    write_skill,
)

# The by-rule skill's trigger.
RULE = 100001
# A rule the catalog maps to the by-technique skill's technique, and one mapped to another.
SPRAY_RULE = 100002
OTHER_RULE = 100003
# A FortiGate (the by-type skill's trigger) and a Windows log source.
LOG_SOURCE = 412
OTHER_LOG_SOURCE = 413
CATALOG = enrichment(
    log_source_types={LOG_SOURCE: FORTIGATE, OTHER_LOG_SOURCE: WINDOWS_SECURITY},
    rule_techniques={SPRAY_RULE: ["T1110.003", "T1078"], OTHER_RULE: ["T1003.006"]},
)


def triggers(
    *,
    rule_ids: list[int] | None = None,
    types: list[str] | None = None,
    techniques: list[str] | None = None,
) -> dict[str, list[Any]]:
    return {
        "rule_ids": rule_ids or [],
        "log_source_types": types or [],
        "attack_techniques": techniques or [],
    }


def approved(root: Path, skill_id: str, **changes: object) -> Path:
    data = manifest_data(id=skill_id, eval_suites=[f"skill-{skill_id}"], **changes)
    return write_skill(root, data, approve=True)


@pytest.fixture
def registry(tmp_path: Path) -> SkillRegistry:
    approved(tmp_path, "by-rule", triggers=triggers(rule_ids=[RULE]))
    approved(tmp_path, "by-type", triggers=triggers(types=[FORTIGATE]))
    approved(tmp_path, "by-technique", triggers=triggers(techniques=["T1110.003"]))
    return load_skills(tmp_path, mode="prod")


def candidates(
    registry: SkillRegistry,
    *,
    rule_ids: list[int] | None = None,
    log_source_ids: list[int] | None = None,
    catalog: EnrichmentContext = CATALOG,
    role: AgentRole = "investigation",
    now: datetime = NOW,
) -> list[str]:
    refs = candidate_skills(
        registry,
        offense(rule_ids=rule_ids, log_source_ids=log_source_ids),
        catalog,
        agent_role=role,
        now=now,
    )
    return [ref.skill_id for ref in refs]


# --- each kind of trigger ------------------------------------------------------------------


def test_a_rule_id_triggers_a_skill(registry: SkillRegistry) -> None:
    assert candidates(registry, rule_ids=[999, RULE]) == ["by-rule"]


def test_a_log_source_type_triggers_a_skill(registry: SkillRegistry) -> None:
    assert candidates(registry, log_source_ids=[LOG_SOURCE]) == ["by-type"]


def test_an_attack_technique_of_a_rule_triggers_a_skill(registry: SkillRegistry) -> None:
    assert candidates(registry, rule_ids=[SPRAY_RULE]) == ["by-technique"]


def test_no_match_gives_an_empty_list(registry: SkillRegistry) -> None:
    assert candidates(registry) == []
    assert candidates(registry, rule_ids=[OTHER_RULE], log_source_ids=[OTHER_LOG_SOURCE]) == []


def test_an_empty_registry_gives_an_empty_list() -> None:
    assert candidates(SkillRegistry([]), rule_ids=[RULE, SPRAY_RULE]) == []


@pytest.mark.parametrize("technique", ["T1110", "T1110.001", "T1110.004"])
def test_techniques_are_compared_exactly(registry: SkillRegistry, technique: str) -> None:
    # The by-technique skill triggers on T1110.003 only: neither its parent nor a sibling.
    catalog = enrichment(rule_techniques={OTHER_RULE: [technique]})
    assert candidates(registry, rule_ids=[OTHER_RULE], catalog=catalog) == []


def test_only_the_offenses_own_catalog_entries_count(registry: SkillRegistry) -> None:
    # The catalog knows log source 412 is a FortiGate and that rule 100002 is spraying, but this
    # offense has neither.
    assert candidates(registry, rule_ids=[OTHER_RULE], log_source_ids=[OTHER_LOG_SOURCE]) == []
    # The offense's own log source and rule, without catalog entries, match nothing either.
    assert candidates(registry, rule_ids=[999], log_source_ids=[999]) == []


def test_catalog_entries_without_a_type_or_techniques_match_nothing(
    registry: SkillRegistry,
) -> None:
    catalog = enrichment(log_source_types={LOG_SOURCE: None}, rule_techniques={SPRAY_RULE: None})
    assert (
        candidates(registry, rule_ids=[SPRAY_RULE], log_source_ids=[LOG_SOURCE], catalog=catalog)
        == []
    )


def test_every_matching_skill_is_listed_in_id_order(registry: SkillRegistry) -> None:
    listed = candidates(registry, rule_ids=[RULE, SPRAY_RULE], log_source_ids=[LOG_SOURCE])
    assert listed == ["by-rule", "by-technique", "by-type"]


def test_the_order_does_not_depend_on_the_registry_order(tmp_path: Path) -> None:
    skills = [
        load_skill(approved(tmp_path, skill_id, triggers=triggers(rule_ids=[RULE])))
        for skill_id in ("zulu", "alpha", "mike")
    ]
    for ordering in (skills, skills[::-1], [skills[1], skills[2], skills[0]]):
        assert candidates(SkillRegistry(ordering), rule_ids=[RULE]) == ["alpha", "mike", "zulu"]


def test_the_same_arguments_give_the_same_list(registry: SkillRegistry) -> None:
    arguments: dict[str, Any] = {"rule_ids": [RULE, SPRAY_RULE], "log_source_ids": [LOG_SOURCE]}
    assert candidates(registry, **arguments) == candidates(registry, **arguments)


def test_candidates_are_skill_refs_with_the_content_hash(registry: SkillRegistry) -> None:
    [ref] = candidate_skills(
        registry, offense(rule_ids=[RULE]), enrichment(), agent_role="investigation", now=NOW
    )
    assert isinstance(ref, SkillRef)
    skill = registry.get("by-rule", "1.0.0")
    assert skill is not None
    assert ref == SkillRef(skill_id="by-rule", version="1.0.0", content_hash=skill.content_hash)
    assert ref.content_hash == skill.manifest.content_hash


# --- what is never offered -----------------------------------------------------------------


def test_a_draft_is_never_a_candidate(tmp_path: Path) -> None:
    write_skill(tmp_path, manifest_data(id="draft-skill", triggers=triggers(rule_ids=[RULE])))
    registry = load_skills(tmp_path, mode="dev")
    assert len(registry) == 1
    assert candidates(registry, rule_ids=[RULE]) == []


def test_an_expired_skill_is_not_a_candidate(tmp_path: Path) -> None:
    approved(tmp_path, "expiring", triggers=triggers(rule_ids=[RULE]), expires_at=date(2027, 1, 1))
    registry = load_skills(tmp_path, mode="prod")
    expiry = datetime.fromisoformat("2027-01-01T00:00:00+00:00")
    assert candidates(registry, rule_ids=[RULE], now=expiry - timedelta(seconds=1)) == ["expiring"]
    assert candidates(registry, rule_ids=[RULE], now=expiry) == []
    assert candidates(registry, rule_ids=[RULE], now=expiry + timedelta(days=400)) == []


@pytest.mark.parametrize("role", ["verification", "triage", "orchestrator", "reporting"])
def test_a_skill_is_offered_only_to_its_roles(registry: SkillRegistry, role: AgentRole) -> None:
    assert candidates(registry, rule_ids=[RULE], role="investigation") == ["by-rule"]
    assert candidates(registry, rule_ids=[RULE], role=role) == []


def test_a_skill_may_allow_several_roles(tmp_path: Path) -> None:
    approved(
        tmp_path,
        "shared",
        triggers=triggers(rule_ids=[RULE]),
        allowed_agent_roles=["investigation", "verification"],
    )
    registry = load_skills(tmp_path, mode="prod")
    assert candidates(registry, rule_ids=[RULE], role="verification") == ["shared"]
    assert candidates(registry, rule_ids=[RULE], role="reporting") == []


# --- versions ------------------------------------------------------------------------------


def write_version(root: Path, version: str, *, approve: bool = True, **changes: object) -> None:
    data = manifest_data(version=version, triggers=triggers(rule_ids=[RULE]), **changes)
    write_skill(root, data, approve=approve)


def offered_versions(root: Path, *, role: AgentRole = "investigation") -> list[str]:
    registry = load_skills(root, mode="dev")
    refs = candidate_skills(
        registry, offense(rule_ids=[RULE]), enrichment(), agent_role=role, now=NOW
    )
    return [f"{ref.skill_id} {ref.version}" for ref in refs]


def test_only_the_latest_approved_version_is_offered(tmp_path: Path) -> None:
    for version in ("1.0.0", "1.2.0", "1.10.0"):
        write_version(tmp_path, version)
    assert offered_versions(tmp_path) == ["test-skill 1.10.0"]


def test_a_newer_draft_does_not_replace_the_approved_version(tmp_path: Path) -> None:
    write_version(tmp_path, "1.0.0")
    write_version(tmp_path, "2.0.0", approve=False)
    assert offered_versions(tmp_path) == ["test-skill 1.0.0"]


def test_an_expired_latest_version_does_not_fall_back_to_an_older_one(tmp_path: Path) -> None:
    write_version(tmp_path, "1.0.0", expires_at=date(2030, 1, 1))
    write_version(tmp_path, "1.1.0", expires_at=date(2026, 1, 1))
    assert offered_versions(tmp_path) == []


def test_a_role_the_latest_version_dropped_gets_no_older_version(tmp_path: Path) -> None:
    write_version(tmp_path, "1.0.0", allowed_agent_roles=["investigation", "verification"])
    write_version(tmp_path, "1.1.0", allowed_agent_roles=["investigation"])
    assert offered_versions(tmp_path, role="verification") == []
    assert offered_versions(tmp_path, role="investigation") == ["test-skill 1.1.0"]


# --- inputs --------------------------------------------------------------------------------


def test_now_must_be_timezone_aware(registry: SkillRegistry) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        candidates(registry, rule_ids=[RULE], now=datetime(2026, 10, 4, 12, 0))  # noqa: DTZ001


def test_the_router_reads_no_clock(registry: SkillRegistry) -> None:
    # Only `now` decides expiry: a time far in the past or the future works as given.
    assert candidates(registry, rule_ids=[RULE], now=NOW - timedelta(days=3650)) == ["by-rule"]
    assert candidates(registry, rule_ids=[RULE], now=NOW + timedelta(days=3650)) == []
