"""KnowledgeSync: the daily catalog sync and its Schedule (T-022 criterion 2).

The workflow runs against a fake `sync_analysis_catalog` on the time-skipping test server. That
server has no Schedules, so the Schedule tests use Temporal's local development server in real
time: the Schedule's next starts are read from Temporal, and runs are triggered by hand.
"""

import asyncio
import itertools
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest
from temporalio import activity
from temporalio.client import (
    Client,
    ScheduleActionExecutionStartWorkflow,
    ScheduleActionStartWorkflow,
    ScheduleHandle,
    ScheduleOverlapPolicy,
    ScheduleRange,
    WorkflowFailureError,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from ais0c_knowledge.catalog import CatalogSyncReport
from ais0c_workflows import BATCH_QUEUE_WORKFLOWS, KnowledgeSync, KnowledgeSyncResult
from ais0c_workflows.knowledge_sync import (
    CATALOG_SYNC_HEARTBEAT,
    CATALOG_SYNC_RETRY,
    CATALOG_SYNC_TIMEOUT,
)
from ais0c_workflows.names import (
    BATCH_TASK_QUEUE,
    KNOWLEDGE_SYNC,
    KNOWLEDGE_SYNC_SCHEDULE_ID,
    SYNC_ANALYSIS_CATALOG,
)
from ais0c_workflows.schedules import (
    KNOWLEDGE_SYNC_HOUR,
    SOC_TIME_ZONE,
    knowledge_sync_schedule,
)

pytestmark = pytest.mark.anyio

COUNTS = CatalogSyncReport(
    rules=134,
    log_sources=51,
    rules_added=(100001, 100002),
    rules_changed=(100003,),
    rules_missing=(100004,),
    rules_marked_missing=(100004,),
    rules_returned=(100005,),
    log_sources_missing=(200001,),
    log_sources_marked_missing=(200001,),
    log_sources_returned=(200002,),
).counts()
WAIT_SECONDS = 30


class FakeSync:
    """`sync_analysis_catalog`: `behavior` decides each attempt by its number."""

    def __init__(self, behavior: Callable[[int], Awaitable[dict[str, int]]] | None = None) -> None:
        self.attempts = 0
        self._behavior = behavior

    @activity.defn(name=SYNC_ANALYSIS_CATALOG)
    async def sync_analysis_catalog(self) -> dict[str, int]:
        self.attempts += 1
        if self._behavior is None:
            return COUNTS
        return await self._behavior(activity.info().attempt)


@asynccontextmanager
async def batch_worker(client: Client, fake: FakeSync) -> AsyncIterator[None]:
    async with Worker(
        client,
        task_queue=BATCH_TASK_QUEUE,
        workflows=list(BATCH_QUEUE_WORKFLOWS),
        activities=[fake.sync_analysis_catalog],
    ):
        yield


async def run_sync(
    env: WorkflowEnvironment, workflow_id: str = "knowledge-sync-test"
) -> KnowledgeSyncResult:
    return await env.client.execute_workflow(
        KnowledgeSync.run, id=workflow_id, task_queue=BATCH_TASK_QUEUE
    )


async def test_a_run_syncs_the_catalog_and_returns_its_counts(env: WorkflowEnvironment) -> None:
    fake = FakeSync()
    async with batch_worker(env.client, fake):
        result = await run_sync(env)

    assert result == KnowledgeSyncResult(catalog=COUNTS)
    assert fake.attempts == 1


async def test_the_sync_runs_with_a_timeout_a_heartbeat_and_a_few_retries(
    env: WorkflowEnvironment,
) -> None:
    async with batch_worker(env.client, FakeSync()):
        await run_sync(env)
        history = await env.client.get_workflow_handle("knowledge-sync-test").fetch_history()

    [scheduled] = [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert scheduled.activity_type.name == SYNC_ANALYSIS_CATALOG
    assert scheduled.task_queue.name == BATCH_TASK_QUEUE
    assert scheduled.start_to_close_timeout.ToTimedelta() == CATALOG_SYNC_TIMEOUT
    assert scheduled.heartbeat_timeout.ToTimedelta() == CATALOG_SYNC_HEARTBEAT
    assert scheduled.retry_policy.maximum_attempts == CATALOG_SYNC_RETRY.maximum_attempts == 5
    assert CATALOG_SYNC_HEARTBEAT < CATALOG_SYNC_TIMEOUT


async def test_a_failed_attempt_is_retried(env: WorkflowEnvironment) -> None:
    async def gateway_down_twice(attempt: int) -> dict[str, int]:
        if attempt <= 2:
            raise RuntimeError("the gateway cannot be reached")
        return COUNTS

    fake = FakeSync(gateway_down_twice)
    async with batch_worker(env.client, fake):
        result = await run_sync(env)

    assert result.catalog == COUNTS
    assert fake.attempts == 3


async def test_a_sync_that_keeps_failing_ends_the_run_as_failed(env: WorkflowEnvironment) -> None:
    async def gateway_down(attempt: int) -> dict[str, int]:
        raise RuntimeError("the gateway cannot be reached")

    fake = FakeSync(gateway_down)
    async with batch_worker(env.client, fake):
        with pytest.raises(WorkflowFailureError) as raised:
            await run_sync(env)

    assert isinstance(raised.value.cause, ActivityError)
    assert fake.attempts == 5


async def test_unreadable_qradar_data_is_not_retried(env: WorkflowEnvironment) -> None:
    async def unreadable(attempt: int) -> dict[str, int]:
        raise ApplicationError(
            "list_rules returned a row that cannot be read; check name",
            type="InventoryUnreadable",
            non_retryable=True,
        )

    fake = FakeSync(unreadable)
    async with batch_worker(env.client, fake):
        with pytest.raises(WorkflowFailureError) as raised:
            await run_sync(env)

    cause = raised.value.cause
    assert isinstance(cause, ActivityError)
    assert isinstance(cause.cause, ApplicationError)
    assert cause.cause.type == "InventoryUnreadable"
    assert fake.attempts == 1


async def test_histories_replay(env: WorkflowEnvironment) -> None:
    async def down_once(attempt: int) -> dict[str, int]:
        if attempt == 1:
            raise RuntimeError("the gateway cannot be reached")
        return COUNTS

    async with batch_worker(env.client, FakeSync(down_once)):
        await run_sync(env, "knowledge-sync-replay")
        history = await env.client.get_workflow_handle("knowledge-sync-replay").fetch_history()

    replayer = Replayer(
        workflows=list(BATCH_QUEUE_WORKFLOWS), data_converter=pydantic_data_converter
    )
    await replayer.replay_workflow(history)


def test_the_schedule_starts_knowledge_sync_daily_at_three_soc_time() -> None:
    schedule = knowledge_sync_schedule()

    action = schedule.action
    assert isinstance(action, ScheduleActionStartWorkflow)
    assert (action.workflow, action.id, action.task_queue) == (
        KNOWLEDGE_SYNC,
        KNOWLEDGE_SYNC_SCHEDULE_ID,
        BATCH_TASK_QUEUE,
    )
    [calendar] = schedule.spec.calendars
    assert (list(calendar.hour), list(calendar.minute), list(calendar.second)) == (
        [ScheduleRange(KNOWLEDGE_SYNC_HOUR)],
        [ScheduleRange(0)],
        [ScheduleRange(0)],
    )
    assert KNOWLEDGE_SYNC_HOUR == 3
    assert schedule.spec.time_zone_name == SOC_TIME_ZONE == "Europe/Istanbul"
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP


@pytest.mark.parametrize("hour", [-1, 24])
def test_the_schedule_hour_is_an_hour_of_the_day(hour: int) -> None:
    with pytest.raises(ValueError, match="between 0 and 23"):
        knowledge_sync_schedule(hour=hour)


@pytest.fixture
async def dev_server() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env:
        yield env


async def started_runs(client: Client, handle: ScheduleHandle) -> list[str]:
    """Workflow IDs of the Schedule's runs so far."""
    runs: list[str] = []
    for result in (await handle.describe()).info.recent_actions:
        started = result.action
        assert isinstance(started, ScheduleActionExecutionStartWorkflow)
        runs.append(started.workflow_id)
    return runs


async def test_the_schedule_runs_daily_and_a_run_by_hand_never_overlaps(
    dev_server: WorkflowEnvironment,
) -> None:
    """Criterion 2 on a real Temporal: daily starts at 03:00 Istanbul time, and a trigger by
    hand runs KnowledgeSync at once unless a run is still going."""
    client = dev_server.client
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_sync(attempt: int) -> dict[str, int]:
        started.set()
        await release.wait()
        return COUNTS

    async with batch_worker(client, FakeSync(slow_sync)):
        handle = await client.create_schedule(KNOWLEDGE_SYNC_SCHEDULE_ID, knowledge_sync_schedule())
        try:
            next_starts = (await handle.describe()).info.next_action_times
            local = [start.astimezone(ZoneInfo(SOC_TIME_ZONE)) for start in next_starts]
            assert len(local) >= 2
            assert {start.time() for start in local} == {time(3, 0)}
            assert {b - a for a, b in itertools.pairwise(local)} == {timedelta(days=1)}

            async with asyncio.timeout(WAIT_SECONDS):
                await handle.trigger()
                await started.wait()
                # A second trigger while the run is going is skipped.
                await handle.trigger()
                while True:
                    if (await handle.describe()).info.num_actions_skipped_overlap:
                        break
                    await asyncio.sleep(0.2)
            release.set()

            [workflow_id] = await started_runs(client, handle)
            assert workflow_id.startswith(f"{KNOWLEDGE_SYNC_SCHEDULE_ID}-")
            result = await client.get_workflow_handle(
                workflow_id, result_type=KnowledgeSyncResult
            ).result()
            assert result == KnowledgeSyncResult(catalog=COUNTS)
        finally:
            release.set()
            await handle.delete()
