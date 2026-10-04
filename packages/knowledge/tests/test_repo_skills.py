"""Acceptance criterion 5: windows-dcsync, vpn-new-country and password-spraying are drafts in
skills/, each with triggers, required telemetry, required evidence, English instructions and the
names of its eval suites."""

import re

import pytest

from ais0c_knowledge.skills import Skill, candidate_skills, load_skills

from .skill_helpers import FORTIGATE, NOW, SKILLS_DIR, WINDOWS_SECURITY, offense

FIRST_SKILLS = ("password-spraying", "vpn-new-country", "windows-dcsync")
# The technique each skill is about, and the event its required telemetry must name.
EXPECTED = {
    "windows-dcsync": ("T1003.006", WINDOWS_SECURITY, "4662"),
    "vpn-new-country": ("T1133", FORTIGATE, "tunnel-up"),
    "password-spraying": ("T1110.003", WINDOWS_SECURITY, "4625"),
}


@pytest.fixture(scope="module")
def skills() -> dict[str, Skill]:
    return {skill.manifest.id: skill for skill in load_skills(SKILLS_DIR, mode="dev")}


def test_the_first_three_skills_are_drafts(skills: dict[str, Skill]) -> None:
    assert sorted(skills) == list(FIRST_SKILLS)
    for skill in skills.values():
        assert skill.manifest.version == "1.0.0"
        assert skill.manifest.status == "draft"
        assert skill.manifest.content_hash is None
        assert skill.manifest.approved_by is None
        assert skill.directory == SKILLS_DIR / skill.manifest.id / "1.0.0"


def test_production_loads_none_of_them() -> None:
    assert len(load_skills(SKILLS_DIR, mode="prod")) == 0


@pytest.mark.parametrize("skill_id", FIRST_SKILLS)
def test_triggers_telemetry_and_evidence_are_defined(
    skills: dict[str, Skill], skill_id: str
) -> None:
    manifest = skills[skill_id].manifest
    technique, log_source_type, event = EXPECTED[skill_id]
    assert technique in manifest.triggers.attack_techniques
    required = [item for item in manifest.required_telemetry if item.required]
    assert any(
        item.log_source_type == log_source_type and any(event in text for text in item.events)
        for item in required
    )
    assert len(manifest.required_evidence) >= 3
    assert manifest.allowed_agent_roles == {"investigation"}
    assert manifest.output_schema == "InvestigationResult"


@pytest.mark.parametrize("skill_id", FIRST_SKILLS)
def test_eval_suites_are_listed(skills: dict[str, Skill], skill_id: str) -> None:
    suites = skills[skill_id].manifest.eval_suites
    assert f"skill-{skill_id}" in suites
    assert "prompt-injection" in suites


@pytest.mark.parametrize("skill_id", FIRST_SKILLS)
def test_instructions_are_english(skills: dict[str, Skill], skill_id: str) -> None:
    text = skills[skill_id].instructions
    assert text.isascii()
    assert not re.search(r"[çğıöşüÇĞİÖŞÜ]", text)
    # Level-2 headings: the prompt places the text under its own Skill section.
    headings = re.findall(r"^(#+) (.+)$", text, flags=re.MULTILINE)
    assert {level for level, _ in headings} == {"##"}
    assert [title for _, title in headings][:3] == [
        "Purpose",
        "Check the telemetry first",
        "Steps",
    ]
    for word in ("the", "and", "evidence", "data gap"):
        assert word in text


@pytest.mark.parametrize("skill_id", FIRST_SKILLS)
def test_rule_ids_are_left_for_the_target_qradar(skills: dict[str, Skill], skill_id: str) -> None:
    # Rule IDs differ between QRadar installations; they are added before approval.
    assert skills[skill_id].manifest.triggers.rule_ids == frozenset()


def test_drafts_are_inert_even_when_an_offense_matches(skills: dict[str, Skill]) -> None:
    registry = load_skills(SKILLS_DIR, mode="dev")
    refs = candidate_skills(
        registry,
        offense(log_source_ids=[1]),
        agent_role="investigation",
        log_source_types={1: WINDOWS_SECURITY},
        attack_techniques=[technique for technique, _, _ in EXPECTED.values()],
        now=NOW,
    )
    assert refs == []
