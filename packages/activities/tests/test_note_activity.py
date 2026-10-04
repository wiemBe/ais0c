"""The `write_offense_note` activity (T-019 criteria 3 and 6): the note goes through the
gateway's `qradar-note-write` profile, in a system run of `action-executor`, and a retried
attempt leaves one note in QRadar.

The gateway is a fake that keeps QRadar's notes in memory and filters them like QRadar's
`note_text LIKE`; the database is real. The gateway itself is tested with the note profile in
services/mcp-gateway/tests/test_note_profile.py (T-018).
"""

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml
from pydantic import JsonValue, ValidationError
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from ais0c_activities import (
    NOTE_PROFILE,
    WRITE_OFFENSE_NOTE,
    NoteActivities,
    NoteGatewayClient,
    NoteToolset,
    RuntimeConfigError,
    SessionFactory,
    load_note_runtime,
)
from ais0c_activities.note import NOTE_WINDOW, NOTES_PAGE_SIZE
from ais0c_agents import GatewayClient, GatewayError, GatewayUnavailableError, ToolsetProfile
from ais0c_contracts import (
    CaseSource,
    CaseVerdict,
    Confidence,
    CostClass,
    Level,
    NoteContent,
    RunStatus,
    TimeWindow,
    ToolCoverage,
    ToolIntent,
    ToolResult,
    ToolStatus,
)
from ais0c_executor.note import (
    EXECUTOR_ID,
    MAX_NOTE_LENGTH,
    EvaluationNote,
    NoDecisionNote,
    NoteOutcome,
    NoteRequest,
    NoteResult,
    OffenseNotesError,
    note_run_marker,
    render_note,
)
from ais0c_storage import ActorKind, NoteStatus, PlatformFlag
from ais0c_storage.models import AgentRunRow, NoteWrittenRow
from ais0c_storage.repositories import create_case, get_note, list_agent_runs, set_platform_flag

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 2, 11, 6, tzinfo=UTC)
OFFENSE_ID = 4711
CASE_ID = f"case-{OFFENSE_ID}"
CASE_URL = f"https://ais0c.example.com/cases/{CASE_ID}"
MARKER = "5e1f00"
_LIKE = re.compile(r'note_text LIKE "%(?P<text>[^"%]+)%"')

# GET /v1/tools for the note profile, in the gateway's shape (Profile.tool_list).
TOOL_LIST: dict[str, JsonValue] = {
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
TOOLSET = NoteToolset.model_validate(TOOL_LIST)


def ok(*rows: dict[str, JsonValue], truncated: bool = False) -> ToolResult:
    return ToolResult(
        status=ToolStatus.OK,
        evidence_id="ev_01JBNOTEACTIVITY01",
        data=list(rows),
        truncated=truncated,
        coverage=ToolCoverage(complete=not truncated, gaps=[]),
    )


def refused(status: ToolStatus, reason: str) -> ToolResult:
    return ToolResult(
        status=status,
        deny_reason=reason,
        data=[],
        truncated=False,
        coverage=ToolCoverage(complete=False, gaps=[]),
    )


class NotesGateway(GatewayClient):
    """QRadar's notes behind the gateway. Answers queued per tool come first; an add can also
    keep the note and then lose its answer, as a timed-out call would."""

    def __init__(self) -> None:
        self.notes: dict[int, list[str]] = {}
        self.intents: list[ToolIntent] = []
        self.queued: dict[str, list[ToolResult | GatewayError]] = {}
        self.lose_add_answers = 0

    async def call(self, intent: ToolIntent) -> ToolResult:
        checked = ToolIntent.model_validate(intent.model_dump())
        assert checked.reason.strip()
        assert checked.expected_evidence.strip()
        self.intents.append(checked)
        queued = self.queued.get(intent.tool_id)
        if queued:
            answer = queued.pop(0)
            if isinstance(answer, GatewayError):
                raise answer
            return answer
        arguments = intent.arguments
        offense_id = arguments["offense_id"]
        assert isinstance(offense_id, int)
        if intent.tool_id == "get_offense_notes":
            match = _LIKE.fullmatch(str(arguments["filter"]))
            assert match is not None
            found = [note for note in self.notes.get(offense_id, []) if match["text"] in note]
            start, limit = arguments["start"], arguments["limit"]
            assert isinstance(start, int)
            assert isinstance(limit, int)
            page = found[start : start + limit]
            return ok(*({"id": start + i, "note_text": text} for i, text in enumerate(page)))
        assert intent.tool_id == "add_offense_note"
        text = arguments["note_text"]
        assert isinstance(text, str)
        self.notes.setdefault(offense_id, []).append(text)
        if self.lose_add_answers:
            self.lose_add_answers -= 1
            raise GatewayError("the gateway call failed (ReadTimeout)")
        return ok({"id": 99, "note_text": text, "username": "API_token: AIS0C"})

    def with_marker(self, offense_id: int, marker: str) -> list[str]:
        return [note for note in self.notes.get(offense_id, []) if note_run_marker(note) == marker]


def note_request(**changes: object) -> EvaluationNote:
    content = NoteContent(
        offense_id=OFFENSE_ID,
        evaluation_no=1,
        run_marker=MARKER,
        verdict=CaseVerdict.FP,
        confidence=Confidence.HIGH,
        notify_level=Level.LOW,
        summary_tr="Yedekleme hesabının planlı işi; zaman ve hedef bakım penceresiyle uyumlu.",
        urgent_events=[],
        recommended_actions=[],
        data_gaps=[],
        case_url=CASE_URL,
    )
    return EvaluationNote(
        case_id=CASE_ID,
        evaluated_at=NOW - timedelta(minutes=1),
        content=content.model_copy(update=changes),
    )


@pytest.fixture
async def case(sessions: SessionFactory) -> None:
    async with sessions.begin() as session:
        await create_case(
            session,
            case_id=CASE_ID,
            source=CaseSource.OFFENSE,
            offense_id=OFFENSE_ID,
            sla_due_at=NOW,
            workflow_id=CASE_ID,
            run_id="run-1",
        )


async def writes(sessions: SessionFactory, enabled: bool) -> None:
    async with sessions.begin() as session:
        await set_platform_flag(
            session,
            PlatformFlag.WRITES_ENABLED,
            enabled=enabled,
            reason="T-019 test",
            actor_kind=ActorKind.USER,
            actor_id="admin01",
        )


@pytest.fixture
def gateway() -> NotesGateway:
    return NotesGateway()


@pytest.fixture
def activities(sessions: SessionFactory, gateway: NotesGateway, case: None) -> NoteActivities:
    return NoteActivities(sessions=sessions, gateway=gateway, toolset=TOOLSET, clock=lambda: NOW)


async def run(activities: NoteActivities, request: NoteRequest) -> NoteOutcome:
    return await ActivityEnvironment().run(activities.write_offense_note, request)


async def runs(sessions: SessionFactory) -> list[AgentRunRow]:
    async with sessions() as session:
        return await list_agent_runs(session, case_id=CASE_ID)


async def recorded(sessions: SessionFactory, marker: str = MARKER) -> NoteWrittenRow:
    async with sessions() as session:
        row = await get_note(session, offense_id=OFFENSE_ID, run_marker=marker)
    assert row is not None
    return row


# --- criterion 6: through the gateway with the note profile ---------------------------------


async def test_the_note_goes_through_the_gateway_with_the_note_profile(
    sessions: SessionFactory, activities: NoteActivities, gateway: NotesGateway
) -> None:
    await writes(sessions, True)
    request = note_request()

    outcome = await run(activities, request)

    assert outcome == NoteOutcome(
        result=NoteResult.WRITTEN, offense_id=OFFENSE_ID, run_marker=MARKER
    )
    read, add = gateway.intents
    [agent_run] = await runs(sessions)
    for intent, tool in zip((read, add), ("get_offense_notes", "add_offense_note"), strict=True):
        assert intent.tool_id == tool
        assert (intent.toolset_profile, intent.agent_id, intent.case_id, intent.hunt_id) == (
            "qradar-note-write",
            "action-executor",
            CASE_ID,
            None,
        )
        assert intent.run_id == agent_run.run_id
        assert intent.tool_schema_version == next(
            t.schema_version for t in TOOLSET.tools if t.id == tool
        )
        assert intent.cost_class is CostClass.LOW
        assert intent.time_window == TimeWindow(start=NOW - NOTE_WINDOW, end=NOW)
    assert read.arguments == {
        "offense_id": OFFENSE_ID,
        "filter": f'note_text LIKE "%run:{MARKER}%"',
        "fields": "id,note_text",
        "start": 0,
        "limit": NOTES_PAGE_SIZE,
    }
    assert add.arguments == {"offense_id": OFFENSE_ID, "note_text": render_note(request)}
    assert (agent_run.agent_id, agent_run.toolset_profile, agent_run.status) == (
        "action-executor",
        "qradar-note-write",
        RunStatus.COMPLETED,
    )
    assert (agent_run.tool_calls, agent_run.model_alias, agent_run.prompt_version) == (
        2,
        "none",
        "none",
    )
    assert gateway.notes == {OFFENSE_ID: [render_note(request)]}
    assert (await recorded(sessions)).status is NoteStatus.WRITTEN


async def test_a_no_decision_note_is_written_the_same_way(
    sessions: SessionFactory, activities: NoteActivities, gateway: NotesGateway
) -> None:
    await writes(sessions, True)
    request = NoDecisionNote(
        case_id=CASE_ID,
        offense_id=OFFENSE_ID,
        evaluation_no=1,
        run_marker="0dec0de",
        evaluated_at=NOW,
        case_url=CASE_URL,
    )

    outcome = await run(activities, request)

    assert outcome.result is NoteResult.WRITTEN
    assert gateway.with_marker(OFFENSE_ID, "0dec0de") == [render_note(request)]


# --- criterion 3: a retried activity leaves one note ----------------------------------------


@workflow.defn(name="T019WriteNote")
class WriteNoteWorkflow:
    """Calls the activity as a case workflow would (T-026), with retries."""

    @workflow.run
    async def run(self, request: EvaluationNote) -> NoteOutcome:
        return await workflow.execute_activity(
            WRITE_OFFENSE_NOTE,
            request,
            result_type=NoteOutcome,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=100), maximum_attempts=3
            ),
        )


async def test_a_retried_activity_leaves_one_note_in_qradar(
    sessions: SessionFactory, activities: NoteActivities, gateway: NotesGateway
) -> None:
    """The first attempt's note reaches QRadar, but the answer is lost and the attempt fails.
    Temporal retries the activity, which finds the note and does not write it again."""
    await writes(sessions, True)
    gateway.lose_add_answers = 1
    request = note_request()

    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="t019-notes",
            workflows=[WriteNoteWorkflow],
            activities=activities.activities(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
    ):
        outcome = await env.client.execute_workflow(
            WriteNoteWorkflow.run,
            request,
            id=f"t019-{secrets.token_hex(4)}",
            task_queue="t019-notes",
        )

    assert outcome.result is NoteResult.SKIPPED_DUPLICATE
    assert len(gateway.with_marker(OFFENSE_ID, MARKER)) == 1
    assert [intent.tool_id for intent in gateway.intents] == [
        "get_offense_notes",
        "add_offense_note",
        "get_offense_notes",
    ]
    first, second = sorted(await runs(sessions), key=lambda item: item.started_at)
    assert (first.status, second.status) == (RunStatus.FAILED, RunStatus.COMPLETED)
    row = await recorded(sessions)
    assert (row.status, row.error) == (NoteStatus.SKIPPED_DUPLICATE, None)


# --- gateway answers -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "answer", "retryable"),
    [
        (
            "add_offense_note",
            refused(
                ToolStatus.DENIED,
                "caller_not_allowed: qradar-note-write serves only action-executor",
            ),
            False,
        ),
        (
            "add_offense_note",
            refused(ToolStatus.DENIED, "invalid_text: note_text is longer than 2000 characters"),
            False,
        ),
        (
            "add_offense_note",
            refused(ToolStatus.DENIED, "quota_exhausted: case pool, 120 per minute; try later"),
            True,
        ),
        (
            "add_offense_note",
            refused(ToolStatus.ERROR, "upstream_error: QRadar answered 503"),
            True,
        ),
        ("get_offense_notes", refused(ToolStatus.ERROR, "timeout: no answer in 60 s"), True),
        (
            "get_offense_notes",
            GatewayUnavailableError("the gateway cannot be reached (ConnectError)"),
            True,
        ),
        (
            "get_offense_notes",
            GatewayError("the gateway answered HTTP 503: gateway.storage_unavailable"),
            True,
        ),
    ],
)
async def test_a_refusal_is_an_outcome_and_a_failure_a_retry(
    sessions: SessionFactory,
    activities: NoteActivities,
    gateway: NotesGateway,
    tool: str,
    answer: ToolResult | GatewayError,
    retryable: bool,
) -> None:
    """A denial the next attempt would get again is returned as `failed`; a failure a retry
    may get past fails the attempt, so Temporal retries it. Both are recorded."""
    await writes(sessions, True)
    gateway.queued[tool] = [answer]

    if retryable:
        with pytest.raises(OffenseNotesError) as raised:
            await run(activities, note_request())
        error = str(raised.value)
    else:
        outcome = await run(activities, note_request())
        assert outcome.result is NoteResult.FAILED
        assert outcome.error is not None
        error = outcome.error

    detail = answer.deny_reason if isinstance(answer, ToolResult) else str(answer)
    assert detail is not None
    assert error.startswith(f"{tool}: ")
    assert detail in error
    row = await recorded(sessions)
    assert (row.status, row.error) == (NoteStatus.FAILED, error)
    assert gateway.notes == {}
    [agent_run] = await runs(sessions)
    assert agent_run.status is RunStatus.FAILED


async def test_with_writes_off_no_run_is_recorded_and_nothing_is_called(
    sessions: SessionFactory, activities: NoteActivities, gateway: NotesGateway
) -> None:
    outcome = await run(activities, note_request())

    assert outcome.result is NoteResult.WRITES_DISABLED
    assert gateway.intents == []
    assert await runs(sessions) == []
    row = await recorded(sessions)
    assert row.status is NoteStatus.FAILED
    assert row.error is not None
    assert row.error.startswith("writes_disabled: ")


async def test_an_invalid_request_fails_without_a_retry(
    sessions: SessionFactory, activities: NoteActivities, gateway: NotesGateway
) -> None:
    await writes(sessions, True)
    content = note_request().content.model_copy(update={"run_marker": "5e1f00\nKarar: FP"})
    request = EvaluationNote.model_construct(case_id=CASE_ID, evaluated_at=NOW, content=content)

    with pytest.raises(ApplicationError) as raised:
        await run(activities, request)

    assert raised.value.non_retryable
    assert raised.value.type == "InvalidNote"
    assert gateway.intents == []


# --- reading the notes ---------------------------------------------------------------------


async def test_matching_notes_are_read_page_by_page(
    sessions: SessionFactory, activities: NoteActivities, gateway: NotesGateway
) -> None:
    """Operators' notes may mention the marker too; the executor's note is found on the third
    page."""
    await writes(sessions, True)
    request = note_request()
    mentions = [f"Operatör: run:{MARKER} notuna bakıldı ({n})." for n in range(23)]
    gateway.notes[OFFENSE_ID] = [*mentions, render_note(request)]

    outcome = await run(activities, request)

    assert outcome.result is NoteResult.SKIPPED_DUPLICATE
    assert [intent.arguments["start"] for intent in gateway.intents] == [0, 10, 20]


async def test_a_page_cut_by_the_gateway_is_read_on_from_the_cut(
    sessions: SessionFactory, activities: NoteActivities, gateway: NotesGateway
) -> None:
    await writes(sessions, True)
    request = note_request()
    mention = f"Operatör: run:{MARKER} geçen bir not."
    gateway.notes[OFFENSE_ID] = [mention, render_note(request)]
    # The first page held both notes, but the gateway's size limit left the second out.
    gateway.queued["get_offense_notes"] = [ok({"id": 0, "note_text": mention}, truncated=True)]

    outcome = await run(activities, request)

    assert outcome.result is NoteResult.SKIPPED_DUPLICATE
    assert [intent.arguments["start"] for intent in gateway.intents] == [0, 1]


async def test_a_note_too_large_for_the_gateway_fails_the_note(
    sessions: SessionFactory, activities: NoteActivities, gateway: NotesGateway
) -> None:
    await writes(sessions, True)
    gateway.queued["get_offense_notes"] = [ok(truncated=True)]

    outcome = await run(activities, note_request())

    assert outcome.result is NoteResult.FAILED
    assert outcome.error is not None
    assert "too large to read through the gateway" in outcome.error
    assert [intent.tool_id for intent in gateway.intents] == ["get_offense_notes"]


async def test_too_many_matching_notes_fail_the_note(
    sessions: SessionFactory, activities: NoteActivities, gateway: NotesGateway
) -> None:
    await writes(sessions, True)
    gateway.notes[OFFENSE_ID] = [f"run:{MARKER} ({n})" for n in range(150)]

    outcome = await run(activities, note_request())

    assert outcome.result is NoteResult.FAILED
    assert outcome.error is not None
    assert "more notes holding" in outcome.error
    assert len(gateway.intents) == 10


# --- the note profile's tool list ----------------------------------------------------------


def test_the_agents_toolset_refuses_the_note_profile() -> None:
    """The gateway lists the write tool as one (T-018), so an agent given the executor's token
    by mistake cannot even load the tools; the executor reads the list with its own model."""
    with pytest.raises(ValidationError):
        ToolsetProfile.model_validate(TOOL_LIST)
    assert [tool.risk for tool in TOOLSET.tools] == ["write", "read"]


@pytest.mark.parametrize(
    "change",
    [
        {"name": "qradar-triage-read"},
        {"tools": [TOOL_LIST["tools"][1]]},  # type: ignore[index]
        {"tools": [{**TOOL_LIST["tools"][0], "risk": "read"}, TOOL_LIST["tools"][1]]},  # type: ignore[index, dict-item]
    ],
)
def test_a_tool_list_that_is_not_the_note_profile_is_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NoteToolset.model_validate(TOOL_LIST | change)


async def test_the_executor_client_lists_the_note_profile() -> None:
    token = secrets.token_urlsafe(32)
    requests: list[httpx2.Request] = []
    body = json.dumps(TOOL_LIST).encode()

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, content=body)

    client = NoteGatewayClient("http://gateway.test", token, transport=httpx2.MockTransport(answer))

    assert await client.fetch_note_toolset() == TOOLSET
    [request] = requests
    assert (request.method, request.url.path) == ("GET", "/v1/tools")
    assert request.headers["authorization"] == f"Bearer {token}"

    agent_profile = json.dumps({**TOOL_LIST, "name": "qradar-triage-read"}).encode()
    other = NoteGatewayClient(
        "http://gateway.test",
        token,
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, content=agent_profile)),
    )
    with pytest.raises(GatewayError, match="not the qradar-note-write profile"):
        await other.fetch_note_toolset()


async def test_the_runtime_needs_the_gateway_and_the_executors_token(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigError, match="AIS0C_GATEWAY_URL"):
        await load_note_runtime({"AIS0C_EXECUTOR_SECRETS_DIR": str(tmp_path)})
    with pytest.raises(RuntimeConfigError, match="gateway-token-qradar-note-write"):
        await load_note_runtime(
            {
                "AIS0C_GATEWAY_URL": "http://127.0.0.1:8090",
                "AIS0C_EXECUTOR_SECRETS_DIR": str(tmp_path),
            }
        )


# --- the gateway's configuration ------------------------------------------------------------


def test_the_gateway_configuration_matches_the_executor() -> None:
    """The note profile (T-018) holds the tools the executor calls, for the caller it runs as,
    and its text limit is QRadar's, which the executor's notes keep to."""
    connector = yaml.safe_load((REPO / "config/connectors/qradar.yaml").read_text(encoding="utf-8"))
    policy = yaml.safe_load((REPO / "config/policies/qradar.yaml").read_text(encoding="utf-8"))

    tools = {tool["id"]: tool for tool in connector["profiles"][NOTE_PROFILE]["tools"]}
    assert {tool_id: (tool["risk"], tool["caller"]) for tool_id, tool in tools.items()} == {
        "add_offense_note": ("write", EXECUTOR_ID),
        "get_offense_notes": ("read", EXECUTOR_ID),
    }
    add_schema = connector["tools"]["add_offense_note"]["input_schema"]
    assert add_schema["properties"]["note_text"]["maxLength"] >= MAX_NOTE_LENGTH
    read_schema = connector["tools"]["get_offense_notes"]["input_schema"]["properties"]
    assert {"offense_id", "filter", "fields", "start", "limit"} <= read_schema.keys()
    assert read_schema["limit"]["maximum"] >= NOTES_PAGE_SIZE
    rule = policy["profiles"][NOTE_PROFILE]["text_arguments"]["note_text"]
    assert rule == {"max_length": MAX_NOTE_LENGTH, "multiline": True}
