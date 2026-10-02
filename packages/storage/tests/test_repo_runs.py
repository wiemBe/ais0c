"""Repository functions of `agent_runs` and `tool_calls` (criterion 6)."""

import pytest
import storage_payloads as payloads
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import CASE_ID, EVIDENCE_ID, T0, T1

from ais0c_contracts import RunStatus, ToolStatus
from ais0c_storage.enums import PolicyDecision
from ais0c_storage.errors import DuplicateError, NotFoundError
from ais0c_storage.models import AgentRunRow
from ais0c_storage.repositories import (
    finish_agent_run,
    get_agent_run,
    list_agent_runs,
    list_tool_calls,
    record_tool_call,
    start_agent_run,
)

pytestmark = pytest.mark.anyio


async def start(
    session: AsyncSession, run_id: str = "run-1", case_id: str | None = CASE_ID
) -> AgentRunRow:
    hunt_id = None if case_id else "hunt-1"
    return await start_agent_run(
        session,
        run_id=run_id,
        task=payloads.agent_task(case_id=case_id, hunt_id=hunt_id),
        prompt_version="v1",
        model_alias="soc-fast",
        model_target="lab-model",
        toolset_profile="qradar-triage-read",
        started_at=T0,
    )


async def test_a_started_run_is_in_progress(session: AsyncSession) -> None:
    run = await start(session)

    assert (run.agent_id, run.agent_version, run.case_id, run.hunt_id) == (
        "triage",
        "1",
        CASE_ID,
        None,
    )
    assert (run.status, run.result, run.ended_at, run.tokens, run.tool_calls) == (
        None,
        None,
        None,
        0,
        0,
    )
    assert run.task == payloads.agent_task()
    with pytest.raises(DuplicateError):
        await start(session)


async def test_finish_stores_the_outcome(session: AsyncSession) -> None:
    await start(session)
    result = payloads.triage_result()

    run = await finish_agent_run(
        session,
        "run-1",
        status=RunStatus.COMPLETED,
        result=result,
        tokens=1500,
        tool_calls=2,
        ended_at=T1,
    )
    assert (run.status, run.result, run.tokens, run.tool_calls, run.ended_at) == (
        RunStatus.COMPLETED,
        result,
        1500,
        2,
        T1,
    )

    # A retried activity may finish the run again.
    run = await finish_agent_run(
        session,
        "run-1",
        status=RunStatus.FAILED,
        result=None,
        tokens=1600,
        tool_calls=2,
        ended_at=T1,
    )
    assert (run.status, run.result, run.tokens) == (RunStatus.FAILED, None, 1600)

    with pytest.raises(ValueError, match=r"result\.status"):
        await finish_agent_run(
            session,
            "run-1",
            status=RunStatus.BUDGET_EXHAUSTED,
            result=payloads.triage_result(status="completed"),
            tokens=1,
            tool_calls=1,
            ended_at=T1,
        )
    with pytest.raises(NotFoundError):
        await finish_agent_run(
            session,
            "run-404",
            status=RunStatus.FAILED,
            result=None,
            tokens=0,
            tool_calls=0,
            ended_at=T1,
        )


async def test_tool_calls_belong_to_a_started_run(session: AsyncSession) -> None:
    await start(session)
    allowed = await record_tool_call(
        session,
        run_id="run-1",
        intent=payloads.tool_intent(),
        policy_decision=PolicyDecision.ALLOW,
        status=ToolStatus.OK,
        latency_ms=420,
        evidence_id=EVIDENCE_ID,
    )
    denied = await record_tool_call(
        session,
        run_id="run-1",
        intent=payloads.tool_intent(),
        policy_decision=PolicyDecision.DENY,
        status=ToolStatus.DENIED,
        latency_ms=3,
        deny_reason="aql_guard: window_too_wide",
    )

    calls = await list_tool_calls(session, "run-1")

    assert [call.id for call in calls] == [allowed.id, denied.id]
    assert allowed.id.version == 7
    assert (calls[0].policy_decision, calls[0].evidence_id, calls[0].intent) == (
        PolicyDecision.ALLOW,
        EVIDENCE_ID,
        payloads.tool_intent(),
    )
    assert (calls[1].status, calls[1].deny_reason) == (
        ToolStatus.DENIED,
        "aql_guard: window_too_wide",
    )
    assert calls[0].created_at is not None
    assert await list_tool_calls(session, "run-2") == []

    with pytest.raises(IntegrityError):
        await record_tool_call(
            session,
            run_id="run-never-started",
            intent=payloads.tool_intent(),
            policy_decision=PolicyDecision.ALLOW,
            status=ToolStatus.OK,
            latency_ms=1,
        )


async def test_list_runs_of_a_case_or_a_hunt(session: AsyncSession) -> None:
    await start(session, "run-1")
    await start(session, "run-2")
    await start(session, "run-3", case_id=None)

    case_runs = await list_agent_runs(session, case_id=CASE_ID)
    hunt_runs = await list_agent_runs(session, hunt_id="hunt-1")

    assert [run.run_id for run in case_runs] == ["run-1", "run-2"]
    assert [run.run_id for run in hunt_runs] == ["run-3"]
    assert await get_agent_run(session, "run-404") is None
    with pytest.raises(ValueError, match="case_id or hunt_id"):
        await list_agent_runs(session)
