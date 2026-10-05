"""Shared types and the "Kanıt ve ortak parçalar" models of docs/impl/contracts.md."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from ais0c_contracts.enums import ActionType, DataGapReason, EvidenceSource

SHORT_TEXT_MAX_LENGTH = 300
SUMMARY_MAX_LENGTH = 600
RUN_ID_MAX_LENGTH = 200

ShortText = Annotated[str, StringConstraints(max_length=SHORT_TEXT_MAX_LENGTH)]
Summary = Annotated[str, StringConstraints(max_length=SUMMARY_MAX_LENGTH)]

# Issued by the gateway, never by an agent.
EvidenceId = Annotated[str, StringConstraints(pattern=r"^ev_\S+$")]

# The form of every run ID the platform issues (v0.4, T-37): workflow IDs such as
# `case-12345-triage-1` and `case-12345-triage-1-retry`, the UUIDs of the pseudo agent runs and
# hunt IDs. A letter or digit first, then letters, digits, `.`, `_`, `:` and `-`; ASCII only.
RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"

# An agent run (`agent_runs.run_id`); set by the platform, never by a model.
RunId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=RUN_ID_MAX_LENGTH, pattern=RUN_ID_PATTERN),
]

# An ATT&CK technique or sub-technique ID, e.g. T1003 or T1003.006 (v0.3, T-26).
AttackTechnique = Annotated[str, StringConstraints(pattern=r"^T[0-9]{4}(\.[0-9]{3})?$")]


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


# Naive datetimes are rejected; aware ones are stored in UTC.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


class ContractModel(BaseModel):
    """Base of every contract model: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


def check_case_or_hunt(case_id: str | None, hunt_id: str | None) -> None:
    """Raise unless at least one of `case_id` and `hunt_id` is set."""
    if not case_id and not hunt_id:
        raise ValueError("case_id or hunt_id is required")


class TimeWindow(ContractModel):
    start: UtcDatetime
    end: UtcDatetime


class Budget(ContractModel):
    tokens: int
    tool_calls: int
    seconds: int


class Usage(ContractModel):
    tokens: int
    tool_calls: int
    seconds: float


class EvidenceRef(ContractModel):
    """Pointer to evidence recorded by the gateway; agents cannot create one."""

    evidence_id: EvidenceId
    source: EvidenceSource
    query_hash: str
    query_text: Annotated[str, StringConstraints(max_length=4000)]
    time_start: UtcDatetime
    time_end: UtcDatetime
    # Enough to find the event at the source: time, log source ID, QID, ...
    identifiers: dict[str, str]
    excerpt: Annotated[str, StringConstraints(max_length=500)]
    retrieved_at: UtcDatetime


class Claim(ContractModel):
    text: ShortText
    evidence_ids: Annotated[list[EvidenceId], Field(min_length=1)]


class DataGap(ContractModel):
    source: str
    period_start: UtcDatetime
    period_end: UtcDatetime
    reason: DataGapReason


class Recommendation(ContractModel):
    action_type: ActionType
    target: Annotated[str, StringConstraints(max_length=200)]
    rationale: ShortText
    evidence_ids: list[EvidenceId]


class UrgentEvent(ContractModel):
    """An event the operator should look at first (architecture §9)."""

    rank: Annotated[int, Field(ge=1)]
    time: UtcDatetime
    log_source: Annotated[str, StringConstraints(max_length=120)]
    event_name: Annotated[str, StringConstraints(max_length=200)]
    qid: int | None = None
    source: Annotated[str, StringConstraints(max_length=100)] | None = None
    destination: Annotated[str, StringConstraints(max_length=100)] | None = None
    username: Annotated[str, StringConstraints(max_length=100)] | None = None
    reason: ShortText
    checklist: Annotated[list[ShortText], Field(max_length=5)]
    # Must have passed the AQL Guard.
    aql: Annotated[str, StringConstraints(max_length=2000)] | None = None
    evidence_id: EvidenceId
