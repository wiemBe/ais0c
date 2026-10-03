"""The intake Schedule carries the checkpoint from run to run (criterion 1 under a Schedule).

The time-skipping test server has no Schedules, so this test uses Temporal's local development
server in real time, with an interval of one second. The intake activities are no-ops: the test
is about the checkpoint, not about offenses.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta

import pytest
from temporalio import activity
from temporalio.client import (
    Client,
    ScheduleActionExecutionStartWorkflow,
    ScheduleHandle,
    ScheduleOverlapPolicy,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ais0c_contracts import OffenseSnapshot
from ais0c_worker import (
    INTAKE_INTERVAL,
    INTAKE_SCHEDULE_ID,
    ensure_intake_schedule,
    intake_schedule,
)
from ais0c_workflows import IntakeCheckpoint, OffenseIntake
from ais0c_workflows.names import (
    ADMIT_OFFENSES,
    CASE_TASK_QUEUE,
    CLOSE_CASE,
    FETCH_OFFENSE_CHANGES,
    FIND_CLOSED_OFFENSES,
    NEXT_PENDING_OFFENSES,
    OFFENSE_INTAKE,
    START_CASE,
)

pytestmark = pytest.mark.anyio

EVERY = timedelta(seconds=1)
WAIT_SECONDS = 30


@activity.defn(name=FETCH_OFFENSE_CHANGES)
async def no_changes(after_time: datetime, after_id: int, limit: int) -> list[OffenseSnapshot]:
    return []


@activity.defn(name=ADMIT_OFFENSES)
async def admit_nothing(offenses: list[OffenseSnapshot], now: datetime) -> list[int]:
    return []


@activity.defn(name=FIND_CLOSED_OFFENSES)
async def nothing_closed() -> list[int]:
    return []


@activity.defn(name=NEXT_PENDING_OFFENSES)
async def nothing_pending() -> list[int]:
    return []


@activity.defn(name=START_CASE)
async def start_nothing(offense_id: int) -> bool:
    return False


@activity.defn(name=CLOSE_CASE)
async def close_nothing(case_id: str, offense_id: int) -> None:
    return None


NO_OP_ACTIVITIES: list[Callable[..., object]] = [
    no_changes,
    admit_nothing,
    nothing_closed,
    nothing_pending,
    start_nothing,
    close_nothing,
]


@pytest.fixture
async def dev_server() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env:
        yield env


async def completed_runs(
    client: Client, handle: ScheduleHandle, count: int
) -> list[IntakeCheckpoint]:
    """Results of the Schedule's runs, oldest first, once at least `count` have finished."""
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            results: list[IntakeCheckpoint] = []
            for action in (await handle.describe()).info.recent_actions:
                started = action.action
                assert isinstance(started, ScheduleActionExecutionStartWorkflow)
                run = client.get_workflow_handle(
                    started.workflow_id,
                    run_id=started.first_execution_run_id,
                    result_type=IntakeCheckpoint,
                )
                description = await run.describe()
                if description.status is not None and description.status.name == "COMPLETED":
                    results.append(await run.result())
            if len(results) >= count:
                return results
            await asyncio.sleep(0.2)


def test_the_schedule_starts_the_intake_on_the_case_queue() -> None:
    schedule = intake_schedule()

    assert INTAKE_INTERVAL == timedelta(minutes=1)
    assert schedule.spec.intervals[0].every == INTAKE_INTERVAL
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
    action = schedule.action
    assert getattr(action, "workflow", None) == OFFENSE_INTAKE
    assert getattr(action, "task_queue", None) == CASE_TASK_QUEUE


async def test_scheduled_runs_keep_the_first_runs_go_live(dev_server: WorkflowEnvironment) -> None:
    client = dev_server.client
    async with Worker(
        client,
        task_queue=CASE_TASK_QUEUE,
        workflows=[OffenseIntake],
        activities=NO_OP_ACTIVITIES,
    ):
        handle = await ensure_intake_schedule(client, every=EVERY)
        assert handle.id == INTAKE_SCHEDULE_ID
        before_update = await completed_runs(client, handle, 3)

        # Updating the Schedule in place keeps its checkpoint and its paused state.
        await handle.pause()
        await ensure_intake_schedule(client, every=EVERY)
        assert (await handle.describe()).schedule.state.paused is True
        await handle.unpause()
        after_update = await completed_runs(client, handle, len(before_update) + 2)
        await handle.delete()

    go_live = {run.go_live_at for run in after_update}
    assert go_live == {before_update[0].go_live_at}
    assert before_update[0].last_updated_time == before_update[0].go_live_at
