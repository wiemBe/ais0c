"""Tables of docs/impl/data-model.md as SQLAlchemy ORM classes.

Every table of the document is here except `knowledge_chunks` (embedding model and vector size
are open). The schema itself is created by the Alembic migrations; a test checks that the two
match.

Column types check values on write (`ais0c_storage.columns`): JSON columns against the contract
model named in the document, enum-like text columns against their value set, times for a time
zone. Where the document leaves a type, a primary key or a nullability open, the choice is
marked "Not in data-model.md".
"""

import uuid
from datetime import datetime

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import (
    BigInteger,
    Double,
    ForeignKey,
    Index,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ais0c_contracts import (
    AgentTask,
    CasePlan,
    CaseReport,
    CaseSource,
    CaseVerdict,
    CatalogMode,
    Claim,
    Confidence,
    EmailKind,
    EvidenceSource,
    FeedbackReason,
    HuntOutcome,
    HuntReport,
    HuntRequest,
    InvestigationResult,
    Level,
    QAReason,
    Recommendation,
    RunStatus,
    ToolIntent,
    ToolStatus,
    TriageResult,
    TuningProposal,
    UrgentEvent,
    VerificationResult,
)
from ais0c_storage.columns import ContractJSONB, EnumText, UtcDateTime
from ais0c_storage.enums import (
    ActorKind,
    Analytic,
    CaseStatus,
    CriticalAssetKind,
    GroupStatus,
    HuntPackStatus,
    HuntStatus,
    NoteStatus,
    NotificationStatus,
    OffenseStatus,
    PolicyDecision,
    QAStatus,
    RecipientList,
    SliceStatus,
    TuningProposalStatus,
)
from ais0c_storage.ids import new_uuid7

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

# `agent_runs.result` holds "the matching result model": the output of the agent that ran.
type AgentRunResult = (
    TriageResult | CasePlan | InvestigationResult | VerificationResult | CaseReport
)
AGENT_RUN_RESULT: TypeAdapter[AgentRunResult] = TypeAdapter(AgentRunResult)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - read by SQLAlchemy, never mutated
        str: Text(),
        datetime: UtcDateTime(),
    }


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=new_uuid7)


def _created_at() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def _json() -> Mapped[JsonValue]:
    """`jsonb` for which data-model.md names no contract model."""
    # Explicit, because `JsonValue` itself includes None.
    return mapped_column(JSONB(none_as_null=True), nullable=False)


# --- Offense ve vaka ------------------------------------------------------------------------


class OffenseSeenRow(Base):
    __tablename__ = "offenses_seen"

    offense_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_seen_at: Mapped[datetime]
    last_updated_at: Mapped[datetime]
    description: Mapped[str]
    rule_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    catalog_mode: Mapped[CatalogMode] = mapped_column(EnumText(CatalogMode))
    group_id: Mapped[str | None] = mapped_column(index=True)
    case_id: Mapped[str | None]
    status: Mapped[OffenseStatus] = mapped_column(EnumText(OffenseStatus), index=True)
    # Order of the backlog after an outage (architecture §9).
    pre_priority: Mapped[int]


class OffenseGroupRow(Base):
    __tablename__ = "offense_groups"

    group_id: Mapped[str] = mapped_column(primary_key=True)
    rule_set_hash: Mapped[str] = mapped_column(index=True)
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    offense_count: Mapped[int]
    status: Mapped[GroupStatus] = mapped_column(EnumText(GroupStatus))
    # Case of the group evaluation.
    case_id: Mapped[str | None]


class CaseRow(Base):
    __tablename__ = "cases"

    # `case-<offense_id>`, `case-hunt-<hunt_id>-<n>` or `group-<group_id>`.
    case_id: Mapped[str] = mapped_column(primary_key=True)
    source: Mapped[CaseSource] = mapped_column(EnumText(CaseSource))
    offense_id: Mapped[int | None] = mapped_column(BigInteger)
    hunt_id: Mapped[str | None]
    group_id: Mapped[str | None]
    status: Mapped[CaseStatus] = mapped_column(EnumText(CaseStatus))
    verdict: Mapped[CaseVerdict | None] = mapped_column(EnumText(CaseVerdict))
    confidence: Mapped[Confidence | None] = mapped_column(EnumText(Confidence))
    ai_level: Mapped[Level | None] = mapped_column(EnumText(Level))
    floor_level: Mapped[Level | None] = mapped_column(EnumText(Level))
    notify_level: Mapped[Level | None] = mapped_column(EnumText(Level))
    report: Mapped[CaseReport | None] = mapped_column(ContractJSONB(CaseReport))
    # Incremented by every re-evaluation.
    evaluation_no: Mapped[int]
    sla_due_at: Mapped[datetime]
    # Not in data-model.md: nullable, because a running case has no decision yet.
    decided_at: Mapped[datetime | None]
    workflow_id: Mapped[str]
    run_id: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


class QAItemRow(Base):
    __tablename__ = "qa_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    reason: Mapped[QAReason] = mapped_column(EnumText(QAReason))
    status: Mapped[QAStatus] = mapped_column(EnumText(QAStatus))
    resolved_by: Mapped[str | None]
    resolved_at: Mapped[datetime | None]


class OperatorFeedbackRow(Base):
    __tablename__ = "operator_feedback"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    # OIDC subject of the operator.
    user_subject: Mapped[str]
    verdict: Mapped[CaseVerdict] = mapped_column(EnumText(CaseVerdict))
    reason: Mapped[FeedbackReason] = mapped_column(EnumText(FeedbackReason))
    comment: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()


# --- Ajan çalışmaları ve kanıt --------------------------------------------------------------


class AgentRunRow(Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(primary_key=True)
    case_id: Mapped[str | None] = mapped_column(index=True)
    hunt_id: Mapped[str | None] = mapped_column(index=True)
    agent_id: Mapped[str]
    agent_version: Mapped[str]
    prompt_version: Mapped[str]
    model_alias: Mapped[str]
    model_target: Mapped[str]
    toolset_profile: Mapped[str]
    # Not in data-model.md: `status` and `ended_at` are NULL while the run is in progress, so
    # `tool_calls` rows can reference a run before it ends.
    status: Mapped[RunStatus | None] = mapped_column(EnumText(RunStatus))
    task: Mapped[AgentTask] = mapped_column(ContractJSONB(AgentTask))
    result: Mapped[AgentRunResult | None] = mapped_column(ContractJSONB(AGENT_RUN_RESULT))
    tokens: Mapped[int]
    tool_calls: Mapped[int]
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]


class ToolCallRow(Base):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.run_id"), index=True)
    intent: Mapped[ToolIntent] = mapped_column(ContractJSONB(ToolIntent))
    policy_decision: Mapped[PolicyDecision] = mapped_column(EnumText(PolicyDecision))
    deny_reason: Mapped[str | None]
    status: Mapped[ToolStatus] = mapped_column(EnumText(ToolStatus))
    evidence_id: Mapped[str | None]
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = _created_at()


class EvidenceRow(Base):
    """A stored `EvidenceRef` (contracts.md); the excerpt is emptied after 30 days (D-08)."""

    __tablename__ = "evidence"

    evidence_id: Mapped[str] = mapped_column(primary_key=True)
    source: Mapped[EvidenceSource] = mapped_column(EnumText(EvidenceSource))
    query_text: Mapped[str]
    query_hash: Mapped[str]
    time_start: Mapped[datetime]
    time_end: Mapped[datetime]
    identifiers: Mapped[dict[str, str]] = mapped_column(ContractJSONB(dict[str, str]))
    # Masked.
    excerpt: Mapped[str]
    retrieved_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class UrgentEventRow(Base):
    __tablename__ = "urgent_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    evaluation_no: Mapped[int]
    rank: Mapped[int]
    event: Mapped[UrgentEvent] = mapped_column(ContractJSONB(UrgentEvent))


class RecommendationRow(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    evaluation_no: Mapped[int]
    recommendation: Mapped[Recommendation] = mapped_column(ContractJSONB(Recommendation))


# --- Yazma ve gönderim kayıtları ------------------------------------------------------------


class NoteWrittenRow(Base):
    __tablename__ = "notes_written"
    __table_args__ = (UniqueConstraint("offense_id", "run_marker"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    offense_id: Mapped[int] = mapped_column(BigInteger)
    evaluation_no: Mapped[int]
    # The `run:` marker on the first line of the note; guards against writing it twice.
    run_marker: Mapped[str]
    status: Mapped[NoteStatus] = mapped_column(EnumText(NoteStatus))
    error: Mapped[str | None]
    written_at: Mapped[datetime]


class NotificationRow(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[EmailKind] = mapped_column(EnumText(EmailKind))
    case_id: Mapped[str | None] = mapped_column(index=True)
    hunt_id: Mapped[str | None]
    group_id: Mapped[str | None]
    recipients: Mapped[list[str]] = mapped_column(ARRAY(Text))
    subject: Mapped[str]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    status: Mapped[NotificationStatus] = mapped_column(EnumText(NotificationStatus))
    sent_at: Mapped[datetime | None]


# --- Analiz Kataloğu ve kurum bağlamı -------------------------------------------------------


class CatalogRuleRow(Base):
    __tablename__ = "catalog_rules"

    rule_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # Synced from QRadar.
    rule_name: Mapped[str]
    # False: shown in the UI as "tanımsız".
    defined: Mapped[bool]
    mode: Mapped[CatalogMode] = mapped_column(
        EnumText(CatalogMode), server_default=text("'analyze'")
    )
    min_level: Mapped[Level | None] = mapped_column(EnumText(Level))
    has_automated_action: Mapped[bool]
    context_note: Mapped[str | None]
    # Suggested by the AI; not used until an operator accepts it.
    ai_draft_note: Mapped[str | None]
    updated_by: Mapped[str]
    updated_at: Mapped[datetime]


class CatalogLogSourceRow(Base):
    __tablename__ = "catalog_log_sources"

    log_source_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # `name` and `type_name` are synced from QRadar.
    name: Mapped[str]
    type_name: Mapped[str]
    defined: Mapped[bool]
    description: Mapped[str | None]
    owner: Mapped[str | None]
    criticality: Mapped[Level | None] = mapped_column(EnumText(Level))
    in_scope: Mapped[bool]
    context_note: Mapped[str | None]
    updated_by: Mapped[str]
    updated_at: Mapped[datetime]


class CriticalAssetRow(Base):
    __tablename__ = "critical_assets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[CriticalAssetKind] = mapped_column(EnumText(CriticalAssetKind))
    value: Mapped[str]
    # For example "SWIFT".
    label: Mapped[str]
    level: Mapped[Level] = mapped_column(EnumText(Level, allowed=(Level.HIGH, Level.CRITICAL)))


class NotificationRecipientRow(Base):
    __tablename__ = "notification_recipients"

    # Not in data-model.md: the primary key (list_name, email).
    list_name: Mapped[RecipientList] = mapped_column(EnumText(RecipientList), primary_key=True)
    # Allowed company domains only.
    email: Mapped[str] = mapped_column(primary_key=True)


class AllowedEmailDomainRow(Base):
    __tablename__ = "allowed_email_domains"

    domain: Mapped[str] = mapped_column(primary_key=True)


# --- Hunt -----------------------------------------------------------------------------------


class HuntPackRow(Base):
    __tablename__ = "hunt_packs"

    pack_id: Mapped[str] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[HuntPackStatus] = mapped_column(EnumText(HuntPackStatus))
    actor_id: Mapped[str | None]
    # The validated pack (hunt-pack.md); there is no contract model for it yet.
    content: Mapped[JsonValue] = _json()
    approved_by: Mapped[str | None]
    approved_at: Mapped[datetime | None]


class HuntRow(Base):
    __tablename__ = "hunts"

    hunt_id: Mapped[str] = mapped_column(primary_key=True)
    request: Mapped[HuntRequest] = mapped_column(ContractJSONB(HuntRequest))
    status: Mapped[HuntStatus] = mapped_column(EnumText(HuntStatus))
    outcome: Mapped[HuntOutcome | None] = mapped_column(EnumText(HuntOutcome))
    report: Mapped[HuntReport | None] = mapped_column(ContractJSONB(HuntReport))
    report_pdf_path: Mapped[str | None]
    schedule_id: Mapped[uuid.UUID | None]
    # Not in data-model.md: the types of these three, and `completed_at` being nullable.
    created_by: Mapped[str]
    created_at: Mapped[datetime] = _created_at()
    completed_at: Mapped[datetime | None]


class HuntSliceRow(Base):
    """One hypothesis x log source type x time slice; the coverage table is computed from it."""

    __tablename__ = "hunt_slices"

    hunt_id: Mapped[str] = mapped_column(ForeignKey("hunts.hunt_id"), primary_key=True)
    hypothesis_id: Mapped[str] = mapped_column(primary_key=True)
    log_source_type: Mapped[str] = mapped_column(primary_key=True)
    slice_start: Mapped[datetime] = mapped_column(primary_key=True)
    slice_end: Mapped[datetime]
    status: Mapped[SliceStatus] = mapped_column(EnumText(SliceStatus))
    query_hash: Mapped[str | None]
    evidence_id: Mapped[str | None]


class AnalyticResultRow(Base):
    __tablename__ = "analytic_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    hunt_id: Mapped[str] = mapped_column(ForeignKey("hunts.hunt_id"), index=True)
    slice_start: Mapped[datetime]
    analytic: Mapped[Analytic] = mapped_column(EnumText(Analytic))
    entity: Mapped[str | None]
    field: Mapped[str]
    value: Mapped[str]
    count: Mapped[int] = mapped_column(BigInteger)
    score: Mapped[float | None] = mapped_column(Double)
    evidence_id: Mapped[str | None]


class HuntFindingRow(Base):
    __tablename__ = "hunt_findings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    hunt_id: Mapped[str] = mapped_column(ForeignKey("hunts.hunt_id"), index=True)
    hypothesis_id: Mapped[str]
    outcome: Mapped[HuntOutcome] = mapped_column(EnumText(HuntOutcome))
    claims: Mapped[list[Claim]] = mapped_column(ContractJSONB(list[Claim]))
    # The `case-hunt-...` case it opened.
    case_id: Mapped[str | None]


class HuntScheduleRow(Base):
    __tablename__ = "hunt_schedules"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Always runs the latest approved version of the pack.
    pack_id: Mapped[str]
    cron: Mapped[str]
    # For example: every week, the last 7 days.
    window_days: Mapped[int]
    scope: Mapped[JsonValue] = _json()
    enabled: Mapped[bool]


# --- Bilgi düzlemi --------------------------------------------------------------------------
# data-model.md lists only the main columns of these tables. Not in data-model.md: column types
# other than the ones it gives, and the primary keys of actor_aliases and actor_techniques.


class ActorRow(Base):
    __tablename__ = "actors"

    actor_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    sector_relevance: Mapped[str]
    priority: Mapped[int]
    sources: Mapped[JsonValue] = _json()


class ActorAliasRow(Base):
    __tablename__ = "actor_aliases"

    actor_id: Mapped[str] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(primary_key=True)


class TechniqueRow(Base):
    __tablename__ = "techniques"

    # For example T1003.006.
    technique_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    tactics: Mapped[list[str]] = mapped_column(ARRAY(Text))


class ActorTechniqueRow(Base):
    __tablename__ = "actor_techniques"

    actor_id: Mapped[str] = mapped_column(primary_key=True)
    technique_id: Mapped[str] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(primary_key=True)
    first_reported: Mapped[datetime]


class IocRow(Base):
    __tablename__ = "iocs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str]
    value: Mapped[str]
    first_seen: Mapped[datetime]
    last_seen: Mapped[datetime]
    confidence: Mapped[Confidence] = mapped_column(EnumText(Confidence))
    tlp: Mapped[str]
    source: Mapped[str]
    actor_id: Mapped[str | None]


class CtiReportRow(Base):
    __tablename__ = "cti_reports"

    report_id: Mapped[str] = mapped_column(primary_key=True)
    source: Mapped[str]
    title: Mapped[str]
    published_at: Mapped[datetime]
    url: Mapped[str | None]


# --- Tuning ---------------------------------------------------------------------------------


class FpClusterRow(Base):
    __tablename__ = "fp_clusters"

    cluster_id: Mapped[str] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(BigInteger)
    pattern: Mapped[JsonValue] = _json()
    case_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    size: Mapped[int]
    created_at: Mapped[datetime] = _created_at()


class TuningProposalRow(Base):
    __tablename__ = "tuning_proposals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    cluster_id: Mapped[str] = mapped_column(ForeignKey("fp_clusters.cluster_id"), index=True)
    proposal: Mapped[TuningProposal] = mapped_column(ContractJSONB(TuningProposal))
    status: Mapped[TuningProposalStatus] = mapped_column(EnumText(TuningProposalStatus))
    # Not in data-model.md: nullable, because an open proposal has no decision yet.
    decided_by: Mapped[str | None]
    decided_at: Mapped[datetime | None]
    comment: Mapped[str | None]


# --- Kullanıcılar ve audit ------------------------------------------------------------------


class UserRow(Base):
    __tablename__ = "users"

    # OIDC `sub`.
    subject: Mapped[str] = mapped_column(primary_key=True)
    display_name: Mapped[str]
    # `operator`, `hunter`, `admin`.
    roles: Mapped[list[str]] = mapped_column(ARRAY(Text))


class AuditLogRow(Base):
    """Append-only: a trigger rejects UPDATE, DELETE and TRUNCATE (migration 0001)."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_object_type_object_id", "object_type", "object_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = _created_at()
    actor_kind: Mapped[ActorKind] = mapped_column(EnumText(ActorKind))
    actor_id: Mapped[str]
    # For example `catalog.rule.update`, `note.write`, `email.send`, `hunt.start`.
    action: Mapped[str]
    object_type: Mapped[str]
    object_id: Mapped[str]
    details: Mapped[dict[str, JsonValue]] = mapped_column(ContractJSONB(dict[str, JsonValue]))
