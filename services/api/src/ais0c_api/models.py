"""Response and request models of the analyst API (T-028).

These models live here, not in `packages/contracts`: they are the UI's wire format, not the
platform's internal schemas (api.md). `services/api/openapi.json` is generated from them and
T-029 derives the UI's TypeScript types from that file.

Everything is read-only from the platform's tables except the few write endpoints, which take
their bodies from the contracts (`OperatorFeedback`) or from the small models at the end of this
module.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ais0c_contracts import (
    CaseReport,
    CaseSource,
    CaseVerdict,
    CatalogMode,
    Confidence,
    DataGap,
    EmailKind,
    EvidenceSource,
    FeedbackReason,
    Level,
    QAReason,
    Recommendation,
    RunStatus,
    ToolStatus,
    UrgentEvent,
    VerificationResult,
)
from ais0c_storage.enums import (
    CaseStatus,
    CriticalAssetKind,
    GroupStatus,
    NoteStatus,
    NotificationStatus,
    OffenseStatus,
    PlatformFlag,
    PolicyDecision,
    QAStatus,
)


class ApiModel(BaseModel):
    """Base of every response model: unknown fields never come back."""

    model_config = ConfigDict(extra="forbid")


class Page[T](ApiModel):
    """A cursor page: `next_cursor` is null on the last page."""

    items: list[T]
    next_cursor: str | None = None


class FieldError(ApiModel):
    field: str
    message: str


class Problem(BaseModel):
    """The shape of an RFC 9457 answer, for the UI's own error handling."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    errors: list[FieldError] | None = None


# --- session and health -----------------------------------------------------------------------


class Me(ApiModel):
    """`GET /me`: who the session is and what it may do."""

    subject: str
    display_name: str
    roles: list[str]


class Health(ApiModel):
    """`GET /health`: liveness only. No version, no database detail, no settings."""

    status: Literal["ok"] = "ok"


# --- cases ------------------------------------------------------------------------------------


class CaseSummary(ApiModel):
    """One row of the offense queue (architecture §24)."""

    case_id: str
    source: CaseSource
    status: CaseStatus
    verdict: CaseVerdict | None
    confidence: Confidence | None
    ai_level: Level | None
    floor_level: Level | None
    notify_level: Level | None
    evaluation_no: int
    sla_due_at: datetime
    decided_at: datetime | None
    created_at: datetime
    offense_id: int | None = None
    hunt_id: str | None = None
    group_id: str | None = None
    # The offense's rule IDs; empty for a hunt or a group case.
    rule_ids: list[int] = Field(default_factory=list)
    # True when the case is past its SLA deadline and has no decision yet.
    sla_overdue: bool = False


class EvidenceItem(ApiModel):
    """One evidence row of the case detail: where to find the event at the source."""

    evidence_id: str
    source: EvidenceSource
    # The gateway tool that issued the query; empty when no call carries the ID.
    tool_id: str = ""
    query_hash: str
    query_text: str
    identifiers: dict[str, str]
    time_start: datetime
    time_end: datetime
    retrieved_at: datetime


class NoteItem(ApiModel):
    """One `notes_written` row: what the executor did with the QRadar note."""

    offense_id: int
    evaluation_no: int
    run_marker: str
    status: NoteStatus
    error: str | None
    written_at: datetime


class NotificationItem(ApiModel):
    """One `notifications` row: the alert e-mail and what became of it."""

    kind: EmailKind
    level: Level | None
    recipients: list[str]
    subject: str
    status: NotificationStatus
    error: str | None
    sent_at: datetime | None


class AgentStep(ApiModel):
    """One agent run of a case's steps (architecture §24, "ajan adımlarının özeti")."""

    run_id: str
    agent_id: str
    agent_version: str
    prompt_version: str
    model_alias: str
    status: RunStatus | None
    tokens: int
    tool_call_count: int
    started_at: datetime
    ended_at: datetime | None
    # Seconds between start and end; None while the run is still going.
    duration_seconds: float | None = None
    error: str | None = None


class ToolCallItem(ApiModel):
    """One gateway call of an agent run: the policy decision and how long it took."""

    tool_id: str
    policy_decision: PolicyDecision
    status: ToolStatus
    deny_reason: str | None
    evidence_id: str | None
    latency_ms: int


class CaseStep(ApiModel):
    """An agent run with its calls, in the order they were recorded."""

    step: AgentStep
    tool_calls: list[ToolCallItem]


class CaseDetail(ApiModel):
    """`GET /cases/{case_id}`: the whole case, as architecture §24's detail screen shows it."""

    case: CaseSummary
    # None for a case that has not been decided, or was decided without a report.
    report: CaseReport | None = None
    urgent_events: list[UrgentEvent] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    # The last Verification of the current evaluation; None when it did not run.
    verification: VerificationResult | None = None
    data_gaps: list[DataGap] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    notes: list[NoteItem] = Field(default_factory=list)
    notifications: list[NotificationItem] = Field(default_factory=list)


class FeedbackAnswer(ApiModel):
    """`POST /cases/{case_id}/feedback`: the stored feedback, with who wrote it."""

    case_id: str
    user_subject: str
    verdict: CaseVerdict
    reason: FeedbackReason
    comment: str | None = None
    created_at: datetime


# --- QA queue ---------------------------------------------------------------------------------


class QAItemSummary(ApiModel):
    """One row of the QA queue."""

    id: UUID
    case_id: str
    evaluation_no: int
    reason: QAReason
    status: QAStatus
    resolved_by: str | None
    resolved_at: datetime | None
    # The case's own decision fields, so the queue row needs no second request.
    case_status: CaseStatus | None = None
    case_verdict: CaseVerdict | None = None
    case_notify_level: Level | None = None
    case_summary_tr: str | None = None


class QAResolveRequest(ApiModel):
    """`POST /qa/{id}/resolve`: the operator's verdict, reason and optional comment."""

    verdict: CaseVerdict
    reason: FeedbackReason
    comment: Annotated[str, StringConstraints(max_length=500)] | None = None


class QAResolveAnswer(ApiModel):
    """The resolved item and the feedback it wrote for the case."""

    item: QAItemSummary
    feedback: FeedbackAnswer


# --- groups -----------------------------------------------------------------------------------


class GroupSummary(ApiModel):
    """One row of the group list."""

    group_id: str
    rule_set_hash: str
    window_start: datetime
    window_end: datetime
    offense_count: int
    status: GroupStatus
    case_id: str | None = None


class GroupOffense(ApiModel):
    """One offense of a group."""

    offense_id: int
    description: str
    status: OffenseStatus
    case_id: str | None = None
    first_seen_at: datetime


class GroupDetail(ApiModel):
    """`GET /groups/{group_id}`: the group, its offenses and its evaluation's decision."""

    group: GroupSummary
    offenses: list[GroupOffense] = Field(default_factory=list)
    # The group's case decision, whatever it is; None while the group has no case.
    case_id: str | None = None
    case_status: CaseStatus | None = None
    verdict: CaseVerdict | None = None
    notify_level: Level | None = None
    report: CaseReport | None = None


# --- analysis catalog -------------------------------------------------------------------------


class CatalogRule(ApiModel):
    """One `catalog_rules` row, as the catalog screen shows it."""

    rule_id: int
    rule_name: str
    defined: bool
    mode: CatalogMode
    min_level: Level | None
    has_automated_action: bool
    context_note: str | None
    # The AI's draft; not used until an admin accepts it.
    ai_draft_note: str | None = None
    attack_techniques: list[str] = Field(default_factory=list)
    # Synced from QRadar: whether the rule is enabled there (T-37).
    qradar_enabled: bool
    # Set when QRadar no longer lists the rule; cleared when it lists it again.
    missing_since: datetime | None = None
    updated_by: str
    updated_at: datetime


class CatalogRuleUpdate(BaseModel):
    """`PUT /catalog/rules/{rule_id}` (admin). The rule becomes defined."""

    mode: CatalogMode
    min_level: Level | None = None
    has_automated_action: bool
    context_note: Annotated[str, StringConstraints(max_length=600)] | None = None
    attack_techniques: list[str] = Field(default_factory=list, max_length=32)


class CatalogLogSource(ApiModel):
    """One `catalog_log_sources` row."""

    log_source_id: int
    name: str
    type_name: str
    defined: bool
    description: str | None
    owner: str | None
    criticality: Level | None
    in_scope: bool
    context_note: str | None
    missing_since: datetime | None = None
    updated_by: str
    updated_at: datetime


class CatalogLogSourceUpdate(BaseModel):
    """`PUT /catalog/log-sources/{log_source_id}` (admin). The log source becomes defined."""

    description: Annotated[str, StringConstraints(max_length=300)] | None = None
    owner: Annotated[str, StringConstraints(max_length=120)] | None = None
    criticality: Level | None = None
    in_scope: bool
    context_note: Annotated[str, StringConstraints(max_length=600)] | None = None


class SyncAccepted(ApiModel):
    """`POST /catalog/sync`: the Schedule was triggered; the run itself is Temporal's."""

    schedule_id: str
    triggered: Literal[True] = True


# --- critical assets, recipients and routes ----------------------------------------------------


class CriticalAsset(ApiModel):
    """One `critical_assets` row."""

    id: UUID
    kind: CriticalAssetKind
    value: str
    label: str
    level: Level


class CriticalAssetAdd(BaseModel):
    """`POST /critical-assets` (admin). Storage normalizes and validates the value."""

    kind: CriticalAssetKind
    value: Annotated[str, StringConstraints(max_length=200)]
    label: Annotated[str, StringConstraints(max_length=300)]
    level: Level


class RecipientGroup(ApiModel):
    """One named recipient group and its members (D-41)."""

    list_name: str
    emails: list[str]


class RecipientsView(ApiModel):
    """`GET /notification-recipients`: the groups and the domain allowlist that guards them."""

    groups: list[RecipientGroup]
    allowed_domains: list[str]


class RecipientGroupUpdate(BaseModel):
    """`PUT /notification-recipients/{list_name}` (admin): the group's whole membership."""

    emails: list[str]


class NotificationRoute(BaseModel):
    """One `notification_routes` row: an alert kind and level go to these groups."""

    kind: EmailKind
    level: Level | None = None
    list_name: str


class NotificationRoutesUpdate(BaseModel):
    """`PUT /notification-routes` (admin): the whole table, every kind and level included."""

    routes: list[NotificationRoute]


# --- metrics and administration ----------------------------------------------------------------


class SlaRow(ApiModel):
    """One `floor_level` bucket of `GET /metrics/sla`; the numbers add up to `total`."""

    # `none` when the cases have no floor; api.md names the bucket, not a null.
    floor_level: Literal["none"] | Level
    total: int
    on_time: int
    late: int
    undecided: int
    running: int


class SLAMetrics(ApiModel):
    """`GET /metrics/sla`: how the cases with a deadline in the range met it."""

    from_: datetime
    to: datetime
    buckets: list[SlaRow]


class PlatformFlagState(ApiModel):
    """`GET /admin/platform-flags`: every known flag. A flag with no row is off."""

    name: PlatformFlag
    enabled: bool
    reason: str | None = None
    # The user who changed it last; empty for a flag that was never set.
    changed_by: str | None = None
    changed_at: datetime | None = None


class PlatformFlagUpdate(BaseModel):
    """`PUT /admin/platform-flags/{name}` (admin). `reason` is required and may not be blank."""

    enabled: bool
    reason: Annotated[str, StringConstraints(max_length=500)]
