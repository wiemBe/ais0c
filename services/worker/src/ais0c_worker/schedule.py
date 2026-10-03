"""The Temporal Schedule that runs OffenseIntake (architecture §6).

The intake checkpoint lives in the Schedule: every run starts from the result of the previous
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

from ais0c_workflows.names import CASE_TASK_QUEUE, OFFENSE_INTAKE

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
    schedule = intake_schedule(every)
    try:
        return await client.create_schedule(INTAKE_SCHEDULE_ID, schedule)
    except ScheduleAlreadyRunningError:
        pass

    def update(current: ScheduleUpdateInput) -> ScheduleUpdate:
        state = current.description.schedule.state
        return ScheduleUpdate(schedule=dataclasses.replace(schedule, state=state))

    handle = client.get_schedule_handle(INTAKE_SCHEDULE_ID)
    await handle.update(update)
    return handle
