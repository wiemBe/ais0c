"""HealthCheck and its Schedule (T-032 criteria 1, 6 and 7).

The workflow runs against fake activities with the real names: the four checks and the syslog
channel and the record on the `soc-batch` queue, `send_email` on the executor's own queue. The
time-skipping test server has no Schedules, so the Schedule tests use Temporal's local
development server in real time.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import activity
from temporalio.client import (
    ScheduleActionExecutionStartWorkflow,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from ais0c_workflows import BATCH_QUEUE_WORKFLOWS, HealthCheck, HealthCheckResult
from ais0c_workflows.health import (
    CHECK_RETRY,
    CHECK_TIMEOUT,
    CHECKS,
    EMAIL_TOTAL_TIMEOUT,
)
from ais0c_workflows.names import (
    BATCH_TASK_QUEUE,
    CHECK_EXECUTOR_WORKER,
    CHECK_INTAKE,
    CHECK_LOG_SOURCES,
    CHECK_WRITE_FAILURES,
    EXECUTOR_TASK_QUEUE,
    HEALTH_CHECK,
    HEALTH_CHECK_SCHEDULE_ID,
    MARK_ALARM_NOTIFIED,
    SEND_ALARM_SYSLOG,
    SEND_EMAIL,
)
from ais0c_workflows.notify import HealthAlarmNotice
from ais0c_workflows.schedules import HEALTH_CHECK_INTERVAL, health_check_schedule

pytestmark = pytest.mark.anyio

OPENED = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
WAIT_SECONDS = 30


def notice(
    kind: str = "log_source_silent", subject: str = "17", **changes: object
) -> HealthAlarmNotice:
    values: dict[str, object] = {
        "alarm_id": "0193a5c2-7b4e-7d1a-8c3f-5e2b9a1d4f60",
        "alarm_kind": kind,
        "subject": subject,
        "subject_name": "FW-DMZ-01",
        "status": "open",
        "notification_no": 1,
        "opened_at": OPENED,
        "counts": {"silent_minutes": 75, "threshold": 60},
    }
    return HealthAlarmNotice.model_validate(values | changes)


type Check = Callable[[int], Awaitable[list[HealthAlarmNotice]]]


async def nothing_due(attempt: int) -> list[HealthAlarmNotice]:
    return []


class Fakes:
    """The activities of one run: `checks` decides each check's answer by name and attempt."""

    def __init__(
        self,
        checks: dict[str, Check] | None = None,
        *,
        email_fails: bool = False,
        syslog_fails: bool = False,
    ) -> None:
        self._checks = checks or {}
        self.email_fails = email_fails
        self.syslog_fails = syslog_fails
        self.calls: list[str] = []
        self.attempts: dict[str, int] = {}
        self.syslog: list[HealthAlarmNotice] = []
        self.emails: list[HealthAlarmNotice] = []
        self.marked: list[str] = []

    async def _check(self, name: str) -> list[HealthAlarmNotice]:
        self.calls.append(name)
        attempt = activity.info().attempt
        self.attempts[name] = attempt
        return await self._checks.get(name, nothing_due)(attempt)

    @activity.defn(name=CHECK_INTAKE)
    async def check_intake(self) -> list[HealthAlarmNotice]:
        return await self._check(CHECK_INTAKE)

    @activity.defn(name=CHECK_LOG_SOURCES)
    async def check_log_sources(self) -> list[HealthAlarmNotice]:
        return await self._check(CHECK_LOG_SOURCES)

    @activity.defn(name=CHECK_WRITE_FAILURES)
    async def check_write_failures(self) -> list[HealthAlarmNotice]:
        return await self._check(CHECK_WRITE_FAILURES)

    @activity.defn(name=CHECK_EXECUTOR_WORKER)
    async def check_executor_worker(self) -> list[HealthAlarmNotice]:
        return await self._check(CHECK_EXECUTOR_WORKER)

    @activity.defn(name=SEND_ALARM_SYSLOG)
    async def send_alarm_syslog(self, alarm: HealthAlarmNotice) -> bool:
        self.calls.append(SEND_ALARM_SYSLOG)
        if self.syslog_fails:
            raise ApplicationError("syslog unreachable", non_retryable=True)
        self.syslog.append(alarm)
        return True

    @activity.defn(name=MARK_ALARM_NOTIFIED)
    async def mark_alarm_notified(self, alarm_id: str) -> bool:
        self.calls.append(MARK_ALARM_NOTIFIED)
        self.marked.append(alarm_id)
        return True

    @activity.defn(name=SEND_EMAIL)
    async def send_email(self, request: HealthAlarmNotice) -> dict[str, object]:
        self.calls.append(SEND_EMAIL)
        if self.email_fails:
            raise ApplicationError("the relay is down")
        self.emails.append(request)
        return {"result": "sent"}

    def batch_activities(self) -> list[Callable[..., object]]:
        return [
            self.check_intake,
            self.check_log_sources,
            self.check_write_failures,
            self.check_executor_worker,
            self.send_alarm_syslog,
            self.mark_alarm_notified,
        ]


@asynccontextmanager
async def workers(
    env: WorkflowEnvironment, fakes: Fakes, *, executor: bool = True
) -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(
            Worker(
                env.client,
                task_queue=BATCH_TASK_QUEUE,
                workflows=list(BATCH_QUEUE_WORKFLOWS),
                activities=fakes.batch_activities(),
            )
        )
        await stack.enter_async_context(
            Worker(
                env.client,
                task_queue=EXECUTOR_TASK_QUEUE,
                workflows=[],
                activities=[fakes.send_email],
            )
        )
        yield


async def run_health(
    env: WorkflowEnvironment, workflow_id: str = "health-test"
) -> HealthCheckResult:
    return await env.client.execute_workflow(
        HealthCheck.run, id=workflow_id, task_queue=BATCH_TASK_QUEUE
    )


async def test_a_run_makes_the_four_checks_in_order_and_has_nothing_to_say(
    env: WorkflowEnvironment,
) -> None:
    fakes = Fakes()
    async with workers(env, fakes):
        result = await run_health(env)

    assert result == HealthCheckResult(checks_failed=[], notified=0)
    assert fakes.calls == [
        CHECK_INTAKE,
        CHECK_LOG_SOURCES,
        CHECK_WRITE_FAILURES,
        CHECK_EXECUTOR_WORKER,
    ]
    assert CHECKS == (CHECK_INTAKE, CHECK_LOG_SOURCES, CHECK_WRITE_FAILURES, CHECK_EXECUTOR_WORKER)


async def test_a_due_notification_goes_out_on_both_channels_and_is_then_recorded(
    env: WorkflowEnvironment,
) -> None:
    due = notice()

    async def silent_log_source(attempt: int) -> list[HealthAlarmNotice]:
        return [due]

    fakes = Fakes({CHECK_LOG_SOURCES: silent_log_source})
    async with workers(env, fakes):
        result = await run_health(env)

    assert result.notified == 1
    assert fakes.syslog == [due]
    assert fakes.emails == [due]
    assert fakes.marked == [due.alarm_id]
    # The record comes last, once both channels were tried.
    assert fakes.calls[-1] == MARK_ALARM_NOTIFIED
    assert set(fakes.calls[-3:-1]) == {SEND_ALARM_SYSLOG, SEND_EMAIL}


async def test_every_due_notification_of_the_run_is_announced(env: WorkflowEnvironment) -> None:
    first = notice(subject="17", alarm_id="0193a5c2-7b4e-7d1a-8c3f-5e2b9a1d4f61")
    second = notice("intake_stopped", "intake", alarm_id="0193a5c2-7b4e-7d1a-8c3f-5e2b9a1d4f62")

    async def one(attempt: int) -> list[HealthAlarmNotice]:
        return [first]

    async def other(attempt: int) -> list[HealthAlarmNotice]:
        return [second]

    fakes = Fakes({CHECK_LOG_SOURCES: one, CHECK_INTAKE: other})
    async with workers(env, fakes):
        result = await run_health(env)

    assert result.notified == 2
    assert {item.alarm_id for item in fakes.emails} == {first.alarm_id, second.alarm_id}
    assert set(fakes.marked) == {first.alarm_id, second.alarm_id}


async def test_a_failing_check_does_not_stop_the_others(env: WorkflowEnvironment) -> None:
    """Criterion 1: the failed check is logged and the other three still run; it is no alarm."""
    due = notice("executor_absent", "soc-executor")

    async def gateway_down(attempt: int) -> list[HealthAlarmNotice]:
        raise ApplicationError("QRadar cannot be read", non_retryable=True)

    async def executor_gone(attempt: int) -> list[HealthAlarmNotice]:
        return [due]

    fakes = Fakes({CHECK_INTAKE: gateway_down, CHECK_EXECUTOR_WORKER: executor_gone})
    async with workers(env, fakes):
        result = await run_health(env)

    assert result.checks_failed == [CHECK_INTAKE]
    assert result.notified == 1
    assert fakes.calls[:4] == [
        CHECK_INTAKE,
        CHECK_LOG_SOURCES,
        CHECK_WRITE_FAILURES,
        CHECK_EXECUTOR_WORKER,
    ]
    assert fakes.emails == [due]


async def test_a_check_is_retried_a_few_times_and_then_given_up(env: WorkflowEnvironment) -> None:
    async def always_down(attempt: int) -> list[HealthAlarmNotice]:
        raise ApplicationError("the gateway is unreachable")

    fakes = Fakes({CHECK_LOG_SOURCES: always_down})
    async with workers(env, fakes):
        result = await run_health(env)

    assert result.checks_failed == [CHECK_LOG_SOURCES]
    assert fakes.attempts[CHECK_LOG_SOURCES] == CHECK_RETRY.maximum_attempts == 3
    # One attempt is bounded, so a run cannot hold up the next start for long.
    assert CHECK_TIMEOUT <= timedelta(minutes=5)


async def test_a_check_that_fails_once_is_retried_and_its_notification_goes_out(
    env: WorkflowEnvironment,
) -> None:
    due = notice()

    async def second_time_lucky(attempt: int) -> list[HealthAlarmNotice]:
        if attempt == 1:
            raise ApplicationError("the gateway is unreachable")
        return [due]

    fakes = Fakes({CHECK_LOG_SOURCES: second_time_lucky})
    async with workers(env, fakes):
        result = await run_health(env)

    assert (result.checks_failed, result.notified) == ([], 1)


async def test_an_executor_that_fails_does_not_stop_syslog_or_the_record(
    env: WorkflowEnvironment,
) -> None:
    """Criterion 7 and T-68 (6): the e-mail cannot go without a working executor (that is the
    alarm of its own kind), the syslog message still goes, the state is recorded, and the run
    ends after the few minutes the e-mail is given instead of the hour a case's e-mail gets."""
    due = notice("executor_absent", "soc-executor")

    async def executor_gone(attempt: int) -> list[HealthAlarmNotice]:
        return [due]

    fakes = Fakes({CHECK_EXECUTOR_WORKER: executor_gone}, email_fails=True)
    async with workers(env, fakes):
        result = await run_health(env)
        history = await env.client.get_workflow_handle("health-test").fetch_history()
    first, last = history.events[0].event_time, history.events[-1].event_time
    elapsed = timedelta(seconds=last.ToSeconds() - first.ToSeconds())

    assert result.notified == 1
    assert fakes.syslog == [due]
    assert fakes.emails == []
    assert fakes.marked == [due.alarm_id]
    # The retries stop where the next one would not fit in the e-mail's few minutes.
    assert timedelta(minutes=1) < elapsed <= EMAIL_TOTAL_TIMEOUT
    # The e-mail call is the executor's activity on the executor's queue, bounded to minutes: with
    # no worker at all (the test server cannot skip time past that) it is given up after this
    # long as well.
    [scheduled] = [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
        and event.activity_task_scheduled_event_attributes.activity_type.name == SEND_EMAIL
    ]
    assert scheduled.task_queue.name == EXECUTOR_TASK_QUEUE
    assert scheduled.schedule_to_close_timeout.ToTimedelta() == EMAIL_TOTAL_TIMEOUT
    assert EMAIL_TOTAL_TIMEOUT < timedelta(hours=1)


async def test_a_failing_email_relay_stops_nothing(env: WorkflowEnvironment) -> None:
    due = notice()

    async def silent_log_source(attempt: int) -> list[HealthAlarmNotice]:
        return [due]

    fakes = Fakes({CHECK_LOG_SOURCES: silent_log_source}, email_fails=True)
    async with workers(env, fakes):
        result = await run_health(env)

    assert result.notified == 1
    assert fakes.syslog == [due]
    assert fakes.marked == [due.alarm_id]


async def test_a_syslog_failure_stops_nothing(env: WorkflowEnvironment) -> None:
    due = notice()

    async def silent_log_source(attempt: int) -> list[HealthAlarmNotice]:
        return [due]

    fakes = Fakes({CHECK_LOG_SOURCES: silent_log_source}, syslog_fails=True)
    async with workers(env, fakes):
        result = await run_health(env)

    assert result.notified == 1
    assert fakes.emails == [due]
    assert fakes.marked == [due.alarm_id]


async def test_histories_replay(env: WorkflowEnvironment) -> None:
    due = notice()

    async def silent_log_source(attempt: int) -> list[HealthAlarmNotice]:
        return [due]

    async def down_once(attempt: int) -> list[HealthAlarmNotice]:
        if attempt == 1:
            raise ApplicationError("the gateway is unreachable")
        return []

    fakes = Fakes({CHECK_LOG_SOURCES: silent_log_source, CHECK_INTAKE: down_once})
    async with workers(env, fakes):
        await run_health(env, "health-replay")
        history = await env.client.get_workflow_handle("health-replay").fetch_history()

    replayer = Replayer(
        workflows=list(BATCH_QUEUE_WORKFLOWS), data_converter=pydantic_data_converter
    )
    await replayer.replay_workflow(history)


# --- the Schedule -----------------------------------------------------------------------------


def test_the_schedule_starts_health_check_every_five_minutes_and_skips_overlaps() -> None:
    schedule = health_check_schedule()

    action = schedule.action
    assert isinstance(action, ScheduleActionStartWorkflow)
    assert (action.workflow, action.id, action.task_queue) == (
        HEALTH_CHECK,
        HEALTH_CHECK_SCHEDULE_ID,
        BATCH_TASK_QUEUE,
    )
    assert HEALTH_CHECK == "HealthCheck"
    assert HEALTH_CHECK_SCHEDULE_ID == "health-check"
    assert schedule.spec.intervals == [ScheduleIntervalSpec(every=timedelta(minutes=5))]
    assert HEALTH_CHECK_INTERVAL == timedelta(minutes=5)
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP


def test_the_interval_can_be_set_and_must_be_positive() -> None:
    schedule = health_check_schedule(every=timedelta(minutes=2))
    assert schedule.spec.intervals == [ScheduleIntervalSpec(every=timedelta(minutes=2))]
    with pytest.raises(ValueError, match="positive"):
        health_check_schedule(every=timedelta(0))


@pytest.fixture
async def dev_server() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env:
        yield env


async def test_a_run_that_is_still_going_makes_temporal_skip_the_next_start(
    dev_server: WorkflowEnvironment,
) -> None:
    """Criterion 1 on a real Temporal: the Schedule runs HealthCheck, and two runs never
    overlap."""
    client = dev_server.client
    started = asyncio.Event()
    release = asyncio.Event()

    async def held(attempt: int) -> list[HealthAlarmNotice]:
        started.set()
        await release.wait()
        return []

    fakes = Fakes({CHECK_INTAKE: held})
    async with workers(dev_server, fakes):
        handle = await client.create_schedule(HEALTH_CHECK_SCHEDULE_ID, health_check_schedule())
        try:
            async with asyncio.timeout(WAIT_SECONDS):
                await handle.trigger()
                await started.wait()
                await handle.trigger()  # a second start while the run is going
                while not (await handle.describe()).info.num_actions_skipped_overlap:  # noqa: ASYNC110 - polls Temporal
                    await asyncio.sleep(0.2)
            release.set()
            [recent] = (await handle.describe()).info.recent_actions
            action = recent.action
            assert isinstance(action, ScheduleActionExecutionStartWorkflow)
            assert action.workflow_id.startswith(f"{HEALTH_CHECK_SCHEDULE_ID}-")
            result = await client.get_workflow_handle(
                action.workflow_id, result_type=HealthCheckResult
            ).result()
            assert result == HealthCheckResult()
        finally:
            release.set()
            await handle.delete()
