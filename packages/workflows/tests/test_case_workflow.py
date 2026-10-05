"""CaseWorkflow: evaluations, signals, the SLA timer (T-010 criteria 7 and 8) and the Triage
child run (T-012 criterion 2).

The workflow runs in the sandbox against fake activities and TriageStub in place of
TriageWorkflow; the SLA tests skip time on the Temporal test server.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflow_fakes import (
    WAIT_SECONDS,
    BudgetExhaustedTriage,
    CaseFakes,
    CrashingTriage,
    TriageStub,
    offense,
    triage_result,
)

from ais0c_contracts import Level, RunStatus, TriageResult
from ais0c_workflows import CaseCarry, CaseStatus, CaseView, CaseWorkflow, TriageOutcome
from ais0c_workflows.names import CASE_TASK_QUEUE, OFFENSE_CLOSED, OFFENSE_UPDATED, TRIAGE_WORKFLOW

pytestmark = pytest.mark.anyio

OFFENSE_ID = 101
CASE_ID = "case-101"


@asynccontextmanager
async def running_case(
    env: WorkflowEnvironment,
    fakes: CaseFakes,
    carry: CaseCarry | None = None,
    triage_workflow: type = TriageStub,
) -> AsyncIterator[WorkflowHandle[CaseWorkflow, CaseView]]:
    async with Worker(
        env.client,
        task_queue=CASE_TASK_QUEUE,
        workflows=[CaseWorkflow, triage_workflow],
        activities=fakes.activities(),
    ):
        handle = await env.client.start_workflow(
            CaseWorkflow.run,
            args=[OFFENSE_ID] if carry is None else [OFFENSE_ID, carry],
            id=CASE_ID,
            task_queue=CASE_TASK_QUEUE,
        )
        yield handle


async def state_when(
    handle: WorkflowHandle[CaseWorkflow, CaseView], check: Callable[[CaseView], bool]
) -> CaseView:
    """The `state` query's answer once `check` holds; the fakes report an activity's work before
    the workflow has seen its result."""
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            state = await handle.query(CaseWorkflow.state)
            if check(state):
                return state
            await asyncio.sleep(0.02)


async def close(handle: WorkflowHandle[CaseWorkflow, CaseView]) -> CaseView:
    await handle.signal(OFFENSE_CLOSED)
    return await handle.result()


async def test_first_evaluation_records_the_triage_decision(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now), floor_level=Level.MEDIUM)

    async with running_case(env, fakes) as handle:
        state = await state_when(handle, lambda view: view.status is CaseStatus.DECIDED)
        result = await close(handle)

    assert state == CaseView(
        case_id=CASE_ID,
        offense_id=OFFENSE_ID,
        status=CaseStatus.DECIDED,
        evaluation_no=1,
        notify_level=Level.HIGH,
    )
    assert fakes.events.names() == ["evaluation", "triage", "decided", "closed"]
    assert [(number, floor) for number, floor, _ in fakes.decisions] == [(1, Level.MEDIUM)]
    assert result.status is CaseStatus.CLOSED


async def test_offense_updated_increments_evaluation_and_runs_triage_again(
    env: WorkflowEnvironment,
) -> None:
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now))

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        updated = now + timedelta(minutes=3)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=updated)
        await handle.signal(OFFENSE_UPDATED, updated)
        await fakes.events.wait_for("decided", 2)
        assert (await handle.query(CaseWorkflow.state)).evaluation_no == 2

        # A version already evaluated starts nothing.
        await handle.signal(OFFENSE_UPDATED, updated)
        await handle.signal(OFFENSE_UPDATED, now)
        result = await close(handle)

    assert result.evaluation_no == 2
    assert [number for name, number in fakes.events.seen if name == "triage"] == [1, 2]
    assert fakes.evaluations == [(1, now), (2, updated)]


async def test_updates_during_an_evaluation_are_evaluated_once_after_it(
    env: WorkflowEnvironment,
) -> None:
    now = await env.get_current_time()
    release = asyncio.Event()

    async def slow_first(evaluation_no: int, attempt: int) -> TriageResult:
        if evaluation_no == 1:
            await release.wait()
        return triage_result()

    fakes = CaseFakes(offense(OFFENSE_ID, start=now), triage_behavior=slow_first)
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("triage", 1)
        latest = now + timedelta(minutes=2)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=latest)
        await handle.signal(OFFENSE_UPDATED, now + timedelta(minutes=1))
        await handle.signal(OFFENSE_UPDATED, latest)
        release.set()
        await fakes.events.wait_for("decided", 2)
        result = await close(handle)

    assert result.evaluation_no == 2
    assert fakes.evaluations == [(1, now), (2, latest)]


async def test_offense_closed_ends_the_workflow(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now))

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        result = await close(handle)
        description = await handle.describe()

    assert result.status is CaseStatus.CLOSED
    assert fakes.closed_records == [(CASE_ID, OFFENSE_ID)]
    assert description.status is not None
    assert description.status.name == "COMPLETED"


async def test_offense_closed_during_triage_abandons_the_evaluation(
    env: WorkflowEnvironment,
) -> None:
    now = await env.get_current_time()
    release = asyncio.Event()

    async def never_finishes(evaluation_no: int, attempt: int) -> TriageResult:
        await release.wait()
        return triage_result()

    fakes = CaseFakes(offense(OFFENSE_ID, start=now), triage_behavior=never_finishes)
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("triage", 1)
        result = await close(handle)
        # The run is abandoned, not cancelled: it finishes on its own and records itself.
        run = env.client.get_workflow_handle(f"{CASE_ID}-triage-1", result_type=TriageOutcome)
        assert (await run.describe()).status is WorkflowExecutionStatus.RUNNING
        release.set()
        outcome = await run.result()

    assert result.status is CaseStatus.CLOSED
    assert outcome.status is RunStatus.COMPLETED
    assert fakes.decisions == []
    assert fakes.closed_records == [(CASE_ID, OFFENSE_ID)]


async def test_offense_closed_before_the_first_evaluation(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now))

    async with Worker(
        env.client,
        task_queue=CASE_TASK_QUEUE,
        workflows=[CaseWorkflow, TriageStub],
        activities=fakes.activities(),
    ):
        handle = await env.client.start_workflow(
            CaseWorkflow.run,
            args=[OFFENSE_ID],
            id=CASE_ID,
            task_queue=CASE_TASK_QUEUE,
            start_signal=OFFENSE_CLOSED,
        )
        result = await handle.result()

    assert (result.status, result.evaluation_no) == (CaseStatus.CLOSED, 0)
    assert fakes.events.names() == ["closed"]


async def test_triage_past_the_sla_marks_no_ai_decision(env: WorkflowEnvironment) -> None:
    """Triage cannot reach the model; the next attempt is due after the 10-minute SLA."""
    now = await env.get_current_time()

    async def model_down_once(evaluation_no: int, attempt: int) -> TriageResult:
        if attempt == 1:
            raise ApplicationError("model unavailable", next_retry_delay=timedelta(minutes=20))
        return triage_result()

    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now), sla=timedelta(minutes=10), triage_behavior=model_down_once
    )
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("triage", 1)
        await env.sleep(timedelta(minutes=11))
        await state_when(handle, lambda view: view.status is CaseStatus.NO_AI_DECISION)
        assert fakes.decisions == []

        # Triage keeps running; its late decision replaces "no AI decision".
        await env.sleep(timedelta(minutes=10))
        await state_when(handle, lambda view: view.status is CaseStatus.DECIDED)
        await close(handle)

    _, _, decided_at = fakes.decisions[0]
    assert decided_at - now >= timedelta(minutes=20)


async def test_case_started_after_its_sla_is_marked_at_once(env: WorkflowEnvironment) -> None:
    """A backlog case starts past its deadline: no AI decision at once, triage still runs."""
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now - timedelta(hours=2)))

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        await close(handle)

    assert fakes.events.names() == ["evaluation", "no_ai_decision", "triage", "decided", "closed"]


async def test_failed_triage_marks_no_ai_decision_until_the_next_update(
    env: WorkflowEnvironment,
) -> None:
    now = await env.get_current_time()

    async def fails_first_evaluation(evaluation_no: int, attempt: int) -> TriageResult:
        if evaluation_no == 1:
            raise ApplicationError("malformed output", non_retryable=True)
        return triage_result()

    fakes = CaseFakes(offense(OFFENSE_ID, start=now), triage_behavior=fails_first_evaluation)
    async with running_case(env, fakes) as handle:
        await state_when(handle, lambda view: view.status is CaseStatus.NO_AI_DECISION)

        updated = now + timedelta(minutes=1)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=updated)
        await handle.signal(OFFENSE_UPDATED, updated)
        await fakes.events.wait_for("decided", 2)
        result = await close(handle)

    assert (result.status, result.evaluation_no) == (CaseStatus.CLOSED, 2)


async def test_a_continued_case_keeps_its_evaluation_count(env: WorkflowEnvironment) -> None:
    """The input Continue-As-New hands to the next run: nothing to evaluate until an update."""
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now))
    carry = CaseCarry(
        status=CaseStatus.DECIDED,
        evaluation_no=3,
        notify_level=Level.HIGH,
        evaluated_version=now,
        latest_version=now,
    )

    async with running_case(env, fakes, carry) as handle:
        assert (await handle.query(CaseWorkflow.state)).evaluation_no == 3
        updated = now + timedelta(minutes=1)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=updated)
        await handle.signal(OFFENSE_UPDATED, updated)
        await fakes.events.wait_for("decided", 4)
        result = await close(handle)

    assert fakes.evaluations == [(4, updated)]
    assert result.evaluation_no == 4


async def test_each_evaluation_is_triaged_by_its_own_run(env: WorkflowEnvironment) -> None:
    """Criterion 2: triage is the child workflow TriageWorkflow, one run per evaluation."""
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now))

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        updated = now + timedelta(minutes=1)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=updated)
        await handle.signal(OFFENSE_UPDATED, updated)
        await fakes.events.wait_for("decided", 2)
        await close(handle)

    assert fakes.triage_runs == [f"{CASE_ID}-triage-1", f"{CASE_ID}-triage-2"]
    request = await env.client.get_workflow_handle(f"{CASE_ID}-triage-2").fetch_history()
    started = request.events[0].workflow_execution_started_event_attributes
    assert started.workflow_type.name == TRIAGE_WORKFLOW
    assert started.parent_workflow_execution.workflow_id == CASE_ID


@pytest.mark.parametrize("triage_workflow", [BudgetExhaustedTriage, CrashingTriage])
async def test_a_triage_run_without_a_decision_marks_no_ai_decision(
    env: WorkflowEnvironment, triage_workflow: type
) -> None:
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now))

    async with running_case(env, fakes, triage_workflow=triage_workflow) as handle:
        state = await state_when(handle, lambda view: view.status is CaseStatus.NO_AI_DECISION)
        result = await close(handle)

    assert (state.evaluation_no, fakes.decisions) == (1, [])
    assert result.status is CaseStatus.CLOSED
