"""`agent_runs` and `tool_calls`."""

from datetime import datetime

import pydantic_core
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import AgentTask, ModelRelease, RunStatus, ToolIntent, ToolStatus
from ais0c_storage.columns import revalidate
from ais0c_storage.enums import PolicyDecision
from ais0c_storage.models import AGENT_RUN_RESULT, AgentRunResult, AgentRunRow, ToolCallRow
from ais0c_storage.repositories._common import (
    fetch_all,
    get_row,
    insert_new,
    insert_row,
    update_one,
)


async def start_agent_run(
    session: AsyncSession,
    *,
    run_id: str,
    task: AgentTask,
    prompt_version: str,
    model_alias: str,
    model_target: str,
    toolset_profile: str,
    started_at: datetime,
    model_release: ModelRelease | None = None,
) -> AgentRunRow:
    """Record a run when it starts, so its tool calls can reference it.

    The agent, its version and the case or hunt are taken from `task`. `model_release` is the
    real identity of the model behind `model_alias` (T-24): an agent run passes the release of
    its alias, a run that uses no model passes none. `status`, `result` and `ended_at` stay
    NULL until `finish_agent_run`. Raises `DuplicateError` if the run exists.
    """
    task = revalidate(AgentTask, task)
    if model_release is not None:
        model_release = revalidate(ModelRelease, model_release)
        if (model_release.alias, model_release.target) != (model_alias, model_target):
            raise ValueError("model_release is not the release of model_alias and model_target")
    values = dict(
        run_id=run_id,
        case_id=task.case_id,
        hunt_id=task.hunt_id,
        agent_id=task.agent_id,
        agent_version=task.agent_version,
        prompt_version=prompt_version,
        model_alias=model_alias,
        model_target=model_target,
        toolset_profile=toolset_profile,
        status=None,
        task=task,
        result=None,
        tokens=0,
        tool_calls=0,
        model_release=model_release,
        started_at=started_at,
        ended_at=None,
    )
    return await insert_new(session, AgentRunRow, values, f"agent run {run_id!r}")


async def finish_agent_run(
    session: AsyncSession,
    run_id: str,
    *,
    status: RunStatus,
    result: AgentRunResult | None,
    tokens: int,
    tool_calls: int,
    ended_at: datetime,
) -> AgentRunRow:
    """Store how the run ended. `result` is the agent's output model, if it produced one; its
    `status` must be the run's status.

    Calling it again overwrites the outcome, so a retried activity is harmless.
    """
    if result is not None:
        result = AGENT_RUN_RESULT.validate_json(pydantic_core.to_json(result))
        if result.status != status:
            raise ValueError("result.status differs from the run status")
    statement = (
        update(AgentRunRow)
        .where(AgentRunRow.run_id == run_id)
        .values(
            status=status,
            result=result,
            tokens=tokens,
            tool_calls=tool_calls,
            ended_at=ended_at,
        )
    )
    return await update_one(session, statement, AgentRunRow, f"agent run {run_id!r}")


async def get_agent_run(session: AsyncSession, run_id: str) -> AgentRunRow | None:
    return await get_row(session, AgentRunRow, run_id)


async def list_agent_runs(
    session: AsyncSession, *, case_id: str | None = None, hunt_id: str | None = None
) -> list[AgentRunRow]:
    """Runs of a case or a hunt, oldest first."""
    if case_id is None and hunt_id is None:
        raise ValueError("case_id or hunt_id is required")
    statement = select(AgentRunRow)
    if case_id is not None:
        statement = statement.where(AgentRunRow.case_id == case_id)
    if hunt_id is not None:
        statement = statement.where(AgentRunRow.hunt_id == hunt_id)
    statement = statement.order_by(AgentRunRow.started_at, AgentRunRow.run_id)
    return await fetch_all(session, statement)


async def latest_model_releases(session: AsyncSession) -> dict[str, ModelRelease]:
    """The model release of the last started run of each model alias, by alias.

    Runs without a release (those that use no model) are left out, and so is an alias none of
    whose runs has one.
    """
    statement = (
        select(AgentRunRow.model_alias, AgentRunRow.model_release)
        .where(AgentRunRow.model_release.is_not(None))
        .ext(distinct_on(AgentRunRow.model_alias))
        .order_by(AgentRunRow.model_alias, AgentRunRow.started_at.desc(), AgentRunRow.run_id.desc())
    )
    rows = await session.execute(statement)
    return {alias: release for alias, release in rows if release is not None}


async def record_tool_call(
    session: AsyncSession,
    *,
    run_id: str,
    intent: ToolIntent,
    policy_decision: PolicyDecision,
    status: ToolStatus,
    latency_ms: int,
    deny_reason: str | None = None,
    evidence_id: str | None = None,
) -> ToolCallRow:
    """Record one gateway call. The run must have been started (`start_agent_run`)."""
    values = dict(
        run_id=run_id,
        intent=revalidate(ToolIntent, intent),
        policy_decision=policy_decision,
        deny_reason=deny_reason,
        status=status,
        evidence_id=evidence_id,
        latency_ms=latency_ms,
    )
    return await insert_row(session, ToolCallRow, values)


async def list_tool_calls(session: AsyncSession, run_id: str) -> list[ToolCallRow]:
    """Calls of a run in the order they were recorded."""
    statement = (
        select(ToolCallRow)
        .where(ToolCallRow.run_id == run_id)
        .order_by(ToolCallRow.created_at, ToolCallRow.id)
    )
    return await fetch_all(session, statement)
