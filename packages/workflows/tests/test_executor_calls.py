"""CaseWorkflow's calls to the Action Executor's activities (T-045 criteria 2-6 and 8) and what
becomes of a call the case gave up on (T-032 criterion 9, T-59 (7)).

The executor's activities are fakes on their own `soc-executor` queue, as the real worker runs
them (criterion 2); the tests read the requests the workflow built. The real activities run in
services/worker/tests/test_executor_flow.py, and services/worker/tests/test_executor_requests.py
pins the request bodies to the executor's types. IPs are from the RFC 5737 ranges.
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Never

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflow_fakes import (
    WAIT_SECONDS,
    AgentBehavior,
    AgentCall,
    AgentFailure,
    AgentKind,
    AgentStub,
    CaseFakes,
    ChainResult,
    ExecutorBehavior,
    ExecutorRequest,
    GroupFakes,
    TriageCall,
    TriageStub,
    agent_failure,
    answer_agents,
    executor_worker,
    group_summary,
    offense,
    report_of,
    triage_failure,
    triage_result,
)

from ais0c_contracts import (
    ActionType,
    CaseVerdict,
    Confidence,
    DataGap,
    DataGapReason,
    Level,
    Recommendation,
    TriageResult,
    UrgentEvent,
)
from ais0c_workflows import (
    CaseStatus,
    CaseView,
    CaseWorkflow,
    GroupCaseWorkflow,
    TriageFailure,
    evaluation,
)
from ais0c_workflows.names import (
    CASE_TASK_QUEUE,
    CASE_URL,
    EXECUTOR_TASK_QUEUE,
    GROUP_UPDATED,
    OFFENSE_CLOSED,
    OFFENSE_UPDATED,
    SEND_EMAIL,
    WRITE_OFFENSE_NOTE,
)
from ais0c_workflows.notify import (
    EXECUTOR_TOTAL_TIMEOUT,
    NO_REPORT_SUMMARY_TR,
    SUMMARY_MAX,
    AbandonedCall,
    EvaluationNoteRequest,
    NoDecisionNoteRequest,
    run_marker,
)

pytestmark = pytest.mark.anyio

OFFENSE_ID = 101
CASE_ID = "case-101"
CASE_URL_OF_CASE = f"https://ais0c.example.com/cases/{CASE_ID}"
DESCRIPTION = "Excessive Firewall Accepts From Single Source"
MARKER = run_marker(CASE_ID, 1, "evaluation")
NO_DECISION_MARKER = run_marker(CASE_ID, 1, "no_ai_decision")
EXECUTOR_ACTIVITIES = {WRITE_OFFENSE_NOTE, SEND_EMAIL}


def deciding(level: Level = Level.MEDIUM) -> Callable[[TriageCall], Awaitable[TriageResult]]:
    """A Triage behavior that decides at `level` at once."""

    async def behavior(call: TriageCall) -> TriageResult:
        return triage_result(ai_level=level)

    return behavior


def gap(at: datetime) -> DataGap:
    return DataGap(
        source="Microsoft Windows Security Event Log",
        period_start=at,
        period_end=at + timedelta(hours=1),
        reason=DataGapReason.NO_DATA,
    )


def urgent_event(evidence_id: str, at: datetime, rank: int) -> UrgentEvent:
    return UrgentEvent(
        rank=rank,
        time=at,
        log_source="DC-01",
        event_name="Directory Service Access",
        source="198.51.100.15",
        reason="Directory replication by a non-machine account.",
        checklist=["Is the account an approved sync account?"],
        evidence_id=evidence_id,
    )


def recommendation(action_type: ActionType) -> Recommendation:
    return Recommendation(
        action_type=action_type,
        target="DC-01",
        rationale="Synthetic recommendation.",
        evidence_ids=["ev_1"],
    )


def reports(**fields: object) -> AgentBehavior:
    """An agent behavior whose Reporting answers a report with `fields`."""

    async def behavior(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.REPORTING:
            return report_of(call, **fields)
        return await answer_agents(call)

    return behavior


async def no_report(call: AgentCall) -> ChainResult:
    """Reporting ends its run without a result: the decision is recorded without a report."""
    if call.agent is AgentKind.REPORTING:
        raise agent_failure(AgentFailure.INVALID_OUTPUT)
    return await answer_agents(call)


async def late_report(call: AgentCall) -> ChainResult:
    """Reporting's first model request fails; its retry comes 15 minutes later."""
    if call.agent is AgentKind.REPORTING and call.attempt == 1:
        raise ApplicationError("model unavailable", next_retry_delay=timedelta(minutes=15))
    return await answer_agents(call)


@asynccontextmanager
async def running_case(
    env: WorkflowEnvironment, fakes: CaseFakes, *, executor: bool = True
) -> AsyncIterator[WorkflowHandle[CaseWorkflow, CaseView]]:
    """The case's workflow on its worker; the executor's fakes on theirs unless `executor` is
    False (the executor worker is not running)."""
    async with AsyncExitStack() as stack:
        if executor:
            await stack.enter_async_context(executor_worker(env, fakes))
        await stack.enter_async_context(
            Worker(
                env.client,
                task_queue=CASE_TASK_QUEUE,
                workflows=[CaseWorkflow, TriageStub, AgentStub],
                activities=fakes.activities(),
            )
        )
        yield await env.client.start_workflow(
            CaseWorkflow.run, args=[OFFENSE_ID], id=CASE_ID, task_queue=CASE_TASK_QUEUE
        )


async def state_when(
    handle: WorkflowHandle[CaseWorkflow, CaseView], status: CaseStatus
) -> CaseView:
    """The case's state once the workflow has digested its activities' results: the fakes
    report their work before the workflow has seen it."""
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            state = await handle.query(CaseWorkflow.state)
            if state.status is status:
                return state
            await asyncio.sleep(0.02)


async def close_decided(env: WorkflowEnvironment, fakes: CaseFakes) -> CaseView:
    """Run one evaluation to its decision and its note, then close the case."""
    async with running_case(env, fakes) as handle:
        await fakes.executor_events.wait_for("note", 1)
        view = await state_when(handle, CaseStatus.DECIDED)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()
    return view


def evaluation_notes(fakes: CaseFakes) -> list[EvaluationNoteRequest]:
    return [note for note in fakes.note_requests if isinstance(note, EvaluationNoteRequest)]


# --- criterion 3: the run marker ----------------------------------------------------------------


def test_the_marker_is_a_short_hash_of_case_evaluation_and_kind() -> None:
    marker = run_marker(CASE_ID, 1, "evaluation")

    assert marker == hashlib.sha256(f"{CASE_ID}:1:evaluation".encode()).hexdigest()[:12]
    assert set(marker) <= set("0123456789abcdef")
    # The same note has the same marker on every attempt; nothing else shares it.
    assert run_marker(CASE_ID, 1, "evaluation") == marker
    others = {
        run_marker(CASE_ID, 2, "evaluation"),
        run_marker(CASE_ID, 1, "no_ai_decision"),
        run_marker(CASE_ID, 1, "group"),
        run_marker("case-102", 1, "evaluation"),
    }
    assert len(others) == 4
    assert marker not in others


# --- criterion 4: the three notes ---------------------------------------------------------------


async def test_a_decided_evaluation_writes_the_note_of_its_report(env: WorkflowEnvironment) -> None:
    at = await env.get_current_time()
    ranked = [
        urgent_event(f"ev_{rank}", at + timedelta(minutes=rank), rank) for rank in range(1, 8)
    ]
    gaps = [gap(at)]
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=at),
        triage_behavior=deciding(Level.HIGH),
        agent_behavior=reports(
            summary_tr="Ö" * 500,
            # Seven by rank: the note carries the first five.
            urgent_events=ranked,
            recommendations=[
                recommendation(ActionType.INVESTIGATE_FURTHER),
                recommendation(ActionType.BLOCK_IOC_MANUAL),
                recommendation(ActionType.INVESTIGATE_FURTHER),
            ],
            data_gaps=gaps,
        ),
    )
    await close_decided(env, fakes)

    [request] = fakes.note_requests
    assert isinstance(request, EvaluationNoteRequest)
    assert request.case_id == CASE_ID
    [(_, _, decided_at)] = fakes.decisions
    assert request.evaluated_at == decided_at  # the decision's time, on the first line
    content = request.content
    assert (content.offense_id, content.evaluation_no, content.run_marker) == (
        OFFENSE_ID,
        1,
        MARKER,
    )
    assert (content.verdict, content.confidence, content.notify_level) == (
        CaseVerdict.SUSPICIOUS,
        Confidence.MEDIUM,
        Level.HIGH,
    )
    # The note's own limit, cut from the report's longer summary.
    assert content.summary_tr == "Ö" * (SUMMARY_MAX - 1) + "…"
    assert content.urgent_events == ranked[:5]
    # In order, each once.
    assert content.recommended_actions == [
        ActionType.INVESTIGATE_FURTHER,
        ActionType.BLOCK_IOC_MANUAL,
    ]
    assert content.data_gaps == gaps
    assert content.case_url == CASE_URL_OF_CASE
    assert content.group_id is None


async def test_a_decision_without_a_report_writes_the_fixed_summary(
    env: WorkflowEnvironment,
) -> None:
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=deciding(Level.MEDIUM),
        agent_behavior=no_report,
    )
    await close_decided(env, fakes)

    [decision] = fakes.chain_decisions
    assert decision.report is None
    [request] = fakes.note_requests
    assert isinstance(request, EvaluationNoteRequest)
    content = request.content
    assert content.summary_tr == NO_REPORT_SUMMARY_TR
    assert (content.urgent_events, content.recommended_actions, content.data_gaps) == ([], [], [])
    # The decision itself is on the note.
    assert (content.verdict, content.notify_level) == (CaseVerdict.SUSPICIOUS, Level.MEDIUM)
    assert content.run_marker == MARKER


async def test_an_evaluation_without_a_decision_writes_the_no_decision_note(
    env: WorkflowEnvironment,
) -> None:
    async def no_decision(call: TriageCall) -> Never:
        raise triage_failure(TriageFailure.INVALID_OUTPUT)

    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()), triage_behavior=no_decision
    )
    async with running_case(env, fakes) as handle:
        await fakes.executor_events.wait_for("note", 1)
        await state_when(handle, CaseStatus.NO_AI_DECISION)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    [request] = fakes.note_requests
    assert isinstance(request, NoDecisionNoteRequest)
    assert (request.case_id, request.offense_id, request.evaluation_no) == (CASE_ID, OFFENSE_ID, 1)
    assert request.run_marker == NO_DECISION_MARKER
    assert request.case_url == CASE_URL_OF_CASE
    assert fakes.email_requests == []  # no decision, no level, no e-mail


async def test_a_late_decision_writes_its_note_after_the_no_decision_one(
    env: WorkflowEnvironment,
) -> None:
    """Criterion 3 with D-30: the SLA passes and the no-decision note goes out; the decision
    that comes later writes its own note, under its own marker."""
    start = await env.get_current_time()
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=start),
        triage_behavior=deciding(Level.HIGH),
        agent_behavior=late_report,
    )
    async with running_case(env, fakes) as handle:
        await fakes.agent_events.wait_for("reporting", 1)
        await env.sleep(timedelta(minutes=11))
        await fakes.executor_events.wait_for("note", 1)
        await env.sleep(timedelta(minutes=10))
        await fakes.executor_events.wait_for("note", 2)
        await fakes.executor_events.wait_for("email", 1)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    first, second = fakes.note_requests
    assert isinstance(first, NoDecisionNoteRequest)
    assert first.run_marker == NO_DECISION_MARKER
    assert first.evaluated_at >= start + timedelta(minutes=10)  # at the deadline
    assert isinstance(second, EvaluationNoteRequest)
    assert second.content.run_marker == MARKER
    assert second.evaluated_at > first.evaluated_at
    # The case link is read once and kept.
    assert fakes.case_urls == [CASE_ID]


# --- criterion 8: the case does not wait for the executor ---------------------------------------


async def first_undecided(call: TriageCall) -> TriageResult:
    """Triage gives no decision in evaluation 1 and decides at high in the next ones."""
    if call.evaluation_no == 1:
        raise triage_failure(TriageFailure.INVALID_OUTPUT)
    return triage_result(ai_level=Level.HIGH)


async def evaluate_again(
    env: WorkflowEnvironment, fakes: CaseFakes, handle: WorkflowHandle[CaseWorkflow, CaseView]
) -> CaseView:
    """An update with a user the case has not seen: evaluated again at once (D-31)."""
    updated = await env.get_current_time()
    fakes.offense = offense(
        OFFENSE_ID, start=fakes.offense.start_time, updated=updated, usernames=["svc_backup_7731"]
    )
    await handle.signal(OFFENSE_UPDATED, updated)
    await fakes.events.wait_for("decided", 2)
    return await state_when(handle, CaseStatus.DECIDED)


async def test_the_case_does_not_wait_for_a_stopped_executor(env: WorkflowEnvironment) -> None:
    """With the executor worker down, the case still evaluates and records its decisions; the
    notes and the e-mail go out, in order, when the worker comes back."""
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()), triage_behavior=first_undecided
    )
    async with running_case(env, fakes, executor=False) as handle:
        await state_when(handle, CaseStatus.NO_AI_DECISION)
        view = await evaluate_again(env, fakes, handle)
        assert view.evaluation_no == 2
        assert (fakes.note_attempts, fakes.email_attempts) == ([], [])

        async with executor_worker(env, fakes):
            await fakes.executor_events.wait_for("note", 2)
            await fakes.executor_events.wait_for("email", 1)
            await handle.signal(OFFENSE_CLOSED)
            await handle.result()

    first, second = fakes.note_requests
    assert isinstance(first, NoDecisionNoteRequest)
    assert first.evaluation_no == 1
    assert isinstance(second, EvaluationNoteRequest)
    assert second.content.evaluation_no == 2
    assert [alert.content.evaluation_no for alert in fakes.email_requests] == [2]


async def test_a_note_in_progress_holds_up_neither_the_case_nor_the_e_mail(
    env: WorkflowEnvironment,
) -> None:
    """The no-decision note hangs; the next evaluation is decided and e-mailed meanwhile, and
    its note follows the first one when that is done (criterion 2: notes keep their order)."""
    release = asyncio.Event()

    async def first_note_hangs(request: ExecutorRequest, attempt: int) -> dict[str, object]:
        if isinstance(request, NoDecisionNoteRequest):
            await release.wait()
        return {"result": "written"}

    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=first_undecided,
        note_behavior=first_note_hangs,
    )
    async with running_case(env, fakes) as handle:
        await fakes.executor_events.wait_for("note", 1)
        await evaluate_again(env, fakes, handle)
        await fakes.executor_events.wait_for("email", 1)
        # Evaluation 2's note waits behind the first.
        assert len(fakes.note_requests) == 1
        release.set()
        await fakes.executor_events.wait_for("note", 2)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    assert [type(note) for note in fakes.note_requests] == [
        NoDecisionNoteRequest,
        EvaluationNoteRequest,
    ]
    assert fakes.note_attempts == [1, 1]


# --- criterion 5: the alert e-mail ---------------------------------------------------------------


async def test_a_high_evaluation_sends_the_case_alert_with_the_offense_name(
    env: WorkflowEnvironment,
) -> None:
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=deciding(Level.HIGH),
    )
    async with running_case(env, fakes) as handle:
        await fakes.executor_events.wait_for("email", 1)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    [alert] = fakes.email_requests
    [note] = evaluation_notes(fakes)
    assert alert.case_id == CASE_ID
    # The offense's description as QRadar holds it; the executor cleans and cuts it.
    assert alert.offense_name == DESCRIPTION
    # The same evaluation's data as its note.
    assert alert.content == note.content
    assert alert.evaluated_at == note.evaluated_at


async def test_the_floor_raises_the_level_that_is_e_mailed(env: WorkflowEnvironment) -> None:
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        floor_level=Level.CRITICAL,
        triage_behavior=deciding(Level.MEDIUM),
    )
    async with running_case(env, fakes) as handle:
        await fakes.executor_events.wait_for("email", 1)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    [alert] = fakes.email_requests
    assert alert.content.notify_level is Level.CRITICAL


@pytest.mark.parametrize("level", [Level.MEDIUM, Level.LOW])
async def test_a_medium_or_low_evaluation_sends_no_email(
    env: WorkflowEnvironment, level: Level
) -> None:
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=deciding(level),
    )
    await close_decided(env, fakes)

    assert fakes.email_attempts == []
    [note] = evaluation_notes(fakes)
    assert note.content.notify_level is level


# --- criterion 2: the executor's own queue ------------------------------------------------------


async def test_the_executor_activities_run_on_their_own_queue(env: WorkflowEnvironment) -> None:
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=deciding(Level.HIGH),
    )
    async with running_case(env, fakes) as handle:
        await fakes.executor_events.wait_for("email", 1)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()
        history = await handle.fetch_history()

    scheduled = [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]
    queues = {(item.activity_type.name, item.task_queue.name) for item in scheduled}
    assert {(WRITE_OFFENSE_NOTE, EXECUTOR_TASK_QUEUE), (SEND_EMAIL, EXECUTOR_TASK_QUEUE)} <= queues
    assert (CASE_URL, CASE_TASK_QUEUE) in queues
    # Only the executor's two activities leave the case queue.
    assert {name for name, queue in queues if queue != CASE_TASK_QUEUE} == EXECUTOR_ACTIVITIES
    assert all(
        queue == EXECUTOR_TASK_QUEUE for name, queue in queues if name in EXECUTOR_ACTIVITIES
    )


# --- criterion 6: the case link -----------------------------------------------------------------


async def test_the_case_link_comes_from_the_worker_setting(env: WorkflowEnvironment) -> None:
    base = "https://soc.example.com/cases"
    fakes = CaseFakes(offense(OFFENSE_ID, start=await env.get_current_time()), case_url_base=base)
    await close_decided(env, fakes)

    assert fakes.case_urls == [CASE_ID]
    [request] = evaluation_notes(fakes)
    assert request.content.case_url == f"{base}/{CASE_ID}"


# --- criterion 8: the executor's failures -------------------------------------------------------


def failing(times: int) -> ExecutorBehavior:
    """Fails the first `times` attempts with an error a retry may get past."""

    async def behavior(request: ExecutorRequest, attempt: int) -> dict[str, object]:
        if attempt <= times:
            raise ApplicationError("the gateway is unreachable")
        return {"result": "written"}

    return behavior


async def invalid_note(request: ExecutorRequest, attempt: int) -> dict[str, object]:
    raise ApplicationError("invalid note request", type="InvalidNote", non_retryable=True)


async def unreachable(request: ExecutorRequest, attempt: int) -> dict[str, object]:
    raise ApplicationError("the gateway is unreachable")


async def test_a_retryable_failure_is_retried_until_the_note_is_written(
    env: WorkflowEnvironment,
) -> None:
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=deciding(Level.MEDIUM),
        note_behavior=failing(times=2),
    )
    async with running_case(env, fakes) as handle:
        await state_when(handle, CaseStatus.DECIDED)
        await handle.signal(OFFENSE_CLOSED)
        # Closing waits for the note in progress.
        await handle.result()

    assert fakes.note_attempts == [1, 2, 3]  # two failures, then the note


async def test_an_invalid_note_is_not_retried_and_breaks_nothing(env: WorkflowEnvironment) -> None:
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=deciding(Level.MEDIUM),
        note_behavior=invalid_note,
    )
    view = await close_decided(env, fakes)

    assert fakes.note_attempts == [1]
    assert view.status is CaseStatus.DECIDED
    assert len(fakes.chain_decisions) == 1


async def test_a_failure_that_stays_leaves_the_case_and_the_e_mail_alone(
    env: WorkflowEnvironment,
) -> None:
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=deciding(Level.HIGH),
        note_behavior=unreachable,
    )
    async with running_case(env, fakes) as handle:
        await fakes.executor_events.wait_for("note", 1)
        # The e-mail does not wait for the note's retries.
        await fakes.executor_events.wait_for("email", 1)
        view = await state_when(handle, CaseStatus.DECIDED)
        await env.sleep(EXECUTOR_TOTAL_TIMEOUT + timedelta(minutes=1))
        tried = len(fakes.note_attempts)
        await env.sleep(timedelta(hours=1))
        await handle.signal(OFFENSE_CLOSED)
        result = await handle.result()

    # Retried with backoff until the total timeout, then left alone.
    assert tried > 5
    assert len(fakes.note_attempts) == tried
    assert fakes.email_attempts == [1]
    assert view.status is CaseStatus.DECIDED
    assert result.status is CaseStatus.CLOSED
    assert len(fakes.chain_decisions) == 1


# --- T-032 criterion 9: a call given up is written down as failed --------------------------------
#
# The Temporal test server skips time only while every activity it scheduled has a worker to run
# on, so the hour-long tests have an executor worker whose activities fail the whole hour; the
# case gives the calls up exactly as it does when no worker answers at all, which the last test
# shows in real time with a short timeout.

GROUP_ID = "G-0123456789ab-20261007T090000Z"
GROUP_CASE_ID = f"group-{GROUP_ID}"
AFTER_THE_HOUR = EXECUTOR_TOTAL_TIMEOUT + timedelta(minutes=5)


async def test_a_note_and_an_email_the_executor_kept_failing_are_recorded_as_failed(
    env: WorkflowEnvironment,
) -> None:
    """The executor fails every attempt for the whole hour: the case gives the calls up and
    records them (T-59 (7)), and goes on."""
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=deciding(Level.HIGH),
        note_behavior=unreachable,
        email_behavior=unreachable,
    )
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        # Within the hour the call may still succeed, so nothing is recorded.
        await env.sleep(EXECUTOR_TOTAL_TIMEOUT - timedelta(minutes=5))
        assert fakes.abandoned == []
        await env.sleep(timedelta(minutes=10))
        await fakes.events.wait_for("abandoned", 2)
        view = await state_when(handle, CaseStatus.DECIDED)
        await handle.signal(OFFENSE_CLOSED)
        result = await handle.result()

    assert (view.status, result.status) == (CaseStatus.DECIDED, CaseStatus.CLOSED)
    email, note = sorted(fakes.abandoned, key=lambda call: call.kind)
    assert note == AbandonedCall(
        kind="note",
        case_id=CASE_ID,
        evaluation_no=1,
        offense_id=OFFENSE_ID,
        run_marker=MARKER,
    )
    assert email == AbandonedCall(
        kind="email",
        case_id=CASE_ID,
        evaluation_no=1,
        email_kind="case_alert",
        level=Level.HIGH,
        idempotency_key=f"case_alert:{CASE_ID}:1",
    )


async def test_a_no_decision_note_is_recorded_with_its_own_marker(
    env: WorkflowEnvironment,
) -> None:
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=first_undecided,
        note_behavior=unreachable,
    )
    async with running_case(env, fakes) as handle:
        await state_when(handle, CaseStatus.NO_AI_DECISION)
        await env.sleep(AFTER_THE_HOUR)
        await fakes.events.wait_for("abandoned", 1)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    [call] = fakes.abandoned
    assert call == AbandonedCall(
        kind="note",
        case_id=CASE_ID,
        evaluation_no=1,
        offense_id=OFFENSE_ID,
        run_marker=NO_DECISION_MARKER,
    )


async def test_a_call_the_retries_got_past_is_not_recorded(env: WorkflowEnvironment) -> None:
    async def recovers_after_two_tries(request: ExecutorRequest, attempt: int) -> dict[str, object]:
        if attempt < 3:
            raise ApplicationError("the gateway is unreachable")
        return {"result": "written"}

    fakes = CaseFakes(
        offense(OFFENSE_ID, start=await env.get_current_time()),
        triage_behavior=deciding(Level.MEDIUM),
        note_behavior=recovers_after_two_tries,
    )
    async with running_case(env, fakes) as handle:
        await fakes.executor_events.wait_for("note", 1)
        await env.sleep(AFTER_THE_HOUR)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    assert fakes.note_attempts[-1] == 3
    assert fakes.abandoned == []


async def test_a_group_note_and_a_group_alert_the_executor_kept_failing_are_recorded(
    env: WorkflowEnvironment,
) -> None:
    """T-65 (6): the group case's calls are given up and recorded as the offense case's are."""
    now = await env.get_current_time()
    fakes = GroupFakes(
        offense(201, start=now),
        summary=group_summary(6, at=now),
        grouped=[201],
        window_end=now + timedelta(hours=10),
        triage_behavior=deciding(Level.HIGH),
        note_behavior=unreachable,
        email_behavior=unreachable,
    )
    async with (
        executor_worker(env, fakes),
        Worker(
            env.client,
            task_queue=CASE_TASK_QUEUE,
            workflows=[GroupCaseWorkflow, TriageStub, AgentStub],
            activities=fakes.activities(),
        ),
    ):
        handle = await env.client.start_workflow(
            GroupCaseWorkflow.run,
            args=[GROUP_ID],
            id=GROUP_CASE_ID,
            task_queue=CASE_TASK_QUEUE,
            start_signal=GROUP_UPDATED,
        )
        await env.sleep(timedelta(minutes=10))
        await fakes.events.wait_for("decided", 1)
        await env.sleep(AFTER_THE_HOUR)
        await fakes.events.wait_for("abandoned", 2)
        await handle.terminate()

    email, note = sorted(fakes.abandoned, key=lambda call: call.kind)
    assert (note.kind, note.case_id, note.offense_id, note.evaluation_no) == (
        "note",
        GROUP_CASE_ID,
        201,
        1,
    )
    assert note.run_marker == run_marker(GROUP_CASE_ID, 1, "group")
    assert email == AbandonedCall(
        kind="email",
        case_id=GROUP_CASE_ID,
        evaluation_no=1,
        email_kind="group_alert",
        level=Level.HIGH,
        group_id=GROUP_ID,
        idempotency_key=f"group_alert:{GROUP_ID}:1",
    )


async def test_with_no_executor_worker_at_all_the_call_is_given_up_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing polls the `soc-executor` queue. The workflow's hour is shortened to seconds (the
    sandbox runs this process's `evaluation` module, which holds the constant), and the test
    server runs in real time because the time-skipping one cannot skip past an activity no
    worker has picked up."""
    monkeypatch.setattr(evaluation, "EXECUTOR_TOTAL_TIMEOUT", timedelta(seconds=3))
    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter
    ) as local:
        fakes = CaseFakes(
            offense(OFFENSE_ID, start=datetime.now(UTC)),
            triage_behavior=deciding(Level.HIGH),
        )
        async with running_case(local, fakes, executor=False) as handle:
            async with asyncio.timeout(60):
                while len(fakes.abandoned) < 2:  # noqa: ASYNC110 - Events.wait_for gives up at 10 s
                    await asyncio.sleep(0.2)
            await handle.signal(OFFENSE_CLOSED)
            await handle.result()

    assert {call.kind for call in fakes.abandoned} == {"note", "email"}
    assert (fakes.note_attempts, fakes.email_attempts) == ([], [])
