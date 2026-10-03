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
    """`notes_written.status`."""

    WRITTEN = "written"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    FAILED = "failed"


class NotificationStatus(StrEnum):
    """`notifications.status`."""

    SENT = "sent"
    REJECTED = "rejected"
    FAILED = "failed"


class CriticalAssetKind(StrEnum):
    """`critical_assets.kind`."""

    IP = "ip"
    CIDR = "cidr"
    HOST = "host"
    USER = "user"


class RecipientList(StrEnum):
    """`notification_recipients.list_name`."""

    OPERATORS = "operators"
    HUNTERS = "hunters"
    ADMINS = "admins"


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


class ActorKind(StrEnum):
    """`audit_log.actor_kind`."""

    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"
