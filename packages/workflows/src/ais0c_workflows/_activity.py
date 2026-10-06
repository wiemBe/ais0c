"""Calling activities by name from workflow code."""

from datetime import timedelta
from typing import Final

from temporalio import workflow
from temporalio.common import RetryPolicy

# Database work.
STORE_TIMEOUT: Final = timedelta(seconds=30)
# Reads from the offense source: QRadar through the MCP Policy Gateway. One read is a few gateway
# calls, and each may wait for its quota pool (up to 60 seconds) before its MCP call (up to 60).
SOURCE_TIMEOUT: Final = timedelta(minutes=5)


async def call[T](
    name: str,
    *args: object,
    result_type: type[T],
    attempt_timeout: timedelta = STORE_TIMEOUT,
    total_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    task_queue: str | None = None,
    cancellation_type: workflow.ActivityCancellationType = (
        workflow.ActivityCancellationType.TRY_CANCEL
    ),
) -> T:
    """Run activity `name` and return its result as `result_type`.

    `attempt_timeout` bounds one attempt; with `heartbeat_timeout`, an attempt that goes that
    long without a heartbeat fails too. `total_timeout` bounds the call from the moment it is
    scheduled, waiting for a worker and retries included. Without `retry_policy` the activity is
    retried until it succeeds (Temporal's default), so an outage delays the workflow instead of
    failing it. `task_queue` names a queue other than the workflow's own, such as the
    executor's (T-045).
    """
    result: T = await workflow.execute_activity(
        name,
        args=list(args),
        result_type=result_type,
        task_queue=task_queue,
        start_to_close_timeout=attempt_timeout,
        schedule_to_close_timeout=total_timeout,
        heartbeat_timeout=heartbeat_timeout,
        retry_policy=retry_policy,
        cancellation_type=cancellation_type,
    )
    return result
