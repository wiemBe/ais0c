"""CaseWorkflow: evaluations, signals, the SLA timer (T-010 criteria 7 and 8), the Triage child
run (T-012 criterion 2), re-evaluation of updates (T-014 criterion 2) and the retry of a run the
model's outage ended (T-014 criterion 5).

The workflow runs in the sandbox against fake activities and TriageStub in place of
TriageWorkflow; the SLA and retry tests skip time on the Temporal test server.
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
    AgentStub,
    BudgetExhaustedTriage,
    CaseFakes,
    CrashingTriage,
    TriageCall,
    TriageStub,
    offense,
    triage_failure,
    triage_result,
)

from ais0c_contracts import Level, RunStatus, TriageResult
from ais0c_workflows import (
    CaseCarry,
    CaseStatus,
    CaseView,
    CaseWorkflow,
    TriageFailure,
    TriageOutcome,
)
from ais0c_workflows.names import CASE_TASK_QUEUE, OFFENSE_CLOSED, OFFENSE_UPDATED, TRIAGE_WORKFLOW

pytestmark = pytest.mark.anyio

OFFENSE_ID = 101
CASE_ID = "case-101"
FIRST_RUN = f"{CASE_ID}-triage-1"
RETRY_RUN = f"{CASE_ID}-triage-1-retry"
NEW_RULE = (100201, 100305)


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
        workflows=[CaseWorkflow, triage_workflow, AgentStub],
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


def triage_numbers(fakes: CaseFakes) -> list[int]:
    return [number for name, number in fakes.events.seen if name == "triage"]


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
        fakes.offense = offense(OFFENSE_ID, start=now, updated=updated, rule_ids=NEW_RULE)
        await handle.signal(OFFENSE_UPDATED, updated)
        await fakes.events.wait_for("decided", 2)
        assert (await handle.query(CaseWorkflow.state)).evaluation_no == 2

        # A version already checked starts nothing.
        await handle.signal(OFFENSE_UPDATED, updated)
        await handle.signal(OFFENSE_UPDATED, now)
        result = await close(handle)

    assert result.evaluation_no == 2
    assert triage_numbers(fakes) == [1, 2]
    assert fakes.evaluations == [(1, now), (2, updated)]
    assert fakes.recorded == [updated]


async def test_updates_during_an_evaluation_are_checked_once_after_it(
    env: WorkflowEnvironment,
) -> None:
    now = await env.get_current_time()
    release = asyncio.Event()

    async def slow_first(call: TriageCall) -> TriageResult:
        if call.evaluation_no == 1:
            await release.wait()
        return triage_result()

    fakes = CaseFakes(offense(OFFENSE_ID, start=now), triage_behavior=slow_first)
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("triage", 1)
        latest = now + timedelta(minutes=2)
        fakes.offense = offense(
            OFFENSE_ID, start=now, updated=latest, usernames=["svc_backup_7731"]
        )
        await handle.signal(OFFENSE_UPDATED, now + timedelta(minutes=1))
        await handle.signal(OFFENSE_UPDATED, latest)
        release.set()
        await fakes.events.wait_for("decided", 2)
        result = await close(handle)

    assert result.evaluation_no == 2
    assert fakes.evaluations == [(1, now), (2, latest)]
    assert fakes.recorded == [latest]


async def test_an_update_with_only_more_events_waits_for_the_interval(
    env: WorkflowEnvironment,
) -> None:
    """T-014 criterion 2 and T-026 criterion 9: an update `should_reevaluate` turns down is
    recorded, without triage. More events count once the interval has passed since the
    evaluation, compared with what the evaluation saw: the case evaluates them then, once, with
    the offense as it is at that time (T-30 (1))."""
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now, event_count=12))

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)

        await env.sleep(timedelta(minutes=10))
        v2 = now + timedelta(minutes=10)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v2, event_count=40)
        await handle.signal(OFFENSE_UPDATED, v2)
        await fakes.events.wait_for("recorded", 1)
        # The decision stays as it is.
        state = await handle.query(CaseWorkflow.state)
        assert (state.status, state.evaluation_no, state.notify_level) == (
            CaseStatus.DECIDED,
            1,
            Level.HIGH,
        )

        # A second update inside the interval plans nothing more.
        await env.sleep(timedelta(minutes=5))
        v3 = now + timedelta(minutes=15)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v3, event_count=50)
        await handle.signal(OFFENSE_UPDATED, v3)
        await fakes.events.wait_for("recorded", 2)
        await env.sleep(timedelta(minutes=10))
        assert fakes.evaluations == [(1, now)]

        # 30 minutes after the evaluation the planned evaluation runs, once.
        await env.sleep(timedelta(minutes=10))
        await fakes.events.wait_for("decided", 2)
        await env.sleep(timedelta(minutes=40))
        result = await close(handle)

    # The planned evaluation fetched and recorded the offense once more.
    assert fakes.recorded == [v2, v3, v3]
    assert fakes.evaluations == [(1, now), (2, v3)]
    assert triage_numbers(fakes) == [1, 2]
    assert result.evaluation_no == 2


async def test_an_evaluation_drops_the_planned_one(env: WorkflowEnvironment) -> None:
    """T-026 criterion 9: an update evaluated at once before the interval ends takes the place
    of the evaluation planned for its end."""
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now, event_count=12))

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        await env.sleep(timedelta(minutes=10))
        v2 = now + timedelta(minutes=10)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v2, event_count=40)
        await handle.signal(OFFENSE_UPDATED, v2)
        await fakes.events.wait_for("recorded", 1)

        # A new rule is evaluated at once.
        await env.sleep(timedelta(minutes=5))
        v3 = now + timedelta(minutes=15)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v3, rule_ids=NEW_RULE)
        await handle.signal(OFFENSE_UPDATED, v3)
        await fakes.events.wait_for("decided", 2)

        # Past the end of the first interval: nothing more is evaluated.
        await env.sleep(timedelta(minutes=30))
        result = await close(handle)

    assert fakes.evaluations == [(1, now), (2, v3)]
    assert result.evaluation_no == 2


async def test_a_planned_evaluation_with_nothing_left_to_evaluate_ends(
    env: WorkflowEnvironment,
) -> None:
    """When the planned evaluation's check finds nothing new, nothing is evaluated and nothing
    more is planned: the case waits for the next signal."""
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now, event_count=12))

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        await env.sleep(timedelta(minutes=10))
        v2 = now + timedelta(minutes=10)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v2, event_count=40)
        await handle.signal(OFFENSE_UPDATED, v2)
        await fakes.events.wait_for("recorded", 1)
        # The source answers the planned check with what the evaluation saw.
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v2, event_count=12)
        await env.sleep(timedelta(minutes=25))
        await fakes.events.wait_for("recorded", 2)
        await env.sleep(timedelta(hours=2))
        result = await close(handle)

    assert fakes.recorded == [v2, v2]
    assert fakes.evaluations == [(1, now)]
    assert result.evaluation_no == 1


async def test_the_interval_comes_from_the_settings(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now, event_count=12), reevaluation_interval=timedelta(hours=2)
    )

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        await env.sleep(timedelta(minutes=40))
        v2 = now + timedelta(minutes=40)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v2, event_count=40)
        await handle.signal(OFFENSE_UPDATED, v2)
        await fakes.events.wait_for("recorded", 1)

        # Past 30 minutes, inside the two hours.
        await env.sleep(timedelta(minutes=60))
        assert fakes.evaluations == [(1, now)]
        await env.sleep(timedelta(minutes=25))
        await fakes.events.wait_for("decided", 2)
        await close(handle)

    assert fakes.evaluations == [(1, now), (2, v2)]


async def test_a_case_without_an_ai_decision_waits_only_the_retry_delay(
    env: WorkflowEnvironment,
) -> None:
    """T-026 criterion 10 (T-30 (2)): while the case is `no_ai_decision`, an update with only
    more events is evaluated once the retry delay (5 minutes) has passed, not 30 minutes."""

    async def no_decision_at_first(call: TriageCall) -> TriageResult:
        if call.evaluation_no == 1:
            raise triage_failure(TriageFailure.INVALID_OUTPUT)
        return triage_result()

    now = await env.get_current_time()
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now, event_count=12), triage_behavior=no_decision_at_first
    )

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("no_ai_decision", 1)
        await env.sleep(timedelta(minutes=2))
        v2 = now + timedelta(minutes=2)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v2, event_count=40)
        await handle.signal(OFFENSE_UPDATED, v2)
        await fakes.events.wait_for("recorded", 1)
        assert fakes.evaluations == [(1, now)]

        await env.sleep(timedelta(minutes=4))
        await fakes.events.wait_for("decided", 2)
        result = await close(handle)

    assert fakes.evaluations == [(1, now), (2, v2)]
    assert result.status is CaseStatus.CLOSED


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

    async def never_finishes(call: TriageCall) -> TriageResult:
        await release.wait()
        return triage_result()

    fakes = CaseFakes(offense(OFFENSE_ID, start=now), triage_behavior=never_finishes)
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("triage", 1)
        result = await close(handle)
        # The run is abandoned, not cancelled: it finishes on its own and records itself.
        run = env.client.get_workflow_handle(FIRST_RUN, result_type=TriageOutcome)
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
        workflows=[CaseWorkflow, TriageStub, AgentStub],
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

    async def model_down_once(call: TriageCall) -> TriageResult:
        if call.attempt == 1:
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


async def test_failed_triage_marks_no_ai_decision_until_an_update_is_evaluated(
    env: WorkflowEnvironment,
) -> None:
    now = await env.get_current_time()

    async def fails_first_evaluation(call: TriageCall) -> TriageResult:
        if call.evaluation_no == 1:
            raise triage_failure(TriageFailure.INVALID_OUTPUT)
        return triage_result()

    fakes = CaseFakes(offense(OFFENSE_ID, start=now), triage_behavior=fails_first_evaluation)
    async with running_case(env, fakes) as handle:
        await state_when(handle, lambda view: view.status is CaseStatus.NO_AI_DECISION)

        updated = now + timedelta(minutes=1)
        fakes.offense = offense(
            OFFENSE_ID, start=now, updated=updated, destination_ips=["198.51.100.15", "192.0.2.20"]
        )
        await handle.signal(OFFENSE_UPDATED, updated)
        await fakes.events.wait_for("decided", 2)
        result = await close(handle)

    assert (result.status, result.evaluation_no) == (CaseStatus.CLOSED, 2)


async def test_a_continued_case_keeps_its_evaluation_and_what_it_saw(
    env: WorkflowEnvironment,
) -> None:
    """The input Continue-As-New hands to the next run: nothing to check until an update, which
    is compared with the snapshot the carried evaluation saw."""
    now = await env.get_current_time()
    seen = offense(OFFENSE_ID, start=now)
    fakes = CaseFakes(seen)
    carry = CaseCarry(
        status=CaseStatus.DECIDED,
        evaluation_no=3,
        notify_level=Level.HIGH,
        evaluated_offense=seen,
        evaluated_at=now,
        checked_version=now,
        latest_version=now,
    )

    async with running_case(env, fakes, carry) as handle:
        assert (await handle.query(CaseWorkflow.state)).evaluation_no == 3
        v1 = now + timedelta(minutes=1)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v1)
        await handle.signal(OFFENSE_UPDATED, v1)
        await fakes.events.wait_for("recorded", 1)

        v2 = now + timedelta(minutes=2)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=v2, log_source_ids=[112, 413])
        await handle.signal(OFFENSE_UPDATED, v2)
        await fakes.events.wait_for("decided", 4)
        result = await close(handle)

    assert fakes.recorded == [v1, v2]
    assert fakes.evaluations == [(4, v2)]
    assert result.evaluation_no == 4


async def test_each_evaluation_is_triaged_by_its_own_run(env: WorkflowEnvironment) -> None:
    """T-012 criterion 2: triage is the child workflow TriageWorkflow, one run per evaluation."""
    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now))

    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        updated = now + timedelta(minutes=1)
        fakes.offense = offense(OFFENSE_ID, start=now, updated=updated, rule_ids=NEW_RULE)
        await handle.signal(OFFENSE_UPDATED, updated)
        await fakes.events.wait_for("decided", 2)
        await close(handle)

    assert fakes.triage_runs == [FIRST_RUN, f"{CASE_ID}-triage-2"]
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
    assert "retry" not in fakes.events.names()


# --- D-33: a run the model's outage ended is run once more ------------------------------------


@pytest.mark.parametrize("failure", [TriageFailure.MODEL_ERROR, TriageFailure.TIMEOUT])
async def test_a_run_the_model_ended_is_retried_after_the_wait(
    env: WorkflowEnvironment, failure: TriageFailure
) -> None:
    """Criterion 5: a model request that failed for good, or a run out of its wall clock, gets
    one more run after the configured wait, here 7 minutes. It decides within the SLA, so the
    case is never `no_ai_decision`."""
    now = await env.get_current_time()

    async def model_back_for_the_retry(call: TriageCall) -> TriageResult:
        if not call.retry:
            raise triage_failure(failure)
        return triage_result()

    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now),
        sla=timedelta(minutes=10),
        retry_delay=timedelta(minutes=7),
        triage_behavior=model_back_for_the_retry,
    )
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("retry")
        await env.sleep(timedelta(minutes=7))
        state = await state_when(handle, lambda view: view.status is CaseStatus.DECIDED)
        await close(handle)

    assert fakes.triage_runs == [FIRST_RUN, RETRY_RUN]
    assert fakes.events.names() == [
        "evaluation",
        "triage",
        "retry",
        "triage",
        "decided",
        "closed",
    ]
    _, _, decided_at = fakes.decisions[0]
    assert timedelta(minutes=7) <= decided_at - now < timedelta(minutes=10)
    assert state.evaluation_no == 1


async def test_a_second_model_failure_leaves_no_ai_decision(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()

    async def model_down(call: TriageCall) -> TriageResult:
        raise ApplicationError("LiteLLM answered 503", type="ModelHTTPError", non_retryable=True)

    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now), sla=timedelta(minutes=30), triage_behavior=model_down
    )
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("retry")
        await env.sleep(timedelta(minutes=5))
        state = await state_when(handle, lambda view: view.status is CaseStatus.NO_AI_DECISION)
        await close(handle)

    # One retry, after the default five minutes; the case is `no_ai_decision` before its SLA.
    assert fakes.triage_runs == [FIRST_RUN, RETRY_RUN]
    assert fakes.events.names() == [
        "evaluation",
        "triage",
        "retry",
        "triage",
        "no_ai_decision",
        "closed",
    ]
    assert (state.evaluation_no, fakes.decisions) == (1, [])


@pytest.mark.parametrize(
    "failure",
    [TriageFailure.INVALID_OUTPUT, TriageFailure.BUDGET_EXHAUSTED, TriageFailure.TOOL_ERROR],
)
async def test_other_failures_are_not_retried(
    env: WorkflowEnvironment, failure: TriageFailure
) -> None:
    """Invalid output and an exhausted budget are not the model's outage, nor is a tool call
    the gateway did not answer: the case is `no_ai_decision` at once."""
    now = await env.get_current_time()

    async def fails(call: TriageCall) -> TriageResult:
        raise triage_failure(failure)

    fakes = CaseFakes(offense(OFFENSE_ID, start=now), triage_behavior=fails)
    async with running_case(env, fakes) as handle:
        await state_when(handle, lambda view: view.status is CaseStatus.NO_AI_DECISION)
        await close(handle)

    assert fakes.triage_runs == [FIRST_RUN]
    assert fakes.events.names() == ["evaluation", "triage", "no_ai_decision", "closed"]


async def test_the_sla_can_pass_while_the_retry_waits(env: WorkflowEnvironment) -> None:
    """The SLA timer keeps running during the wait; the retry's decision replaces "no AI
    decision" like any late decision."""
    now = await env.get_current_time()

    async def model_back_for_the_retry(call: TriageCall) -> TriageResult:
        if not call.retry:
            raise triage_failure(TriageFailure.MODEL_ERROR)
        return triage_result()

    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now),
        sla=timedelta(minutes=10),
        retry_delay=timedelta(minutes=15),
        triage_behavior=model_back_for_the_retry,
    )
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("retry")
        await env.sleep(timedelta(minutes=11))
        await state_when(handle, lambda view: view.status is CaseStatus.NO_AI_DECISION)
        await env.sleep(timedelta(minutes=5))
        await state_when(handle, lambda view: view.status is CaseStatus.DECIDED)
        await close(handle)

    assert fakes.events.names() == [
        "evaluation",
        "triage",
        "retry",
        "no_ai_decision",
        "triage",
        "decided",
        "closed",
    ]


async def test_closing_during_the_wait_drops_the_retry(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()

    async def model_down(call: TriageCall) -> TriageResult:
        raise triage_failure(TriageFailure.MODEL_ERROR)

    fakes = CaseFakes(offense(OFFENSE_ID, start=now), triage_behavior=model_down)
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("retry")
        result = await close(handle)

    assert result.status is CaseStatus.CLOSED
    assert fakes.triage_runs == [FIRST_RUN]
