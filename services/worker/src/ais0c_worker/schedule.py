"""The Temporal Schedules the workers create at start-up (architecture §6).

The intake's Schedule is next to the case worker; the periodic workflows' Schedules are defined
with the workflows they start (`ais0c_workflows.schedules`), which says what one Schedule is.
Creating it, or updating it in place, is the worker's business and is here.

The intake checkpoint lives in its Schedule: every run starts from the result of the previous
successful run. Updating the Schedule keeps it. Deleting the Schedule loses it, and the next run
is a first run again with a new go-live time (D-26).
"""

import dataclasses
from datetime import timedelta
from typing import Final

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleHandle,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from ais0c_workflows.names import (
    CASE_TASK_QUEUE,
    KNOWLEDGE_SYNC_SCHEDULE_ID,
    OFFENSE_INTAKE,
)
from ais0c_workflows.schedules import KNOWLEDGE_SYNC_HOUR, knowledge_sync_schedule

INTAKE_SCHEDULE_ID: Final = "offense-intake"
INTAKE_INTERVAL: Final = timedelta(minutes=1)


def intake_schedule(every: timedelta = INTAKE_INTERVAL) -> Schedule:
    """Start OffenseIntake every `every`; while a run is still going, the next one is skipped."""
    return Schedule(
        action=ScheduleActionStartWorkflow(
            OFFENSE_INTAKE, id=INTAKE_SCHEDULE_ID, task_queue=CASE_TASK_QUEUE
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=every)]),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_intake_schedule(
    client: Client, *, every: timedelta = INTAKE_INTERVAL
) -> ScheduleHandle:
    """Create the intake Schedule, or update the existing one in place.

    An update changes the action, interval and policy; it keeps the Schedule's state (paused or
    not) and its checkpoint.
    """
    return await _ensure_schedule(client, INTAKE_SCHEDULE_ID, intake_schedule(every))


async def ensure_knowledge_sync_schedule(
    client: Client, *, hour: int = KNOWLEDGE_SYNC_HOUR
) -> ScheduleHandle:
    """Create the Schedule that starts KnowledgeSync on the `soc-batch` queue, or update the
    existing one in place, keeping its state.

    The Schedule is the one `ais0c_workflows.schedules.knowledge_sync_schedule` describes: every
    day at `hour`:00 SOC time, a run that is still going makes Temporal skip the next start.
    """
    return await _ensure_schedule(
        client, KNOWLEDGE_SYNC_SCHEDULE_ID, knowledge_sync_schedule(hour=hour)
    )


async def _ensure_schedule(client: Client, schedule_id: str, schedule: Schedule) -> ScheduleHandle:
    """`schedule` under `schedule_id`, created or updated in place; the state is kept."""
    try:
        return await client.create_schedule(schedule_id, schedule)
    except ScheduleAlreadyRunningError:
        pass

    def update(current: ScheduleUpdateInput) -> ScheduleUpdate:
        state = current.description.schedule.state
        return ScheduleUpdate(schedule=dataclasses.replace(schedule, state=state))

    handle = client.get_schedule_handle(schedule_id)
    await handle.update(update)
    return handle
