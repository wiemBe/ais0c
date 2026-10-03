"""OffenseIntake: one polling pass over the offense source (architecture §6, §9).

A Temporal Schedule starts a run every short interval (services/worker). A run:

1. Continues from the checkpoint the previous successful run returned. The first run has none,
   so it sets the go-live time to its own start; offenses that started before it are never
   processed (D-26).
2. Pages through the offenses changed since the checkpoint and admits them: repeat check,
   Analysis Catalog, grouping and pre-priority (`admit_offenses`). An open case whose offense
   changed gets `offense_updated`.
3. Sends `offense_closed` to the cases of offenses closed in QRadar.
4. Starts the pending cases that fit under the concurrent case limit, highest pre-priority
   first.
5. Returns the new checkpoint; the Schedule hands it to the next run.
"""

import asyncio
from datetime import datetime
from typing import Final, Self

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from ais0c_workflows._activity import SOURCE_TIMEOUT, call
from ais0c_workflows.names import (
    ADMIT_OFFENSES,
    CLOSE_CASE,
    FETCH_OFFENSE_CHANGES,
    FIND_CLOSED_OFFENSES,
    NEXT_PENDING_OFFENSES,
    OFFENSE_CLOSED,
    OFFENSE_INTAKE,
    OFFENSE_UPDATED,
    START_CASE,
    case_workflow_id,
)

with workflow.unsafe.imports_passed_through():
    from pydantic import AwareDatetime, BaseModel, ConfigDict

    from ais0c_contracts import OffenseSnapshot

PAGE_SIZE: Final = 50
MAX_PAGES_PER_RUN: Final = 10
# Error type of a signal sent to a workflow that does not exist or has finished.
WORKFLOW_NOT_FOUND: Final = "ExternalWorkflowExecutionNotFound"


class IntakeCheckpoint(BaseModel):
    """Where the intake stands. Every run returns one; the next run continues from it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Offenses that started before this are never processed (D-26).
    go_live_at: AwareDatetime
    # Position of the last offense handled, in (last_updated_time, offense_id) order.
    last_updated_time: AwareDatetime
    last_offense_id: int

    @classmethod
    def go_live(cls, now: datetime) -> Self:
        return cls(go_live_at=now, last_updated_time=now, last_offense_id=0)

    def past(self, offense: OffenseSnapshot) -> Self:
        """The checkpoint moved past `offense`, unless it is already further on."""
        position = (offense.last_updated_time, offense.offense_id)
        if position <= (self.last_updated_time, self.last_offense_id):
            return self
        return self.model_copy(
            update={
                "last_updated_time": offense.last_updated_time,
                "last_offense_id": offense.offense_id,
            }
        )


@workflow.defn(name=OFFENSE_INTAKE)
class OffenseIntake:
    @workflow.run
    async def run(self, seed: IntakeCheckpoint | None = None) -> IntakeCheckpoint:
        """`seed` is a starting checkpoint for a run that has no previous result, such as a run
        started by hand; under the Schedule the previous run's result always wins."""
        checkpoint = await self._admit_changes(_starting_checkpoint(seed))
        await self._close_cases()
        await self._start_cases()
        return checkpoint

    async def _admit_changes(self, checkpoint: IntakeCheckpoint) -> IntakeCheckpoint:
        for _ in range(MAX_PAGES_PER_RUN):
            page = await call(
                FETCH_OFFENSE_CHANGES,
                checkpoint.last_updated_time,
                checkpoint.last_offense_id,
                PAGE_SIZE,
                result_type=list[OffenseSnapshot],
                attempt_timeout=SOURCE_TIMEOUT,
            )
            eligible = [offense for offense in page if offense.start_time >= checkpoint.go_live_at]
            if eligible:
                changed = await call(
                    ADMIT_OFFENSES, eligible, workflow.now(), result_type=list[int]
                )
                versions = {offense.offense_id: offense.last_updated_time for offense in eligible}
                await asyncio.gather(
                    *(
                        _signal_case(oid, OFFENSE_UPDATED, versions[oid])
                        for oid in changed
                        if oid in versions
                    )
                )
            for offense in page:
                checkpoint = checkpoint.past(offense)
            if len(page) < PAGE_SIZE:
                break
        return checkpoint

    async def _close_cases(self) -> None:
        closed = await call(
            FIND_CLOSED_OFFENSES, result_type=list[int], attempt_timeout=SOURCE_TIMEOUT
        )
        delivered = await asyncio.gather(*(_signal_case(oid, OFFENSE_CLOSED) for oid in closed))
        for offense_id, signaled in zip(closed, delivered, strict=True):
            if not signaled:
                # The case workflow is gone, for example terminated by hand; close its records.
                await call(
                    CLOSE_CASE, case_workflow_id(offense_id), offense_id, result_type=type(None)
                )

    async def _start_cases(self) -> None:
        for offense_id in await call(NEXT_PENDING_OFFENSES, result_type=list[int]):
            await call(START_CASE, offense_id, result_type=bool)


def _starting_checkpoint(seed: IntakeCheckpoint | None) -> IntakeCheckpoint:
    if workflow.has_last_completion_result():
        previous: IntakeCheckpoint | None = workflow.get_last_completion_result(IntakeCheckpoint)
        if previous is not None:
            return previous
    if seed is not None:
        return seed
    checkpoint = IntakeCheckpoint.go_live(workflow.now())
    workflow.logger.info("first intake run: go-live at %s", checkpoint.go_live_at.isoformat())
    return checkpoint


async def _signal_case(offense_id: int, signal: str, *args: object) -> bool:
    """Send `signal` to the offense's case workflow; False if that workflow is not running."""
    handle = workflow.get_external_workflow_handle(case_workflow_id(offense_id))
    try:
        await handle.signal(signal, args=list(args))
    except ApplicationError as error:
        if error.type != WORKFLOW_NOT_FOUND:
            raise
        workflow.logger.warning("no running case workflow for offense %d (%s)", offense_id, signal)
        return False
    return True
