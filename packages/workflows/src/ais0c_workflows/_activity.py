"""Calling activities by name from workflow code."""

from datetime import timedelta
from typing import Final

from temporalio import workflow
from temporalio.common import RetryPolicy

# Database work.
STORE_TIMEOUT: Final = timedelta(seconds=30)
# Reads from the offense source: QRadar through the gateway from T-012 on.
SOURCE_TIMEOUT: Final = timedelta(minutes=1)


async def call[T](
    name: str,
    *args: object,
    result_type: type[T],
    attempt_timeout: timedelta = STORE_TIMEOUT,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: workflow.ActivityCancellationType = (
        workflow.ActivityCancellationType.TRY_CANCEL
    ),
) -> T:
    """Run activity `name` and return its result as `result_type`.

    `attempt_timeout` bounds one attempt. Without `retry_policy` the activity is retried until
    it succeeds (Temporal's default), so an outage delays the workflow instead of failing it.
    """
    result: T = await workflow.execute_activity(
        name,
        args=list(args),
        result_type=result_type,
        start_to_close_timeout=attempt_timeout,
        retry_policy=retry_policy,
        cancellation_type=cancellation_type,
    )
    return result
