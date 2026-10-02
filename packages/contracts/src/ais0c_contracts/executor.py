"""The "Executor girdileri" models of docs/impl/contracts.md."""

from typing import Annotated

from pydantic import Field, StringConstraints

from ais0c_contracts.common import ContractModel, DataGap, UrgentEvent
from ais0c_contracts.enums import ActionType, CaseVerdict, Confidence, EmailKind, Level


class NoteContent(ContractModel):
    """Structured content of a QRadar note (architecture §9); the executor fills a fixed
    template with it."""

    offense_id: int
    evaluation_no: int
    # The `run:` marker on the first line of the note.
    run_marker: str
    verdict: CaseVerdict
    confidence: Confidence
    notify_level: Level
    summary_tr: Annotated[str, StringConstraints(max_length=400)]
    # Only the identifiers and `reason` of each event go into the note.
    urgent_events: Annotated[list[UrgentEvent], Field(max_length=5)]
    recommended_actions: list[ActionType]
    data_gaps: list[DataGap]
    case_url: str
    # Set for an offense added to a group; the short note format is used.
    group_id: str | None = None


class EmailMessage(ContractModel):
    kind: EmailKind
    # The executor rejects any address outside the allowed company domains.
    recipients: list[str]
    subject: Annotated[str, StringConstraints(max_length=150)]
    template_id: str
    # Template fields.
    fields: dict[str, str]
    # Storage references (hunt PDF).
    attachments: list[str]
    idempotency_key: str
