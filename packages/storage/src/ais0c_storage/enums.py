"""Value sets of text columns defined in docs/impl/data-model.md.

Columns whose values come from a contract enum (`Level`, `CaseVerdict`, ...) use that enum from
`ais0c_contracts`; this module holds the sets that exist only in the data model.
"""

from enum import StrEnum


class OffenseStatus(StrEnum):
    """`offenses_seen.status`."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    GROUPED = "grouped"


class GroupStatus(StrEnum):
    """`offense_groups.status`."""

    OPEN = "open"
    STORM = "storm"
    CLOSED = "closed"


class FullAnalysisReason(StrEnum):
    """`offenses_seen.full_analysis_reason`: why an offense of a group got a full analysis
    (architecture §9; T-14, T-22, T-62). Not in data-model.md (T-027). Empty for an offense the
    group took, a skipped one and one recorded before the column existed.

    The hourly limit counts `limit` and `exempt`; `novelty` and `sample` have counters of their
    own, so they do not use up the limit.
    """

    # Within the group's hourly full analysis limit.
    LIMIT = "limit"
    # A critical asset (a privileged user included), an IOC or a high catalog floor: never
    # grouped, without a limit.
    EXEMPT = "exempt"
    # A log source or offense category the group had not seen, within its own hourly limit.
    NOVELTY = "novelty"
    # The group's hourly sample.
    SAMPLE = "sample"


class GroupValueKind(StrEnum):
    """`offense_group_values.kind`: the values a group keeps of its offenses (T-46, T-62).
    Not in data-model.md (T-027)."""

    SOURCE_IP = "source_ip"
    DESTINATION_IP = "destination_ip"
    USERNAME = "username"
    LOG_SOURCE = "log_source"
    CATEGORY = "category"


class CaseStatus(StrEnum):
    """`cases.status`."""

    RUNNING = "running"
    DECIDED = "decided"
    NO_AI_DECISION = "no_ai_decision"
    CLOSED = "closed"


class QAStatus(StrEnum):
    """`qa_items.status`."""

    OPEN = "open"
    RESOLVED = "resolved"


class PolicyDecision(StrEnum):
    """`tool_calls.policy_decision`."""

    ALLOW = "allow"
    DENY = "deny"


class NoteStatus(StrEnum):
    """`notes_written.status`.

    `disabled`: the kill switch was off (shadow mode, T-23), so the note was not written. It is
    not a failure; the error alarms do not count it (T-37).
    """

    WRITTEN = "written"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    DISABLED = "disabled"
    FAILED = "failed"


class NotificationStatus(StrEnum):
    """`notifications.status`.

    `disabled`: the kill switch was off (T-23), so the e-mail was not sent. Only `sent` counts
    as sent; a key recorded as `disabled`, `rejected` or `failed` is tried again (T-37).
    """

    SENT = "sent"
    REJECTED = "rejected"
    DISABLED = "disabled"
    FAILED = "failed"


class CriticalAssetKind(StrEnum):
    """`critical_assets.kind`."""

    IP = "ip"
    CIDR = "cidr"
    HOST = "host"
    USER = "user"


class HuntPackStatus(StrEnum):
    """`hunt_packs.status`."""

    DRAFT = "draft"
    APPROVED = "approved"


class HuntStatus(StrEnum):
    """`hunts.status`."""

    PLANNED = "planned"
    RUNNING = "running"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class SliceStatus(StrEnum):
    """`hunt_slices.status`."""

    PENDING = "pending"
    DONE = "done"
    NO_DATA = "no_data"
    NOT_PARSED = "not_parsed"
    NOT_VISIBLE = "not_visible"
    FAILED = "failed"


class Analytic(StrEnum):
    """`analytic_results.analytic`."""

    STACK_COUNT = "stack_count"
    RARITY = "rarity"
    FIRST_SEEN = "first_seen"
    BASELINE = "baseline"
    PERIODICITY = "periodicity"
    IOC_SWEEP = "ioc_sweep"


class TuningProposalStatus(StrEnum):
    """`tuning_proposals.status`."""

    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PlatformFlag(StrEnum):
    """`platform_flags.name`: the switches the platform knows (T-23)."""

    # The kill switch. While it is off, the executor writes nothing outside the platform: no
    # QRadar note, no e-mail. Shadow mode is this flag being off.
    WRITES_ENABLED = "writes_enabled"


class HealthAlarmKind(StrEnum):
    """`health_alarms.kind`: the platform's own health alarms (T-23, T-68)."""

    # QRadar has an offense update the platform has not seen, or QRadar cannot be reached.
    INTAKE_STOPPED = "intake_stopped"
    # A log source in the catalog's scope sends no events.
    LOG_SOURCE_SILENT = "log_source_silent"
    # Failed notes or e-mails in the last window went over the threshold.
    WRITE_FAILURES = "write_failures"
    # No worker listens on the `soc-executor` task queue.
    EXECUTOR_ABSENT = "executor_absent"


class HealthAlarmStatus(StrEnum):
    """`health_alarms.status`: at most one `open` alarm per kind and subject."""

    OPEN = "open"
    RESOLVED = "resolved"


class ChangeObjectType(StrEnum):
    """`change_approvals.object_type` (D-36, T-77). `skill`, `policy` and `hunt_pack` are files in
    the repository whose approval is a code review; they are listed because data-model.md lists
    them and no endpoint writes them."""

    CATALOG_RULE = "catalog_rule"
    CATALOG_LOG_SOURCE = "catalog_log_source"
    CRITICAL_ASSET = "critical_asset"
    # Only the opening of the kill switch (T-77); closing it is one step.
    PLATFORM_FLAG = "platform_flag"
    SKILL = "skill"
    POLICY = "policy"
    HUNT_PACK = "hunt_pack"


class ChangeStatus(StrEnum):
    """`change_approvals.status`."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ChangeRejectReason(StrEnum):
    """`change_approvals.reason`, set only on a `rejected` request."""

    REJECTED_BY_ADMIN = "rejected_by_admin"
    # The object changed after the request was made.
    STALE = "stale"
    # The requester took the request back.
    WITHDRAWN = "withdrawn"


class ActorKind(StrEnum):
    """`audit_log.actor_kind`."""

    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"
