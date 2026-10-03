"""Histories recorded in a test replay without error (criterion 9).

Each test drives a workflow through its paths on the test server, fetches the history and
replays it in the sandbox. A workflow changed under the same name must fail the replay, which
shows the replay really checks determinism.
"""

from datetime import timedelta

import pytest
from temporalio import workflow
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker
from workflow_fakes import CaseFakes, CaseStub, IntakeFakes, offense, triage_result

from ais0c_contracts import TriageResult
from ais0c_workflows import CASE_QUEUE_WORKFLOWS, CaseWorkflow, OffenseIntake
from ais0c_workflows.names import (
    CASE_TASK_QUEUE,
    CASE_WORKFLOW,
    ENRICH_OFFENSE,
    FETCH_OFFENSE,
    OFFENSE_CLOSED,
    OFFENSE_UPDATED,
)

pytestmark = pytest.mark.anyio


async def replay(history: WorkflowHistory) -> None:
    replayer = Replayer(
        workflows=list(CASE_QUEUE_WORKFLOWS), data_converter=pydantic_data_converter
    )
    await replayer.replay_workflow(history)


async def case_history(env: WorkflowEnvironment) -> WorkflowHistory:
    """A case with a missed SLA and a late decision, an update and a closure."""
    now = await env.get_current_time()

    async def model_down_once(evaluation_no: int, attempt: int) -> TriageResult:
        if evaluation_no == 1 and attempt == 1:
            raise ApplicationError("model unavailable", next_retry_delay=timedelta(minutes=20))
        return triage_result()

    fakes = CaseFakes(offense(101, start=now), triage_behavior=model_down_once)
    async with Worker(
        env.client,
        task_queue=CASE_TASK_QUEUE,
        workflows=[CaseWorkflow],
        activities=fakes.activities(),
    ):
        handle = await env.client.start_workflow(
            CaseWorkflow.run, args=[101], id="case-101", task_queue=CASE_TASK_QUEUE
        )
        await fakes.events.wait_for("triage", 1)
        await env.sleep(timedelta(minutes=25))
        await fakes.events.wait_for("decided", 1)
        updated = now + timedelta(minutes=30)
        fakes.offense = offense(101, start=now, updated=updated)
        await handle.signal(OFFENSE_UPDATED, updated)
        await fakes.events.wait_for("decided", 2)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()
    assert "no_ai_decision" in fakes.events.names()
    return await handle.fetch_history()


async def test_case_history_replays(env: WorkflowEnvironment) -> None:
    await replay(await case_history(env))


async def test_intake_history_replays(env: WorkflowEnvironment) -> None:
    fakes = IntakeFakes()
    async with Worker(
        env.client,
        task_queue=CASE_TASK_QUEUE,
        workflows=[OffenseIntake, CaseStub],
        activities=fakes.activities(),
    ):
        first = await env.client.execute_workflow(
            OffenseIntake.run, None, id="offense-intake-1", task_queue=CASE_TASK_QUEUE
        )
        fakes.open_cases.update({1, 2})
        case = await env.client.start_workflow(
            CaseStub.run, id="case-1", task_queue=CASE_TASK_QUEUE
        )
        fakes.closed.add(2)
        fakes.pending = [3, 4]
        await env.sleep(timedelta(minutes=5))
        now = await env.get_current_time()
        fakes.put(
            offense(1, start=now - timedelta(minutes=3), updated=now),
            offense(5, start=now - timedelta(minutes=2)),
        )
        handle = await env.client.start_workflow(
            OffenseIntake.run, first, id="offense-intake-2", task_queue=CASE_TASK_QUEUE
        )
        await handle.result()
        assert await case.query(CaseStub.state) == [now]

    assert fakes.started == [3, 4]
    assert fakes.closed_records == [("case-2", 2)]
    await replay(await handle.fetch_history())


@workflow.defn(name=CASE_WORKFLOW)
class ReorderedCaseWorkflow:
    """CaseWorkflow as if someone enriched before fetching: a nondeterministic change."""

    @workflow.run
    async def run(self, offense_id: int) -> None:
        await workflow.execute_activity(
            ENRICH_OFFENSE, offense_id, start_to_close_timeout=timedelta(seconds=30)
        )
        await workflow.execute_activity(
            FETCH_OFFENSE, offense_id, start_to_close_timeout=timedelta(seconds=30)
        )


async def test_a_changed_workflow_fails_the_replay(env: WorkflowEnvironment) -> None:
    history = await case_history(env)
    replayer = Replayer(
        workflows=[ReorderedCaseWorkflow],
        data_converter=pydantic_data_converter,
        workflow_runner=UnsandboxedWorkflowRunner(),
    )

    with pytest.raises(workflow.NondeterminismError):
        await replayer.replay_workflow(history)
