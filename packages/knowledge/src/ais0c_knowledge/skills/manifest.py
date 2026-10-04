"""Skill manifests (architecture §7, "Skill'ler"; decision T-21): skills/<id>/<version>/skill.yaml.

The manifest has exactly the fields of architecture §7; any other field is rejected. A skill
describes a method and grants nothing, so a field that reads like a grant of tools, permissions
or scripts (`tools`, `allowed_tools`, `permissions`, ...) is rejected with its own error,
SkillPermissionError, wherever it appears in the manifest.

The models are plain Pydantic models, not contracts: only the loader and the router read them.
"""

from collections.abc import Set
from datetime import UTC, date, datetime, time
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

import ais0c_contracts
from ais0c_contracts import AgentResult, AttackTechnique
from ais0c_knowledge.skills.errors import SkillError, SkillPermissionError

# Lowercase words joined by hyphens, e.g. windows-dcsync; skill, evidence and suite names.
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", max_length=63)]
# MAJOR.MINOR.PATCH, as the agent manifests' versions.
SemVer = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){2}$")]
# `sha256:` and 64 lowercase hex digits, as SkillRef.content_hash.
ContentHash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
# One line of text without leading or trailing spaces.
_ONE_LINE = r"^\S(.*\S)?$"
# A QRadar log source type name, e.g. "Microsoft Windows Security Event Log".
LogSourceType = Annotated[str, StringConstraints(max_length=255, pattern=_ONE_LINE)]
# A person or team.
Name = Annotated[str, StringConstraints(max_length=100, pattern=_ONE_LINE)]
Description = Annotated[str, StringConstraints(max_length=300, pattern=_ONE_LINE)]

SkillStatus = Literal["draft", "approved"]
# The agents of architecture §7, named as their prompts/<agent>/ directories (prompts.md).
AgentRole = Literal[
    "orchestrator",
    "triage",
    "investigation",
    "verification",
    "reporting",
    "tuning",
    "hypothesis",
    "hunter-external",
    "hunter-internal",
    "hunt-verifier",
]


def _agent_result_schemas() -> frozenset[str]:
    """Names of the agent result models of ais0c_contracts, e.g. InvestigationResult."""
    names: set[str] = set()
    for name in ais0c_contracts.__all__:
        value = getattr(ais0c_contracts, name)
        if isinstance(value, type) and issubclass(value, AgentResult) and value is not AgentResult:
            names.add(name)
    return frozenset(names)


AGENT_RESULT_SCHEMAS: Final = _agent_result_schemas()


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillTriggers(_ManifestModel):
    """What makes the router offer the skill: any one match is enough."""

    # QRadar rule IDs differ between QRadar installations.
    rule_ids: Annotated[frozenset[PositiveInt], Field(max_length=100)]
    log_source_types: Annotated[frozenset[LogSourceType], Field(max_length=100)]
    # ATT&CK technique IDs, as the catalog maps them to rules (T-26). Matched exactly: T1003
    # does not match T1003.006, nor the other way round.
    attack_techniques: Annotated[frozenset[AttackTechnique], Field(max_length=100)]

    @model_validator(mode="after")
    def _at_least_one(self) -> Self:
        if not (self.rule_ids or self.log_source_types or self.attack_techniques):
            raise ValueError(
                "a skill needs at least one trigger: a rule ID, a log source type or an "
                "ATT&CK technique"
            )
        return self

    def matches(
        self, *, rule_ids: Set[int], log_source_types: Set[str], attack_techniques: Set[str]
    ) -> bool:
        """Whether an offense with these rule IDs, log source types and techniques triggers it."""
        return bool(
            self.rule_ids & rule_ids
            or self.log_source_types & log_source_types
            or self.attack_techniques & attack_techniques
        )


class TelemetryRequirement(_ManifestModel):
    """Telemetry the method reads. Without a `required` source the agent reports a data gap
    instead of concluding; an optional one only adds detail."""

    log_source_type: LogSourceType
    events: Annotated[tuple[Description, ...], Field(min_length=1, max_length=10)]
    required: bool


class EvidenceRequirement(_ManifestModel):
    """Evidence the agent must collect, or report as a data gap, before it concludes."""

    id: Slug
    description: Description


class SkillBudgets(_ManifestModel):
    """Upper limits for one agent run that uses the skill; the fields of the agent manifests'
    budgets (architecture §8.1)."""

    tokens: PositiveInt
    tool_calls: PositiveInt
    wall_clock_seconds: PositiveInt


class SkillManifest(_ManifestModel):
    """The fields of architecture §7. Unknown fields are rejected."""

    id: Slug
    version: SemVer
    status: SkillStatus
    owner: Name
    allowed_agent_roles: Annotated[frozenset[AgentRole], Field(min_length=1)]
    triggers: SkillTriggers
    required_telemetry: Annotated[
        tuple[TelemetryRequirement, ...], Field(min_length=1, max_length=10)
    ]
    required_evidence: Annotated[
        tuple[EvidenceRequirement, ...], Field(min_length=1, max_length=10)
    ]
    budgets: SkillBudgets
    # An agent result model of ais0c_contracts, e.g. InvestigationResult.
    output_schema: str
    # Run by the harness (T-030) before the skill is approved.
    eval_suites: Annotated[frozenset[Slug], Field(min_length=1, max_length=10)]
    # The skill expires at 00:00 UTC on this day; an expired skill is never offered.
    expires_at: date
    # Set on approval, together with status and approved_by; null while the skill is a draft.
    content_hash: ContentHash | None
    approved_by: Name | None

    @field_validator("output_schema")
    @classmethod
    def _agent_result_schema(cls, value: str) -> str:
        if value not in AGENT_RESULT_SCHEMAS:
            raise ValueError(
                f"output_schema must name an agent result model of ais0c_contracts "
                f"({', '.join(sorted(AGENT_RESULT_SCHEMAS))}), not {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _approval_fields(self) -> Self:
        if self.status == "approved":
            if self.content_hash is None or self.approved_by is None:
                raise ValueError("an approved skill needs content_hash and approved_by")
        elif self.content_hash is not None or self.approved_by is not None:
            raise ValueError(
                "a draft skill has content_hash: null and approved_by: null; both are set "
                "when it is approved"
            )
        return self

    @model_validator(mode="after")
    def _some_telemetry_required(self) -> Self:
        if not any(requirement.required for requirement in self.required_telemetry):
            raise ValueError("at least one required_telemetry entry must have required: true")
        return self

    @model_validator(mode="after")
    def _unique_evidence_ids(self) -> Self:
        ids = [requirement.id for requirement in self.required_evidence]
        if duplicates := sorted({item for item in ids if ids.count(item) > 1}):
            raise ValueError(f"required_evidence IDs repeat: {', '.join(duplicates)}")
        return self

    @property
    def expiry(self) -> datetime:
        """When the skill expires: 00:00 UTC on `expires_at`."""
        return datetime.combine(self.expires_at, time(), tzinfo=UTC)

    def is_expired(self, now: datetime) -> bool:
        """Whether the skill has expired at `now`, which must be timezone-aware."""
        if now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime")
        return now >= self.expiry


# Every field name the manifest models define, at any depth.
_KNOWN_FIELDS: Final = frozenset(
    name
    for model in (
        SkillManifest,
        SkillTriggers,
        TelemetryRequirement,
        EvidenceRequirement,
        SkillBudgets,
    )
    for name in model.model_fields
)
# Parts of a field name that read like a grant. Only names the manifest does not define are
# checked, so `allowed_agent_roles` and the budgets' `tool_calls` are fine.
_GRANT_WORDS: Final = (
    "tool",
    "permission",
    "capabilit",
    "scope",
    "grant",
    "privilege",
    "access",
    "allow",
    "action",
    "profile",
    "connector",
    "mcp",
    "polic",
    "role",
    "autonomy",
    "write",
    "exec",
    "script",
    "command",
    "sudo",
    "admin",
)


def parse_manifest(data: object) -> SkillManifest:
    """Validate the data of a skill.yaml.

    Raises SkillPermissionError for a field that reads like a grant of tools, permissions or
    scripts, and SkillError for anything else that is invalid.
    """
    if not isinstance(data, dict):
        raise SkillError("skill.yaml must be a mapping of manifest fields")
    _check_grants(data)
    try:
        return SkillManifest.model_validate(data)
    except ValidationError as error:
        raise SkillError(f"invalid skill manifest: {error}") from error


def _check_grants(data: object, path: str = "") -> None:
    """Raise SkillPermissionError if a mapping key anywhere in `data` reads like a grant."""
    if isinstance(data, dict):
        for key, value in data.items():
            name = str(key)
            where = f"{path}.{name}" if path else name
            if name not in _KNOWN_FIELDS and _reads_like_grant(name):
                raise SkillPermissionError(
                    f"skill.yaml field {where!r} reads like a grant of tools, permissions or "
                    "scripts. A skill grants nothing and runs no scripts: an agent's "
                    "permissions are its toolset, the workflow policy and the gateway policy "
                    "(architecture §7)."
                )
            _check_grants(value, where)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _check_grants(item, f"{path}[{index}]")


def _reads_like_grant(name: str) -> bool:
    letters = "".join(char for char in name.casefold() if char.isalnum())
    return any(word in letters for word in _GRANT_WORDS)
