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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from ais0c_api.auth import Role
from ais0c_contracts import (
    AttackTechnique,
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
    ChangeObjectType,
    ChangeRejectReason,
    ChangeStatus,
    CriticalAssetKind,
    FullAnalysisReason,
    GroupStatus,
    GroupValueKind,
    NoteStatus,
    NotificationStatus,
    OffenseStatus,
    PlatformFlag,
    PolicyDecision,
    QAStatus,
)


class ApiModel(BaseModel):
    """Base of every model here: a response never carries a field the schema does not name, and
    a request body with a field the endpoint does not know is a 422.

    The second matters for the `PUT`s, which replace what they are given: a misspelt
    `contex_note` must not be dropped silently and clear the note.
    """

    model_config = ConfigDict(extra="forbid")


class Page[T](ApiModel):
    """A cursor page: `next_cursor` is null on the last page."""

    items: list[T]
    next_cursor: str | None = None


class FieldError(ApiModel):
    field: str
    message: str


class Problem(BaseModel):
    """The shape of an RFC 9457 answer, for the UI's own error handling.

    Not a response model of any route: `ais0c_api.openapi` makes it every operation's `default`
    response in the schema. `errors`, `domains` and `list_names` appear only on the problems that
    carry them.
    """

    type: str = "about:blank"
    # A machine-readable code, such as `qa.already_resolved`.
    title: str
    status: int
    detail: str | None = None
    # A body or query that does not validate: the field paths, never the rejected values.
    errors: list[FieldError] | None = None
    # `notification_recipients.domain_not_allowed`: the domains outside the allowlist.
    domains: list[str] | None = None
    # `notification_routes.unknown_group`: the groups that have no members.
    list_names: list[str] | None = None
    # `change.pending_exists`: the request that is already waiting for the object.
    change_id: UUID | None = None


# --- session and health -----------------------------------------------------------------------


class Me(ApiModel):
    """`GET /me`: who the session is and what it may do."""

    subject: str
    display_name: str
    # Most privileged first.
    roles: list[Role]


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
    # The offense's QRadar description, as the intake recorded it; None for a hunt or a group case.
    offense_description: str | None = None
    hunt_id: str | None = None
    group_id: str | None = None
    # The offense's rule IDs; empty for a hunt or a group case.
    rule_ids: list[int] = Field(default_factory=list)
    # True when the current evaluation has no decision and its SLA deadline has passed.
    sla_overdue: bool = False
    # The offense's page in the QRadar console; None without an offense or without a configured
    # template (T-029).
    qradar_offense_url: str | None = None


class EvidenceItem(ApiModel):
    """One evidence row of the case detail: where to find the event at the source."""

    evidence_id: str
    source: EvidenceSource
    # The gateway tool that issued the query; empty when no call carries the ID.
    tool_id: str = ""
    # True when the report, its urgent events and recommendations or Verification cite it; the
    # rest is what the evaluation's agents collected and did not cite.
    cited: bool
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
    # The evaluation the run belongs to, read from its run ID (decision T-29); None for a run the
    # platform's own code made, such as the executor's note run.
    evaluation_no: int | None = None
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
    # The evaluation the report, the Verification and the evidence come from: the case's own
    # `evaluation_no`, or an earlier one while a re-evaluation runs or after it ended without a
    # decision (the case keeps the last decision it has).
    evaluation_no: int
    # None for a case that has not been decided, or was decided without a report.
    report: CaseReport | None = None
    urgent_events: list[UrgentEvent] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    # The last Verification of that evaluation that gave a result; None when none did.
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
    # Why the offense got a full analysis instead of joining the group; None for an offense the
    # group took, a skipped one and one recorded before the column existed.
    full_analysis_reason: FullAnalysisReason | None = None
    qradar_offense_url: str | None = None


class GroupValueCount(ApiModel):
    """One value of a group and the number of its offenses that carry it."""

    value: str
    offenses: int


class GroupValueKindSummary(ApiModel):
    """One kind of value (source IP, user, log source, ...) over the group's offenses."""

    kind: GroupValueKind
    distinct: int
    top: list[GroupValueCount] = Field(default_factory=list)


class GroupDigest(ApiModel):
    """The deterministic summary of a group, counted from its rows (T-029 criterion 9; no model)."""

    offense_count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    rule_ids: list[int] = Field(default_factory=list)
    values: list[GroupValueKindSummary] = Field(default_factory=list)


class GroupDetail(ApiModel):
    """`GET /groups/{group_id}`: the group, its offenses and its evaluation's decision."""

    group: GroupSummary
    offenses: list[GroupOffense] = Field(default_factory=list)
    summary: GroupDigest
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


class CatalogRuleUpdate(ApiModel):
    """`PUT /catalog/rules/{rule_id}` (admin). The rule becomes defined."""

    mode: CatalogMode
    min_level: Level | None = None
    has_automated_action: bool
    context_note: Annotated[str, StringConstraints(max_length=600)] | None = None
    # `T1003` or `T1003.006` (contracts `AttackTechnique`), the form the skill router reads.
    attack_techniques: list[AttackTechnique] = Field(default_factory=list, max_length=32)


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


class CatalogLogSourceUpdate(ApiModel):
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


class CriticalAssetAdd(ApiModel):
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


class RecipientGroupUpdate(ApiModel):
    """`PUT /notification-recipients/{list_name}` (admin): the group's whole membership."""

    emails: list[Annotated[str, StringConstraints(max_length=254)]] = Field(max_length=200)


# The alert kinds whose route names a level; a hunt report has none (`level` NULL, D-41).
LEVELLED_KINDS = frozenset({EmailKind.CASE_ALERT, EmailKind.GROUP_ALERT})


class NotificationRoute(ApiModel):
    """One `notification_routes` row: an alert kind and level go to these groups."""

    kind: EmailKind
    level: Level | None = None
    list_name: str


class NotificationRouteEntry(NotificationRoute):
    """One route of `PUT /notification-routes`.

    A case or group alert is routed by its level, a hunt report without one. A route that breaks
    this would never match an e-mail the executor sends, so it is a 422 rather than a dead row.
    The check is on the request only: a row already stored is shown as it is, so an admin can
    see and replace it.
    """

    @model_validator(mode="after")
    def _level_fits_the_kind(self) -> "NotificationRouteEntry":
        if (self.kind in LEVELLED_KINDS) != (self.level is not None):
            raise ValueError("a case or group alert needs a level and a hunt report has none")
        return self


class NotificationRoutesUpdate(ApiModel):
    """`PUT /notification-routes` (admin): the whole table, every kind and level included."""

    routes: list[NotificationRouteEntry] = Field(max_length=200)


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
    # Closed in QRadar before the AI decided.
    closed: int


class SLAMetrics(ApiModel):
    """`GET /metrics/sla`: how the cases with a deadline in the range met it."""

    # `from` on the wire; a Python name cannot be a keyword.
    from_: datetime = Field(serialization_alias="from")
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


class PlatformFlagUpdate(ApiModel):
    """`PUT /admin/platform-flags/{name}` (admin). `reason` is required and may not be blank."""

    enabled: bool
    reason: Annotated[str, StringConstraints(max_length=500)]


# --- double control ----------------------------------------------------------------------------


class ChangeAccepted(ApiModel):
    """The answer (202) of a change that waits for a second admin (D-36, T-77)."""

    change_id: UUID


class ChangeItem(ApiModel):
    """One `change_approvals` row: a change asked for, and what became of it.

    `change` is `{ action, before, after }` (a flag change also has `reason`): the object's values
    when the request was made and the ones asked for. `decided_by` is set only by a second admin's
    approval or rejection; a withdrawn or stale request has none.
    """

    id: UUID
    object_type: ChangeObjectType
    object_id: str
    object_version: str
    change: dict[str, JsonValue]
    requested_by: str
    requested_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    status: ChangeStatus
    reason: ChangeRejectReason | None = None
    comment: str | None = None


class ChangeReject(ApiModel):
    """`POST /changes/{id}/reject`."""

    comment: Annotated[str, StringConstraints(max_length=500)] | None = None
