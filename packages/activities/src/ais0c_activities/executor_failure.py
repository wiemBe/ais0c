"""The record of an executor call the case gave up on (T-032, decision T-59 (7)).

A case starts its note and e-mail calls on the `soc-executor` queue and does not wait for them
(T-045). A call that the retries did not get past in the hour it was given, because the executor
worker was away or failing the whole time, is given up. `record_executor_failure` runs on the
case queue and writes what the executor would have: a `notes_written` row for a note, a
`notifications` row for an e-mail, in status `failed` with the error `executor_unavailable`. The
write-failure alarm counts such rows (`check_write_failures`) and the analyst UI shows them.

A row that is already there is left as it is: the executor made an attempt of its own, and its
record says more than this one. The e-mail row carries no recipients and a fixed subject; the
executor, should it come back, overwrites it when it sends the e-mail under the same key.

`AbandonedCall` mirrors `ais0c_workflows.notify.AbandonedCall` field for field (the workflows
package may not import this one); a worker test keeps the two together.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator
from temporalio import activity

from ais0c_activities.db import SessionFactory
from ais0c_activities.gateway import utc_now
from ais0c_activities.names import RECORD_EXECUTOR_FAILURE
from ais0c_contracts import EmailKind, EmailMessage, Level
from ais0c_storage import DuplicateError, NotificationStatus
from ais0c_storage.repositories import record_note_failure, record_notification

EXECUTOR_UNAVAILABLE: Final = "executor_unavailable"
# What the row's e-mail says in place of an e-mail that was never built.
SUBJECT: Final = "[AI-SOC] E-posta gönderilemedi: yürütücü kullanılamıyor"

_log = logging.getLogger(__name__)


class AbandonedCall(BaseModel):
    """What the case knows of the call it gave up: enough to write the executor's record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["note", "email"]
    case_id: str
    evaluation_no: int
    offense_id: int | None = None
    """A note's offense."""
    run_marker: str | None = None
    """A note's `run:` marker."""
    email_kind: Literal["case_alert", "group_alert"] | None = None
    level: Level | None = None
    """An e-mail's notify level."""
    group_id: str | None = None
    """A group alert's group."""
    idempotency_key: str | None = None
    """An e-mail's key, the one the executor would have used."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.kind == "note":
            complete = self.offense_id is not None and self.run_marker is not None
        else:
            complete = (
                self.email_kind is not None
                and self.level is not None
                and self.idempotency_key is not None
                and (self.email_kind == "group_alert") == (self.group_id is not None)
            )
        if not complete:
            raise ValueError(f"an abandoned {self.kind} call is missing what the record needs")
        return self


class ExecutorFailureActivities:
    """`record_executor_failure`, the one activity of this module."""

    def __init__(
        self, *, sessions: SessionFactory, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    def activities(self) -> list[Callable[..., object]]:
        return [self.record_executor_failure]

    @activity.defn(name=RECORD_EXECUTOR_FAILURE)
    async def record_executor_failure(self, call: AbandonedCall) -> bool:
        """Record the call as `failed` with the error `executor_unavailable`; True if a row was
        written, False if one was there already (it is left as it is)."""
        try:
            async with self._sessions.begin() as session:
                if (
                    call.kind == "note"
                    and call.offense_id is not None
                    and call.run_marker is not None
                ):
                    await record_note_failure(
                        session,
                        case_id=call.case_id,
                        offense_id=call.offense_id,
                        evaluation_no=call.evaluation_no,
                        run_marker=call.run_marker,
                        written_at=self._clock(),
                        error=EXECUTOR_UNAVAILABLE,
                    )
                elif (
                    call.kind == "email"
                    and call.email_kind is not None
                    and call.idempotency_key is not None
                ):
                    message = EmailMessage(
                        kind=EmailKind(call.email_kind),
                        recipients=[],
                        subject=SUBJECT,
                        template_id=call.email_kind,
                        fields={},
                        attachments=[],
                        idempotency_key=call.idempotency_key,
                    )
                    await record_notification(
                        session,
                        message,
                        status=NotificationStatus.FAILED,
                        level=call.level,
                        case_id=call.case_id,
                        group_id=call.group_id,
                        error=EXECUTOR_UNAVAILABLE,
                    )
                else:  # the model's validator refuses such a call; this keeps the types honest
                    raise ValueError("an abandoned call is missing what the record needs")
        except DuplicateError:
            return False
        _log.warning(
            "the executor was unavailable: %s of case %s recorded as failed",
            call.kind,
            call.case_id,
        )
        return True
