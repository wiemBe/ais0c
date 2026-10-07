"""`agent_runs` and `tool_calls`."""

from collections.abc import Collection
from datetime import datetime

import pydantic_core
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import AgentTask, ModelRelease, RunStatus, SkillRef, ToolIntent, ToolStatus
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

MAX_ERROR_LENGTH = 2000


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
    skill: SkillRef | None = None,
) -> AgentRunRow:
    """Record a run when it starts, so its tool calls can reference it.

    The agent, its version and the case or hunt are taken from `task`. `model_release` is the
    real identity of the model behind `model_alias` (T-24): an agent run passes the release of
    its alias, a run that uses no model passes none. `skill` is the skill version the run uses
    (T-21); None for a run without one. `status`, `result` and `ended_at` stay NULL until
    `finish_agent_run`. Raises `DuplicateError` if the run exists.
    """
    task = revalidate(AgentTask, task)
    if skill is not None:
        skill = revalidate(SkillRef, skill)
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
        error=None,
        tokens=0,
        tool_calls=0,
        skill=skill,
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
    error: str | None = None,
) -> AgentRunRow:
    """Store how the run ended. `result` is the agent's output model, if it produced one; its
    `status` must be the run's status.

    `error` is the failure or budget exhaustion reason and is truncated to 2000 characters.
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
            error=None if error is None else error[:MAX_ERROR_LENGTH],
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


async def list_tool_calls_of_runs(
    session: AsyncSession, run_ids: Collection[str]
) -> list[ToolCallRow]:
    """The tool calls of several runs in one query, each run's in the order they were recorded.

    For the analyst API's case steps and evidence list (T-028), which would otherwise read the
    calls run by run.
    """
    wanted = set(run_ids)
    if not wanted:
        return []
    statement = (
        select(ToolCallRow)
        .where(ToolCallRow.run_id.in_(list(wanted)))
        .order_by(ToolCallRow.created_at, ToolCallRow.id)
    )
    return await fetch_all(session, statement)


async def list_tools_for_evidence(
    session: AsyncSession, evidence_ids: Collection[str]
) -> dict[str, str]:
    """The gateway tool that issued each of `evidence_ids`, by evidence ID.

    The evidence table holds no tool: an `EvidenceRef` is the query and its identifiers. The tool
    call that recorded it does, so the analyst API's evidence list (T-028) reads it from there.
    An evidence ID no call carries, or one several calls share, maps to the earliest call; the
    value is `""` when nothing carries the ID.
    """
    wanted = set(evidence_ids)
    if not wanted:
        return {}
    statement = (
        select(ToolCallRow.evidence_id, ToolCallRow.intent)
        .where(ToolCallRow.evidence_id.in_(list(wanted)))
        .order_by(ToolCallRow.created_at, ToolCallRow.id)
    )
    rows = await session.execute(statement)
    tools: dict[str, str] = {}
    for evidence_id, intent in rows:
        if evidence_id is not None:
            tools.setdefault(evidence_id, intent.tool_id)
    return tools
