"""T-045: the executor's activities in the case flow (criteria 1, 5, 7 and 8).

The Temporal server, the workflows and the activities are real: the case side runs as in the
other worker tests (`worker_support.running_platform`), and the `soc-executor` queue runs the
real `write_offense_note` and `send_email` activities in their own worker (criterion 1).
QRadar's notes and the SMTP relay are stand-ins: the notes gateway keeps the notes in memory
(the T-019 tests' fake) and the relay records what it took. The database is real.

The kill switch decides what leaves the platform (criterion 7): with no `writes_enabled` row it
is off, the platform is in shadow mode and the records say `disabled` while the analysis goes
on; switched on, the same case's next evaluation writes its note and sends its e-mail. A failure
a retry may get past is retried; a refusal a retry would get again is recorded once (criterion
8). IPs are from the RFC 5737 ranges.
"""

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from pydantic import JsonValue
from sqlalchemy import insert, select
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from worker_support import (
    IntakeCheckpoint,
    Platform,
    TriageModel,
    eventually,
    offense,
    running_platform,
)

from ais0c_activities import (
    CaseSettings,
    EmailActivities,
    NoteActivities,
    NoteToolset,
    SessionFactory,
)
from ais0c_agents import GatewayClient, GatewayError
from ais0c_contracts import (
    CaseVerdict,
    EmailMessage,
    Level,
    ToolCoverage,
    ToolIntent,
    ToolResult,
    ToolStatus,
)
from ais0c_executor.email import EmailTransportError, SendReceipt
from ais0c_executor.note import note_run_marker
from ais0c_storage import ActorKind, NoteStatus, NotificationStatus, PlatformFlag
from ais0c_storage.enums import CaseStatus
from ais0c_storage.models import (
    AllowedEmailDomainRow,
    CaseRow,
    NoteWrittenRow,
    NotificationRecipientRow,
    NotificationRow,
)
from ais0c_storage.repositories import set_platform_flag
from ais0c_workflows.names import EXECUTOR_TASK_QUEUE
from ais0c_workflows.notify import run_marker

pytestmark = pytest.mark.anyio

OFFENSE_ID = 60
CASE_ID = f"case-{OFFENSE_ID}"
DESCRIPTION = "Excessive Firewall Accepts From Single Source"
OPERATOR = "soc-operator@example.com"

# The note profile as the gateway lists it (`GET /v1/tools`): a write and a read tool.
TOOLSET = NoteToolset.model_validate(
    {
        "name": "qradar-note-write",
        "connector": "qradar",
        "tools": [
            {
                "id": "add_offense_note",
                "description": "Add a note to a QRadar offense.",
                "schema_version": "a1b2c3d4e5f60718",
                "cost_class": "low",
                "risk": "write",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "id": "get_offense_notes",
                "description": "Read one page of an offense's notes.",
                "schema_version": "0f1e2d3c4b5a6978",
                "cost_class": "low",
                "risk": "read",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        ],
    }
)
_LIKE = re.compile(r'note_text LIKE "%(?P<text>[^"%]+)%"')


class NotesGateway(GatewayClient):
    """QRadar's notes behind the gateway, in memory: what the note activity reads and writes.

    `add_failures` answer the next `add_offense_note` calls first: an exception is raised (the
    gateway unreachable), a ToolResult is returned (a refusal)."""

    def __init__(self) -> None:
        self.notes: dict[int, list[str]] = {}
        self.intents: list[ToolIntent] = []
        self.add_failures: list[Exception | ToolResult] = []

    async def call(self, intent: ToolIntent) -> ToolResult:
        self.intents.append(intent)
        arguments = intent.arguments
        offense_id = arguments["offense_id"]
        assert isinstance(offense_id, int)
        if intent.tool_id == "get_offense_notes":
            found = _LIKE.fullmatch(str(arguments["filter"]))
            assert found is not None
            hits = [note for note in self.notes.get(offense_id, []) if found["text"] in note]
            return _ok(*({"id": n, "note_text": text} for n, text in enumerate(hits)))
        assert intent.tool_id == "add_offense_note"
        if self.add_failures:
            failure = self.add_failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        text = arguments["note_text"]
        assert isinstance(text, str)
        self.notes.setdefault(offense_id, []).append(text)
        return _ok({"id": 99, "note_text": text})

    def texts(self, offense_id: int) -> list[str]:
        return list(self.notes.get(offense_id, []))

    def adds(self) -> int:
        return sum(intent.tool_id == "add_offense_note" for intent in self.intents)


def _ok(*rows: dict[str, JsonValue]) -> ToolResult:
    return ToolResult(
        status=ToolStatus.OK,
        evidence_id="ev_01JBEXECUTORFLOW01",
        data=list(rows),
        truncated=False,
        coverage=ToolCoverage(complete=True, gaps=[]),
    )


class Relay:
    """Stands in for the SMTP relay: keeps the e-mails it took, with their bodies; `failures`
    are raised first."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.bodies: list[str] = []
        self.failures: list[Exception] = []

    def connect(self) -> "Relay":
        return self

    async def __aenter__(self) -> "Relay":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send(self, message: EmailMessage, body: str) -> SendReceipt:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(message)
        self.bodies.append(body)
        return SendReceipt(message_id=f"<relay.{len(self.sent)}@example.com>")


@pytest.fixture
async def routes(sessions: SessionFactory) -> None:
    """An operator in an allowed domain. Migration 0007 routes the case alerts of both levels
    to the `operators` group (D-41)."""
    async with sessions.begin() as session:
        await session.execute(insert(AllowedEmailDomainRow), [{"domain": "example.com"}])
        await session.execute(
            insert(NotificationRecipientRow), [{"list_name": "operators", "email": OPERATOR}]
        )


@asynccontextmanager
async def running_executor(
    env: WorkflowEnvironment, sessions: SessionFactory, notes: NotesGateway, relay: Relay
) -> AsyncIterator[None]:
    """The executor worker of the `soc-executor` queue, as `python -m ais0c_worker executor`
    runs it (criterion 1): the real activities over a stand-in gateway and relay."""
    note = NoteActivities(sessions=sessions, gateway=notes, toolset=TOOLSET)
    email = EmailActivities(sessions=sessions, transport=relay)
    async with Worker(
        env.client,
        task_queue=EXECUTOR_TASK_QUEUE,
        workflows=[],
        activities=[note.write_offense_note, email.send_email],
    ):
        yield


async def writes(sessions: SessionFactory, enabled: bool) -> None:
    async with sessions.begin() as session:
        await set_platform_flag(
            session,
            PlatformFlag.WRITES_ENABLED,
            enabled=enabled,
            reason="T-045 test",
            actor_kind=ActorKind.USER,
            actor_id="admin01",
        )


async def note_row(
    sessions: SessionFactory, offense_id: int, marker: str, *, status: NoteStatus
) -> NoteWrittenRow:
    """The `notes_written` row of one note of the offense, once it holds `status`."""

    async def found() -> NoteWrittenRow | None:
        async with sessions() as session:
            row = await session.scalar(
                select(NoteWrittenRow).where(
                    NoteWrittenRow.offense_id == offense_id,
                    NoteWrittenRow.run_marker == marker,
                )
            )
            return row if row is not None and row.status is status else None

    return await eventually(found, seconds=30)


async def notifications_of(sessions: SessionFactory, case_id: str) -> list[NotificationRow]:
    async with sessions() as session:
        rows = await session.scalars(
            select(NotificationRow)
            .where(NotificationRow.case_id == case_id)
            .order_by(NotificationRow.idempotency_key)
        )
        return list(rows)


async def sent_email(
    sessions: SessionFactory, case_id: str, evaluation_no: int
) -> NotificationRow | None:
    for row in await notifications_of(sessions, case_id):
        if row.status is NotificationStatus.SENT and row.idempotency_key.endswith(
            f":{evaluation_no}"
        ):
            return row
    return None


async def admit(platform: Platform, env: WorkflowEnvironment) -> tuple[IntakeCheckpoint, datetime]:
    """Go live, then admit offense OFFENSE_ID, started after go-live (D-26); returns the
    intake's checkpoint and the offense's start."""
    first = await platform.run_intake()
    await env.sleep(timedelta(minutes=10))
    start = await env.get_current_time() - timedelta(minutes=4)
    platform.source.put(offense(OFFENSE_ID, start=start))
    return await platform.run_intake(first), start


async def reevaluate(
    platform: Platform,
    env: WorkflowEnvironment,
    checkpoint: IntakeCheckpoint,
    start: datetime,
    destination: str,
) -> IntakeCheckpoint:
    """Re-evaluate the case at once: an update with a destination it never had (D-31)."""
    now = await env.get_current_time()
    platform.source.put(
        offense(
            OFFENSE_ID,
            start=start,
            updated=now,
            destination_ips=["198.51.100.15", destination],
        )
    )
    return await platform.run_intake(checkpoint)


def decided(evaluation_no: int = 1) -> Callable[[CaseRow], bool]:
    """The case has decided evaluation `evaluation_no`."""

    def check(row: CaseRow) -> bool:
        return row.evaluation_no == evaluation_no and row.status is CaseStatus.DECIDED

    return check


# --- criterion 7: shadow mode --------------------------------------------------------------------


async def test_with_writes_off_nothing_leaves_and_the_analysis_goes_on(
    env: WorkflowEnvironment, sessions: SessionFactory, routes: None
) -> None:
    notes = NotesGateway()
    relay = Relay()
    async with running_platform(
        env,
        sessions,
        settings=CaseSettings(case_url_base="https://ais0c.example.com/cases"),
        model=TriageModel(ai_level=Level.HIGH),
        executor_stub=False,
    ) as platform:
        async with running_executor(env, sessions, notes, relay):
            await admit(platform, env)
            # The analysis is recorded as with writes on.
            case = await platform.case_when(CASE_ID, decided())
            assert (case.verdict, case.notify_level) == (CaseVerdict.SUSPICIOUS, Level.HIGH)
            assert case.report is not None

            # The records say `disabled`, and the stand-ins got nothing (T-23, T-37).
            marker = run_marker(CASE_ID, 1, "evaluation")
            row = await note_row(sessions, OFFENSE_ID, marker, status=NoteStatus.DISABLED)

            async def disabled_email() -> NotificationRow | None:
                rows = await notifications_of(sessions, CASE_ID)
                return rows[0] if rows else None

            email = await eventually(disabled_email, seconds=30)

    assert row.error is None
    # Shadow mode does not even read QRadar's notes.
    assert notes.intents == []
    assert (email.status, email.level, email.error) == (
        NotificationStatus.DISABLED,
        Level.HIGH,
        None,
    )
    assert len(await notifications_of(sessions, CASE_ID)) == 1
    assert relay.sent == []


# --- criterion 7: the switch turned on -----------------------------------------------------------


async def test_once_writes_are_on_the_next_evaluation_of_the_case_writes(
    env: WorkflowEnvironment, sessions: SessionFactory, routes: None
) -> None:
    notes = NotesGateway()
    relay = Relay()
    async with running_platform(
        env,
        sessions,
        settings=CaseSettings(case_url_base="https://ais0c.example.com/cases"),
        model=TriageModel(ai_level=Level.HIGH),
        executor_stub=False,
    ) as platform:
        async with running_executor(env, sessions, notes, relay):
            checkpoint, start = await admit(platform, env)
            await platform.case_when(CASE_ID, decided())
            disabled = run_marker(CASE_ID, 1, "evaluation")
            await note_row(sessions, OFFENSE_ID, disabled, status=NoteStatus.DISABLED)

            await writes(sessions, True)
            checkpoint = await reevaluate(platform, env, checkpoint, start, "198.51.100.99")
            await platform.case_when(CASE_ID, decided(2))

            marker = run_marker(CASE_ID, 2, "evaluation")
            await note_row(sessions, OFFENSE_ID, marker, status=NoteStatus.WRITTEN)
            # The note itself is in QRadar, its marker on the first line: another one than the
            # disabled evaluation's.
            [text] = notes.texts(OFFENSE_ID)
            assert note_run_marker(text) == marker
            assert marker != disabled
            # The e-mail went out to the routed group; evaluation 1's stays `disabled`.
            sent = await eventually(lambda: sent_email(sessions, CASE_ID, 2), seconds=30)
            assert [row.status for row in await notifications_of(sessions, CASE_ID)] == [
                NotificationStatus.DISABLED,
                NotificationStatus.SENT,
            ]
            assert sent.level is Level.HIGH
            assert sent.recipients == [OPERATOR]
            assert [message.recipients for message in relay.sent] == [[OPERATOR]]


# --- criterion 5: which evaluations are e-mailed -------------------------------------------------


async def test_an_e_mail_goes_out_only_when_the_level_rises_above_what_was_sent(
    env: WorkflowEnvironment, sessions: SessionFactory, routes: None
) -> None:
    """D-42 in the flow: high is sent once, high again is not, critical is."""
    notes = NotesGateway()
    relay = Relay()
    await writes(sessions, True)
    model = TriageModel(ai_level=Level.HIGH)
    async with running_platform(
        env,
        sessions,
        settings=CaseSettings(case_url_base="https://ais0c.example.com/cases"),
        model=model,
        executor_stub=False,
    ) as platform:
        async with running_executor(env, sessions, notes, relay):
            checkpoint, start = await admit(platform, env)
            await platform.case_when(CASE_ID, decided())
            await eventually(lambda: sent_email(sessions, CASE_ID, 1), seconds=30)
            assert len(relay.sent) == 1

            # High again: the executor's level rule holds it back (not needed, no record).
            checkpoint = await reevaluate(platform, env, checkpoint, start, "198.51.100.99")
            await platform.case_when(CASE_ID, decided(2))
            assert await sent_email(sessions, CASE_ID, 2) is None
            assert len(relay.sent) == 1
            assert len(await notifications_of(sessions, CASE_ID)) == 1

            # Critical: above every level sent, so it goes out. The e-mail's subject carries
            # the level tag and the offense's description (the executor cleans it).
            model.ai_level = Level.CRITICAL
            checkpoint = await reevaluate(platform, env, checkpoint, start, "198.51.100.7")
            await platform.case_when(CASE_ID, decided(3))
            await eventually(lambda: sent_email(sessions, CASE_ID, 3), seconds=30)
            assert len(relay.sent) == 2
            assert relay.sent[1].subject.startswith("[AI-SOC] KRİTİK · AI kararı:")
            assert f"Offense #{OFFENSE_ID}: {DESCRIPTION}" in relay.sent[1].subject


# --- criterion 8: the executor's failures --------------------------------------------------------


async def test_a_gateway_outage_is_retried_and_the_note_written_once(
    env: WorkflowEnvironment, sessions: SessionFactory, routes: None
) -> None:
    notes = NotesGateway()
    notes.add_failures.append(GatewayError("the gateway is unreachable"))
    relay = Relay()
    relay.failures.append(EmailTransportError("421 try again later", retryable=True))
    await writes(sessions, True)
    async with running_platform(
        env, sessions, model=TriageModel(ai_level=Level.HIGH), executor_stub=False
    ) as platform:
        async with running_executor(env, sessions, notes, relay):
            await admit(platform, env)
            await platform.case_when(CASE_ID, decided())

            async def first_attempts_failed() -> bool | None:
                return True if not (notes.add_failures or relay.failures) else None

            await eventually(first_attempts_failed, seconds=30)
            # Past the retries' backoff.
            await env.sleep(timedelta(minutes=1))
            marker = run_marker(CASE_ID, 1, "evaluation")
            await note_row(sessions, OFFENSE_ID, marker, status=NoteStatus.WRITTEN)
            await eventually(lambda: sent_email(sessions, CASE_ID, 1), seconds=30)

    # The failed attempt wrote nothing; the retry wrote the note once.
    assert notes.adds() == 2
    [text] = notes.texts(OFFENSE_ID)
    assert note_run_marker(text) == marker
    assert len(relay.sent) == 1


async def test_a_refusal_is_recorded_once_and_the_decision_stands(
    env: WorkflowEnvironment, sessions: SessionFactory, routes: None
) -> None:
    """A refusal a retry would get again is not retried: the gateway refuses the note, the
    relay refuses the e-mail with a 5xx reply; both are recorded as failed."""
    notes = NotesGateway()
    notes.add_failures.append(
        ToolResult(
            status=ToolStatus.DENIED,
            deny_reason="caller_not_allowed: the caller may not use qradar-note-write",
            data=[],
            truncated=False,
            coverage=ToolCoverage(complete=True, gaps=[]),
        )
    )
    relay = Relay()
    relay.failures.append(EmailTransportError("550 relaying denied", retryable=False))
    await writes(sessions, True)
    async with running_platform(
        env, sessions, model=TriageModel(ai_level=Level.HIGH), executor_stub=False
    ) as platform:
        async with running_executor(env, sessions, notes, relay):
            await admit(platform, env)
            case = await platform.case_when(CASE_ID, decided())
            marker = run_marker(CASE_ID, 1, "evaluation")
            row = await note_row(sessions, OFFENSE_ID, marker, status=NoteStatus.FAILED)

            async def failed_email() -> NotificationRow | None:
                rows = await notifications_of(sessions, CASE_ID)
                return next((r for r in rows if r.status is NotificationStatus.FAILED), None)

            email = await eventually(failed_email, seconds=30)
            await env.sleep(timedelta(minutes=10))

    assert "caller_not_allowed" in (row.error or "")
    assert "550" in (email.error or "")
    # Neither was tried again, and the decision is the one recorded.
    assert notes.adds() == 1
    assert notes.texts(OFFENSE_ID) == []
    assert relay.sent == []
    assert (case.status, case.evaluation_no) == (CaseStatus.DECIDED, 1)
