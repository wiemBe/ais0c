"""T-043 criterion 5: the prompt's Skill section (decisions T-36 (1), T-44).

With a skill the section is its instructions as they are, then its required telemetry and its
required evidence in a fixed format. Without one it is a fixed sentence. Either way it is part
of the prompt, not untrusted data.
"""

import pytest
from pydantic import ValidationError

from ais0c_agents import NO_SKILL, SkillInput, SkillTelemetrySource, render_skill
from ais0c_knowledge.skills import Skill, load_skills

from .helpers import REPO_ROOT, SUMMARY_PROMPT, lenient_tags

INSTRUCTIONS = """## Purpose

Investigate an offense that points to DCSync.

## Steps

1. Find the 4662 events with a DS-Replication right.
"""


def skill_input(**overrides: object) -> SkillInput:
    data: dict[str, object] = {
        "ref": {
            "skill_id": "windows-dcsync",
            "version": "1.0.0",
            "content_hash": "sha256:" + "0" * 64,
        },
        "allowed_agent_roles": ["investigation"],
        "budgets": {"tokens": 120000, "tool_calls": 24, "wall_clock_seconds": 300},
        "instructions": INSTRUCTIONS,
        "required_telemetry": [
            {
                "telemetry_class": "windows",
                "events": ["4662 on domain controllers: an operation with a DS-Replication right"],
                "required": True,
            },
            {
                "telemetry_class": "windows",
                "events": ["4624 network logons of the account", "4672 special privileges"],
                "required": False,
            },
        ],
        "required_evidence": [
            {"id": "replication-events", "description": "The 4662 events: time, DC and account"},
            {"id": "request-source", "description": "The source address of the account's logon"},
        ],
    }
    return SkillInput.model_validate(data | overrides)


def test_with_a_skill_the_section_is_its_instructions_telemetry_and_evidence() -> None:
    assert render_skill(skill_input()) == (
        "## Purpose\n"
        "\n"
        "Investigate an offense that points to DCSync.\n"
        "\n"
        "## Steps\n"
        "\n"
        "1. Find the 4662 events with a DS-Replication right.\n"
        "\n"
        "## Required telemetry\n"
        "\n"
        "- windows (required):\n"
        "  - 4662 on domain controllers: an operation with a DS-Replication right\n"
        "- windows (optional):\n"
        "  - 4624 network logons of the account\n"
        "  - 4672 special privileges\n"
        "\n"
        "## Required evidence\n"
        "\n"
        "- replication-events: The 4662 events: time, DC and account\n"
        "- request-source: The source address of the account's logon"
    )


def test_without_a_skill_the_section_is_the_fixed_sentence() -> None:
    assert render_skill(None) == NO_SKILL
    assert NO_SKILL == "No skill was selected for this case: investigate with the general method."


def test_render_names_the_installation_sources() -> None:
    section = render_skill(
        skill_input(
            required_telemetry=[
                {
                    "telemetry_class": "waf",
                    "events": ["Request log with attack_type SQL-Injection: ..."],
                    "required": True,
                }
            ],
            telemetry_sources=(
                SkillTelemetrySource(
                    telemetry_class="waf",
                    type_name="F5 Networks BIG-IP ASM",
                    log_source_ids=(21,),
                    total=1,
                ),
            ),
        )
    )

    assert (
        "- waf (required):\n"
        "  - In this installation: F5 Networks BIG-IP ASM, 1 log source (logsourceid 21).\n"
        "  - Request log with attack_type SQL-Injection: ..."
    ) in section


def test_render_says_when_a_class_has_no_source() -> None:
    section = render_skill(
        skill_input(
            required_telemetry=[
                {
                    "telemetry_class": "firewall",
                    "events": [
                        "Traffic from the web server to external addresses after the requests ..."
                    ],
                    "required": False,
                }
            ],
            telemetry_sources=(),
        )
    )

    assert (
        "- firewall (optional):\n"
        "  - In this installation: no enabled log source of this class; report a data gap "
        "for it.\n"
        "  - Traffic from the web server to external addresses after the requests ..."
    ) in section


def test_render_without_resolution_is_unchanged() -> None:
    assert render_skill(skill_input(telemetry_sources=None)) == render_skill(skill_input())


def test_render_caps_ids_at_20() -> None:
    section = render_skill(
        skill_input(
            telemetry_sources=(
                SkillTelemetrySource(
                    telemetry_class="windows",
                    type_name="Microsoft Windows Security Event Log",
                    log_source_ids=tuple(range(12, 32)),
                    total=34,
                ),
            )
        )
    )

    ids = ", ".join(str(log_source_id) for log_source_id in range(12, 32))
    assert (
        "  - In this installation: Microsoft Windows Security Event Log, 34 log sources "
        f"(logsourceid {ids}, first 20 of 34)."
    ) in section


@pytest.mark.parametrize("unsafe_name", ["Unsafe\nType", "Unsafe<Type"])
def test_unsafe_type_name_is_not_rendered(unsafe_name: str) -> None:
    with pytest.raises(ValidationError):
        SkillTelemetrySource(
            telemetry_class="windows",
            type_name=unsafe_name,
            log_source_ids=(12,),
            total=1,
        )

    section = render_skill(
        skill_input(
            telemetry_sources=(
                SkillTelemetrySource(
                    telemetry_class="windows",
                    type_name=None,
                    log_source_ids=(12,),
                    total=1,
                ),
            )
        )
    )
    assert "In this installation: a custom type, 1 log source (logsourceid 12)." in section
    assert unsafe_name not in section


def test_the_section_is_part_of_the_prompt_not_untrusted_data() -> None:
    for skill in (skill_input(), None):
        prompt = SUMMARY_PROMPT.render({"evidence": "No evidence.", "skill": render_skill(skill)})

        assert f"# Skill\n{render_skill(skill)}\n" in prompt
        assert lenient_tags(prompt) == []


def test_the_instructions_come_as_they_are() -> None:
    text = 'Keep `code`, *emphasis*, "quotes" and   spacing.\n\n\n'

    section = render_skill(skill_input(instructions=text))

    assert section.startswith('Keep `code`, *emphasis*, "quotes" and   spacing.\n\n## Required')


@pytest.mark.parametrize(
    "update",
    [
        {"instructions": ""},
        {"required_telemetry": []},
        {"required_evidence": [{"id": "Not A Slug", "description": "x"}]},
        {"required_evidence": [{"id": "source", "description": "two\nlines"}]},
        {"ref": {"skill_id": "windows-dcsync", "version": "1.0.0", "content_hash": "abc"}},
    ],
    ids=["empty instructions", "no telemetry", "bad evidence id", "two lines", "bad hash"],
)
def test_a_skill_input_is_validated(update: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        skill_input(**update)


def from_skill(skill: Skill) -> SkillInput:
    """What the activity building an agent's task does with a loaded skill (T-026)."""
    manifest = skill.manifest
    return SkillInput.model_validate(
        {
            "ref": skill.ref,
            "allowed_agent_roles": manifest.allowed_agent_roles,
            "budgets": manifest.budgets.model_dump(),
            "instructions": skill.instructions,
            "required_telemetry": [item.model_dump() for item in manifest.required_telemetry],
            "required_evidence": [item.model_dump() for item in manifest.required_evidence],
        }
    )


def test_every_repository_skill_becomes_a_skill_input() -> None:
    skills = list(load_skills(REPO_ROOT / "skills", mode="dev"))

    on_disk = {path.name for path in (REPO_ROOT / "skills").iterdir() if path.is_dir()}
    assert {skill.manifest.id for skill in skills} == on_disk
    assert "windows-dcsync" in on_disk
    for skill in skills:
        section = render_skill(from_skill(skill))
        assert section.startswith(skill.instructions.rstrip("\n") + "\n\n## Required telemetry\n")
        for evidence in skill.manifest.required_evidence:
            assert f"- {evidence.id}: {evidence.description}" in section
