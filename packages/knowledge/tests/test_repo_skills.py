"""The repo's skill catalog (T-064): skills/CATALOG.md is the single source. Every row
matches a loaded draft skill (id, techniques, status, suite); the directories, the catalog
and the loader agree; instructions are English and follow the guide's section order."""

import re

import pytest

from ais0c_knowledge.skills import Skill, candidate_skills, load_skills

from .skill_helpers import F5_ASM, FORTIGATE, NOW, SKILLS_DIR, WINDOWS_SECURITY, enrichment, offense

# The guide's section order (docs/impl/skill-authoring.md, section 4); mandatory ones
# marked. A skill's sections are a subsequence of this list.
SECTION_ORDER = {
    "Purpose": True,
    "Check the telemetry first": True,
    "How it looks in the logs": False,
    "Steps": True,
    "Attempt or impact": False,
    "Benign lookalikes": False,
    "Verdict": True,
    "Level": False,
    "Urgent events": False,
}
_ROW = re.compile(
    r"^\| ([a-z0-9-]+) \| (\d+\.\d+\.\d+) \| (.*?) \| (.*?) \| (.*?) \| (\w+) \| (\S+) \|$"
)


def catalog_rows() -> dict[str, tuple[str, str, list[str], list[str], str]]:
    """id -> (group, version, techniques, telemetry names, status) from CATALOG.md."""
    rows: dict[str, tuple[str, str, list[str], list[str], str]] = {}
    group = None
    for line in (SKILLS_DIR / "CATALOG.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            group = line.removeprefix("## ").strip().lower()
        match = _ROW.match(line)
        if match:
            sid, version, techniques, telemetry, _scenario, status, _suite = match.groups()
            assert sid not in rows, f"catalog row repeats {sid}"
            rows[sid] = (
                group or "",
                version,
                [tech.strip() for tech in techniques.split(",")],
                [name.strip() for name in telemetry.split(",")],
                status,
            )
    assert rows, "no catalog rows parsed"
    return rows


@pytest.fixture(scope="module")
def skills() -> dict[str, Skill]:
    return {skill.manifest.id: skill for skill in load_skills(SKILLS_DIR, mode="dev")}


def test_the_catalog_and_the_directories_agree(skills: dict[str, Skill]) -> None:
    rows = catalog_rows()
    on_disk = {path.name for path in SKILLS_DIR.iterdir() if path.is_dir()}
    assert set(skills) == set(rows) == on_disk
    for sid, skill in skills.items():
        group, version, _techniques, _telemetry, status = rows[sid]
        assert group in ("internal", "external")
        assert skill.manifest.version == version
        assert skill.manifest.status == status == "draft"
        assert skill.manifest.content_hash is None
        assert skill.manifest.approved_by is None
        assert skill.directory == SKILLS_DIR / sid / version


def test_production_loads_none_of_them() -> None:
    assert len(load_skills(SKILLS_DIR, mode="prod")) == 0


@pytest.mark.parametrize("skill_id", sorted(catalog_rows()))
def test_triggers_telemetry_and_evidence_match_the_catalog_row(
    skills: dict[str, Skill], skill_id: str
) -> None:
    manifest = skills[skill_id].manifest
    _group, _version, techniques, telemetry, _status = catalog_rows()[skill_id]
    assert set(techniques) <= set(manifest.triggers.attack_techniques)
    required = [item for item in manifest.required_telemetry if item.required]
    assert any(item.log_source_type in telemetry for item in required)
    assert len(manifest.required_evidence) >= 3
    assert manifest.allowed_agent_roles == {"investigation"}
    assert manifest.output_schema == "InvestigationResult"
    assert manifest.budgets.tokens >= 600000
    assert f"skill-{skill_id}" in manifest.eval_suites
    assert "prompt-injection" in manifest.eval_suites


@pytest.mark.parametrize("skill_id", sorted(catalog_rows()))
def test_instructions_are_english_with_the_guide_sections(
    skills: dict[str, Skill], skill_id: str
) -> None:
    text = skills[skill_id].instructions
    assert text.isascii()
    assert not re.search(r"[çğıöşüÇĞİÖŞÜ]", text)
    # Level-2 headings: the prompt places the text under its own Skill section.
    headings = re.findall(r"^(#+) (.+)$", text, flags=re.MULTILINE)
    assert {level for level, _ in headings} == {"##"}
    titles = [title for _, title in headings]
    assert len(titles) == len(set(titles)), "a section appears twice"
    assert all(title in SECTION_ORDER for title in titles), "unknown section"
    for title, mandatory in SECTION_ORDER.items():
        if mandatory:
            assert title in titles
    # The observed order follows the guide's order (skips absent optional sections).
    positions = [list(SECTION_ORDER).index(title) for title in titles]
    assert positions == sorted(positions)
    for word in ("the", "and", "evidence", "data gap"):
        assert word in text


@pytest.mark.parametrize("skill_id", sorted(catalog_rows()))
def test_rule_ids_are_left_for_the_production_qradar(
    skills: dict[str, Skill], skill_id: str
) -> None:
    # Rule IDs differ between QRadar installations: an approved skill carries the production
    # QRadar's, and the lab uses the technique trigger (T-26).
    assert skills[skill_id].manifest.triggers.rule_ids == frozenset()


def test_drafts_are_inert_even_when_an_offense_matches(skills: dict[str, Skill]) -> None:
    registry = load_skills(SKILLS_DIR, mode="dev")
    techniques = sorted(
        {tech for _g, _v, techs, _t, _s in catalog_rows().values() for tech in techs}
    )
    # A rule maps to at most 20 techniques: chunk the catalog's across as many rules.
    rule_techniques = {
        100000 + index: techniques[start : start + 20]
        for index, start in enumerate(range(0, len(techniques), 20))
    }
    catalog = enrichment(
        log_source_types={1: WINDOWS_SECURITY, 2: FORTIGATE, 3: F5_ASM},
        rule_techniques=rule_techniques,
    )
    refs = candidate_skills(
        registry,
        offense(rule_ids=sorted(rule_techniques), log_source_ids=[1, 2, 3]),
        catalog,
        agent_role="investigation",
        now=NOW,
    )
    assert refs == []
