"""OffenseIntake: go-live checkpoint, paging, signals and case starts (criteria 1, 2, 5, 7).

The intake runs against fake activities. `CaseStub` stands in for the case workflows under
their `case-<offense_id>` IDs and records the signals it gets.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflow_fakes import CaseStub, IntakeFakes, offense

from ais0c_workflows import IntakeCheckpoint, OffenseIntake
from ais0c_workflows.intake import MAX_PAGES_PER_RUN, PAGE_SIZE
from ais0c_workflows.names import CASE_TASK_QUEUE

pytestmark = pytest.mark.anyio


class Intake:
    def __init__(self, env: WorkflowEnvironment, fakes: IntakeFakes) -> None:
        self.env = env
        self.fakes = fakes
        self.runs = 0

    async def run(self, seed: IntakeCheckpoint | None = None) -> IntakeCheckpoint:
        self.runs += 1
        return await self.env.client.execute_workflow(
            OffenseIntake.run,
            seed,
            id=f"offense-intake-{self.runs}",
            task_queue=CASE_TASK_QUEUE,
        )

    async def open_case(self, offense_id: int) -> WorkflowHandle[CaseStub, list[datetime]]:
        self.fakes.open_cases.add(offense_id)
        return await self.env.client.start_workflow(
            CaseStub.run, id=f"case-{offense_id}", task_queue=CASE_TASK_QUEUE
        )


@asynccontextmanager
async def intake(env: WorkflowEnvironment, fakes: IntakeFakes) -> AsyncIterator[Intake]:
    async with Worker(
        env.client,
        task_queue=CASE_TASK_QUEUE,
        workflows=[OffenseIntake, CaseStub],
        activities=fakes.activities(),
    ):
        yield Intake(env, fakes)


async def test_first_run_sets_go_live_and_older_offenses_are_never_processed(
    env: WorkflowEnvironment,
) -> None:
    t0 = await env.get_current_time()
    fakes = IntakeFakes()
    fakes.put(
        offense(1, start=t0 - timedelta(hours=2)),
        offense(2, start=t0 - timedelta(hours=1)),
    )

    async with intake(env, fakes) as runner:
        first = await runner.run()
        assert t0 <= first.go_live_at <= await env.get_current_time()
        assert (first.last_updated_time, first.last_offense_id) == (first.go_live_at, 0)
        assert fakes.admitted == []

        # Offense 2 started before go-live and changes after it; offense 3 starts after it.
        await env.sleep(timedelta(minutes=10))
        t1 = await env.get_current_time()
        fakes.put(
            offense(2, start=t0 - timedelta(hours=1), updated=t1 - timedelta(minutes=5)),
            offense(3, start=t1 - timedelta(minutes=4)),
        )
        second = await runner.run(first)

    assert fakes.admitted == [[3]]
    assert second.go_live_at == first.go_live_at
    assert (second.last_updated_time, second.last_offense_id) == (t1 - timedelta(minutes=4), 3)


async def test_each_run_continues_from_the_previous_checkpoint(env: WorkflowEnvironment) -> None:
    fakes = IntakeFakes()
    async with intake(env, fakes) as runner:
        first = await runner.run()
        await env.sleep(timedelta(minutes=5))
        now = await env.get_current_time()
        fakes.put(offense(10, start=now - timedelta(minutes=2)), offense(11, start=now))
        second = await runner.run(first)
        third = await runner.run(second)

    assert fakes.admitted == [[10, 11]]
    assert third == second


async def test_pages_through_every_change(env: WorkflowEnvironment) -> None:
    fakes = IntakeFakes()
    async with intake(env, fakes) as runner:
        first = await runner.run()
        await env.sleep(timedelta(hours=1))
        now = await env.get_current_time()
        count = PAGE_SIZE * MAX_PAGES_PER_RUN + 20
        fakes.put(*(offense(n, start=now - timedelta(seconds=count - n)) for n in range(count)))
        second = await runner.run(first)
        third = await runner.run(second)

    pages = [len(page) for page in fakes.admitted]
    assert pages == [PAGE_SIZE] * MAX_PAGES_PER_RUN + [20]
    assert [n for page in fakes.admitted for n in page] == list(range(count))
    assert second.last_offense_id == PAGE_SIZE * MAX_PAGES_PER_RUN - 1
    assert third.last_offense_id == count - 1


async def test_a_changed_offense_with_an_open_case_gets_offense_updated(
    env: WorkflowEnvironment,
) -> None:
    fakes = IntakeFakes()
    async with intake(env, fakes) as runner:
        first = await runner.run()
        case = await runner.open_case(20)
        fakes.open_cases.add(21)  # recorded as open, but its workflow is gone
        await env.sleep(timedelta(minutes=5))
        now = await env.get_current_time()
        fakes.put(
            offense(20, start=now - timedelta(minutes=4), updated=now - timedelta(minutes=1)),
            offense(21, start=now - timedelta(minutes=3), updated=now),
        )
        await runner.run(first)
        updates = await case.query(CaseStub.state)

    assert updates == [now - timedelta(minutes=1)]


async def test_a_closed_offense_ends_its_case(env: WorkflowEnvironment) -> None:
    fakes = IntakeFakes()
    async with intake(env, fakes) as runner:
        case = await runner.open_case(30)
        fakes.open_cases.add(31)  # recorded as open, but its workflow is gone
        fakes.closed.update({30, 31})
        await runner.run()
        await case.result()

    # The records of the case whose workflow is gone are closed by the intake itself.
    assert fakes.closed_records == [("case-31", 31)]


async def test_pending_cases_start_in_the_order_given(env: WorkflowEnvironment) -> None:
    fakes = IntakeFakes()
    fakes.pending = [7, 3, 5]
    async with intake(env, fakes) as runner:
        await runner.run()

    assert fakes.started == [7, 3, 5]
