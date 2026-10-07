"""Temporal Schedules of the periodic workflows (architecture §6).

A function here says what one Schedule is; the worker that runs the workflow creates the
Schedule, or updates it in place, at start-up. The intake's Schedule is still defined next to
the case worker (`ais0c_worker.schedule`).

The module holds no workflow code and is not imported by any: building a Schedule is plain
data, and only the worker sends it to Temporal.
"""

from datetime import timedelta
from typing import Final

from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleCalendarSpec,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
)

from ais0c_workflows.names import (
    BATCH_TASK_QUEUE,
    HEALTH_CHECK,
    HEALTH_CHECK_SCHEDULE_ID,
    KNOWLEDGE_SYNC,
    KNOWLEDGE_SYNC_SCHEDULE_ID,
)

# The SOC's local time, as for the hunt pool's hours (config/connectors/qradar.yaml).
SOC_TIME_ZONE: Final = "Europe/Istanbul"
# KnowledgeSync runs at night, so the morning shift finds new rules and log sources in the
# catalog's undefined list.
KNOWLEDGE_SYNC_HOUR: Final = 3
# HealthCheck runs this often unless `AIS0C_HEALTH_INTERVAL_MINUTES` says otherwise (T-68).
HEALTH_CHECK_INTERVAL: Final = timedelta(minutes=5)


def knowledge_sync_schedule(*, hour: int = KNOWLEDGE_SYNC_HOUR) -> Schedule:
    """Start KnowledgeSync on the `soc-batch` queue every day at `hour`:00, SOC time.

    While a run is still going, the next start is skipped, whether it is due or triggered by
    hand (`ScheduleHandle.trigger`). Temporal names each run `knowledge-sync-<start time>`.
    """
    if not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    return Schedule(
        action=ScheduleActionStartWorkflow(
            KNOWLEDGE_SYNC, id=KNOWLEDGE_SYNC_SCHEDULE_ID, task_queue=BATCH_TASK_QUEUE
        ),
        spec=ScheduleSpec(
            calendars=[ScheduleCalendarSpec(hour=[ScheduleRange(hour)])],
            time_zone_name=SOC_TIME_ZONE,
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


def health_check_schedule(*, every: timedelta = HEALTH_CHECK_INTERVAL) -> Schedule:
    """Start HealthCheck on the `soc-batch` queue every `every`.

    While a run is still going, the next start is skipped, so two runs never reconcile the
    same alarms at once. Temporal names each run `health-check-<start time>`.
    """
    if every <= timedelta(0):
        raise ValueError("every must be positive")
    return Schedule(
        action=ScheduleActionStartWorkflow(
            HEALTH_CHECK, id=HEALTH_CHECK_SCHEDULE_ID, task_queue=BATCH_TASK_QUEUE
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=every)]),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
