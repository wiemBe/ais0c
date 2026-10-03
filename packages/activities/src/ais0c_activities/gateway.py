"""The MCP Policy Gateway as activities use it (architecture §13).

The gateway records every call in `tool_calls` under the agent run its ToolIntent names in
`run_id` (contracts v0.2, T-19). The run must be recorded, in progress and belong to the
caller's profile, agent and case or hunt; otherwise the call is refused.

An agent gets the plain gateway client: its tool functions take the run ID from the run's deps
(`ais0c_agents.RunDeps`). `system_run` is for platform code that reads QRadar without an agent,
such as the offense source. It records a short run of a pseudo agent (D-33), puts the run's ID
in the intents of the calls made inside and closes it. The run has no prompt or model; those
columns hold `none`.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue
from temporalio import activity

from ais0c_activities.db import SessionFactory
from ais0c_agents import GatewayClient, ToolsetProfile, ToolSpec
from ais0c_contracts import (
    AgentTask,
    Budget,
    RunStatus,
    TimeWindow,
    ToolIntent,
    ToolResult,
    ToolStatus,
)
from ais0c_storage import new_uuid7
from ais0c_storage.repositories import finish_agent_run, start_agent_run

# What a system run records where an agent run has a version, prompt and model.
SYSTEM_AGENT_VERSION: Final = "1.0.0"
NOT_APPLICABLE: Final = "none"

_log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class SystemRunError(RuntimeError):
    """A tool call of a system run did not return `ok`."""


@dataclass
class SystemRun:
    """Calls of one system run; every call carries the run's ID, context and time window."""

    gateway: GatewayClient
    profile: ToolsetProfile
    run_id: str
    task: AgentTask
    calls: int = 0

    async def call(
        self,
        tool_id: str,
        arguments: dict[str, JsonValue],
        *,
        reason: str,
        expected_evidence: str,
    ) -> ToolResult:
        """One gateway call; a result other than `ok` raises SystemRunError."""
        spec = self._spec(tool_id)
        intent = ToolIntent(
            run_id=self.run_id,
            case_id=self.task.case_id,
            hunt_id=self.task.hunt_id,
            agent_id=self.task.agent_id,
            toolset_profile=self.profile.name,
            tool_id=tool_id,
            tool_schema_version=spec.schema_version,
            arguments=arguments,
            reason=reason,
            expected_evidence=expected_evidence,
            time_window=self.task.time_window,
            cost_class=spec.cost_class,
        )
        self.calls += 1
        result = await self.gateway.call(intent)
        if result.status is not ToolStatus.OK:
            raise SystemRunError(f"{tool_id}: {result.status.value}: {result.deny_reason or ''}")
        if result.truncated:
            _log.warning("%s returned a truncated result in run %s", tool_id, self.run_id)
        return result

    def _spec(self, tool_id: str) -> ToolSpec:
        for spec in self.profile.tools:
            if spec.id == tool_id:
                return spec
        raise SystemRunError(f"{tool_id} is not a tool of {self.profile.name}")


@asynccontextmanager
async def system_run(
    *,
    sessions: SessionFactory,
    gateway: GatewayClient,
    profile: ToolsetProfile,
    agent_id: str,
    case_id: str,
    objective: str,
    window: TimeWindow,
    budget: Budget,
    clock: Callable[[], datetime] = utc_now,
) -> AsyncIterator[SystemRun]:
    """Record a run of the pseudo agent `agent_id` for `case_id` around the calls inside.

    The run ends `completed` when the block finishes and `failed` when it raises.
    """
    run_id = str(new_uuid7())
    info = activity.info() if activity.in_activity() else None
    task = AgentTask(
        task_id=run_id,
        parent_run_id=(info.workflow_run_id if info is not None else None) or run_id,
        case_id=case_id,
        agent_id=agent_id,
        agent_version=SYSTEM_AGENT_VERSION,
        objective=objective,
        context_refs=[],
        time_window=window,
        budget=budget,
    )
    async with sessions.begin() as session:
        await start_agent_run(
            session,
            run_id=run_id,
            task=task,
            prompt_version=NOT_APPLICABLE,
            model_alias=NOT_APPLICABLE,
            model_target=NOT_APPLICABLE,
            toolset_profile=profile.name,
            started_at=clock(),
        )
    run = SystemRun(gateway=gateway, profile=profile, run_id=run_id, task=task)
    try:
        yield run
    except BaseException:
        await _finish(sessions, run, RunStatus.FAILED, clock)
        raise
    await _finish(sessions, run, RunStatus.COMPLETED, clock)


async def _finish(
    sessions: SessionFactory, run: SystemRun, status: RunStatus, clock: Callable[[], datetime]
) -> None:
    async with sessions.begin() as session:
        await finish_agent_run(
            session,
            run.run_id,
            status=status,
            result=None,
            tokens=0,
            tool_calls=run.calls,
            ended_at=clock(),
        )
