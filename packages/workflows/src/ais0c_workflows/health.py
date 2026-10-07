"""HealthCheck: the platform's minimal health alarms (architecture §26, decisions T-23, T-68;
T-032).

A Temporal Schedule starts a run every few minutes on the `soc-batch` task queue
(`ais0c_workflows.schedules`), so the alarms work when the case worker is down. A run:

1. Runs the four checks, one after the other: intake, log sources, note and e-mail failures and
   the executor worker. Each is an activity that looks at one thing, updates the alarms of its
   kind in `health_alarms` and returns the notifications that are due (a new alarm, a reminder of
   one that stays open, a resolved one). A check that fails, after a few attempts, is logged and
   the others still run; a failing check is not an alarm itself.
2. Announces every due notification on both channels at the same time: syslog to QRadar
   (`send_alarm_syslog`) and the executor's `send_email` on the `soc-executor` queue. A failure
   of either is logged and stops nothing: syslog works without the executor, and the executor
   being away is exactly one of the alarms. The e-mail is given a few minutes, not the hour a
   case's e-mail gets, so the run ends.
3. Records that the notification went out (`mark_alarm_notified`) whether or not a channel
   failed; the alarm's state is what the run keeps.

Runs never overlap: the Schedule skips a start while one is going.
"""

import asyncio
from datetime import timedelta
from typing import Final

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from ais0c_workflows._activity import call
from ais0c_workflows.names import (
    CHECK_EXECUTOR_WORKER,
    CHECK_INTAKE,
    CHECK_LOG_SOURCES,
    CHECK_WRITE_FAILURES,
    EXECUTOR_TASK_QUEUE,
    HEALTH_CHECK,
    MARK_ALARM_NOTIFIED,
    SEND_ALARM_SYSLOG,
    SEND_EMAIL,
)

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, ConfigDict, Field

    from ais0c_workflows.notify import HealthAlarmNotice

# The checks, in the order a run makes them.
CHECKS: Final = (CHECK_INTAKE, CHECK_LOG_SOURCES, CHECK_WRITE_FAILURES, CHECK_EXECUTOR_WORKER)
# One attempt of a check: QRadar reads through the gateway, a page at a time for the log sources.
CHECK_TIMEOUT: Final = timedelta(minutes=5)
CHECK_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    maximum_attempts=3,
    maximum_interval=timedelta(minutes=1),
)
# Syslog is a datagram or one short TCP exchange.
SYSLOG_TIMEOUT: Final = timedelta(seconds=30)
SYSLOG_RETRY: Final = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=2)
# The alarm's e-mail: the relay may be slow, but the executor being away is an alarm of its own,
# so the run waits a few minutes for it and no longer.
EMAIL_ATTEMPT_TIMEOUT: Final = timedelta(minutes=1)
EMAIL_TOTAL_TIMEOUT: Final = timedelta(minutes=3)
EMAIL_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(seconds=10), maximum_interval=timedelta(seconds=60)
)


class HealthCheckResult(BaseModel):
    """What a run did."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checks_failed: list[str] = Field(default_factory=list[str])
    """The checks that failed after their attempts; the others ran."""
    notified: int = 0
    """How many notifications the run announced (an e-mail or a syslog message that failed
    still counts: the state was recorded)."""


@workflow.defn(name=HEALTH_CHECK)
class HealthCheck:
    @workflow.run
    async def run(self) -> HealthCheckResult:
        due: list[HealthAlarmNotice] = []
        failed: list[str] = []
        for check in CHECKS:
            try:
                due.extend(
                    await call(
                        check,
                        result_type=list[HealthAlarmNotice],
                        attempt_timeout=CHECK_TIMEOUT,
                        retry_policy=CHECK_RETRY,
                    )
                )
            except ActivityError as error:
                workflow.logger.warning("health check %s failed: %s", check, error)
                failed.append(check)
        await asyncio.gather(*(self._announce(notice) for notice in due))
        return HealthCheckResult(checks_failed=failed, notified=len(due))

    async def _announce(self, notice: HealthAlarmNotice) -> None:
        """Both channels at once, then the record that the notification went out."""
        await asyncio.gather(self._syslog(notice), self._email(notice))
        try:
            await call(MARK_ALARM_NOTIFIED, notice.alarm_id, result_type=bool)
        except ActivityError as error:
            workflow.logger.warning(
                "health alarm %s: the notification could not be recorded: %s",
                notice.alarm_id,
                error,
            )

    async def _syslog(self, notice: HealthAlarmNotice) -> None:
        try:
            sent = await call(
                SEND_ALARM_SYSLOG,
                notice,
                result_type=bool,
                attempt_timeout=SYSLOG_TIMEOUT,
                retry_policy=SYSLOG_RETRY,
            )
        except ActivityError as error:
            workflow.logger.warning("health alarm %s: syslog failed: %s", notice.alarm_id, error)
            return
        if not sent:
            workflow.logger.info("health alarm %s: syslog is off or failed", notice.alarm_id)

    async def _email(self, notice: HealthAlarmNotice) -> None:
        try:
            await call(
                SEND_EMAIL,
                notice,
                result_type=dict[str, object],
                attempt_timeout=EMAIL_ATTEMPT_TIMEOUT,
                total_timeout=EMAIL_TOTAL_TIMEOUT,
                retry_policy=EMAIL_RETRY,
                task_queue=EXECUTOR_TASK_QUEUE,
            )
        except ActivityError as error:
            workflow.logger.warning("health alarm %s: e-mail failed: %s", notice.alarm_id, error)
