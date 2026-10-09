"""The Skill section of a prompt (docs/impl/prompts.md, "Prompt yapısı"; decisions T-36, T-44).

A skill is a versioned investigation method (architecture §7, "Skill'ler"). The workflow
validates it and hands its content to the agent as a SkillInput. The section is part of the
prompt, not untrusted data: the skill loader (ais0c_knowledge.skills) refused any skill whose
instructions or manifest text fail the injection scan, and review approved it.

The section is in every agent run. With a skill it carries the instructions verbatim, then the
required telemetry and the required evidence in a fixed format. Without one it is NO_SKILL.
"""

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ais0c_agents.manifest import Budgets, Name
from ais0c_contracts import SkillRef

NO_SKILL: Final = "No skill was selected for this case: investigate with the general method."

# One line of text without leading or trailing spaces, as in the skill manifest.
_ONE_LINE = r"^\S(.*\S)?$"
Description = Annotated[str, StringConstraints(max_length=300, pattern=_ONE_LINE)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", max_length=63)]


# Not ContractModels: contract models are defined only in packages/contracts. The fields are
# those of the skill manifest (ais0c_knowledge.skills.manifest), which this package cannot
# import (docs/impl/repo-structure.md).
class SkillTelemetry(BaseModel):
    """Telemetry the method reads; a required source without data is a data gap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    telemetry_class: Slug
    events: Annotated[tuple[Description, ...], Field(min_length=1, max_length=10)]
    required: bool


class SkillTelemetrySource(BaseModel):
    """The installation's enabled log sources of one type that serve a telemetry class."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    telemetry_class: Slug
    type_name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9 ._()/-]{1,255}$")] | None
    log_source_ids: Annotated[tuple[int, ...], Field(max_length=20)]
    total: Annotated[int, Field(ge=1)]


class SkillEvidence(BaseModel):
    """Evidence the agent collects, or reports as a data gap, before it concludes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Slug
    description: Description


class SkillInput(BaseModel):
    """A skill the workflow validated, as an agent's task carries it.

    The activity that builds the task makes it from the loader's Skill: `ref` from its ID,
    version and content hash; the rest from its manifest and instructions.md.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: SkillRef
    """Written to the agent run (architecture §7, "Seçim" 5)."""
    allowed_agent_roles: Annotated[frozenset[Name], Field(min_length=1)]
    budgets: Budgets
    instructions: Annotated[str, StringConstraints(min_length=1)]
    required_telemetry: Annotated[tuple[SkillTelemetry, ...], Field(min_length=1, max_length=10)]
    required_evidence: Annotated[tuple[SkillEvidence, ...], Field(min_length=1, max_length=10)]
    telemetry_sources: tuple[SkillTelemetrySource, ...] | None = None


def render_skill(skill: SkillInput | None) -> str:
    """The text of the prompt's Skill section, for its `{{ skill }}` placeholder.

    With a skill: its instructions as they are, then its required telemetry (telemetry class,
    required or optional, events) and its required evidence (ID and description). Without
    one: NO_SKILL. The text is not wrapped as untrusted data (architecture §7).
    """
    if skill is None:
        return NO_SKILL
    lines = [skill.instructions.rstrip("\n"), "", "## Required telemetry", ""]
    for telemetry in skill.required_telemetry:
        need = "required" if telemetry.required else "optional"
        lines.append(f"- {telemetry.telemetry_class} ({need}):")
        if skill.telemetry_sources is not None:
            sources = [
                source
                for source in skill.telemetry_sources
                if source.telemetry_class == telemetry.telemetry_class
            ]
            if not sources:
                lines.append(
                    "  - In this installation: no enabled log source of this class; "
                    "report a data gap for it."
                )
            for source in sorted(sources, key=lambda item: item.type_name or "a custom type"):
                name = source.type_name if source.type_name is not None else "a custom type"
                noun = "log source" if source.total == 1 else "log sources"
                ids = ", ".join(str(log_source_id) for log_source_id in source.log_source_ids)
                if source.total > len(source.log_source_ids):
                    ids = f"{ids}, first {len(source.log_source_ids)} of {source.total}"
                lines.append(
                    f"  - In this installation: {name}, {source.total} {noun} (logsourceid {ids})."
                )
        lines.extend(f"  - {event}" for event in telemetry.events)
    lines += ["", "## Required evidence", ""]
    lines.extend(f"- {evidence.id}: {evidence.description}" for evidence in skill.required_evidence)
    return "\n".join(lines)
