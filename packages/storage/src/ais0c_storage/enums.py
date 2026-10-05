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


class ActorKind(StrEnum):
    """`audit_log.actor_kind`."""

    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"
