"""Repository functions of `agent_runs` and `tool_calls` (T-004 criterion 6), and the model
release of a run (T-016 criteria 2 and 3)."""

from datetime import datetime, timedelta

import pytest
import storage_payloads as payloads
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import CASE_ID, EVIDENCE_ID, T0, T1

from ais0c_contracts import ModelRelease, RunStatus, ToolStatus
from ais0c_storage.enums import PolicyDecision
from ais0c_storage.errors import DuplicateError, NotFoundError
from ais0c_storage.models import AgentRunRow
from ais0c_storage.repositories import (
    finish_agent_run,
    get_agent_run,
    latest_model_releases,
    list_agent_runs,
    list_tool_calls,
    record_tool_call,
    start_agent_run,
)

pytestmark = pytest.mark.anyio


async def start(
    session: AsyncSession,
    run_id: str = "run-1",
    case_id: str | None = CASE_ID,
    *,
    model_alias: str = "soc-fast",
    model_target: str = "lab-model",
    started_at: datetime = T0,
    model_release: ModelRelease | None = None,
) -> AgentRunRow:
    hunt_id = None if case_id else "hunt-1"
    return await start_agent_run(
        session,
        run_id=run_id,
        task=payloads.agent_task(case_id=case_id, hunt_id=hunt_id),
        prompt_version="v1",
        model_alias=model_alias,
        model_target=model_target,
        toolset_profile="qradar-triage-read",
        started_at=started_at,
        model_release=model_release,
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
    # No model release unless one is given; no skill until T-021 records one.
    assert (run.model_release, run.skill) == (None, None)
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


# --- Model release (T-016)


async def test_a_run_keeps_the_release_of_its_model(session: AsyncSession) -> None:
    release = payloads.model_release()
    await start(session, model_release=release)
    await session.commit()
    session.expunge_all()

    run = await get_agent_run(session, "run-1")

    assert run is not None
    assert isinstance(run.model_release, ModelRelease)
    assert run.model_release == release
    assert run.skill is None


@pytest.mark.parametrize(
    "release",
    [
        pytest.param(payloads.model_release(alias="soc-report"), id="another-alias"),
        pytest.param(payloads.model_release(target="another-model"), id="another-target"),
    ],
)
async def test_a_release_of_another_model_is_refused(
    session: AsyncSession, release: ModelRelease
) -> None:
    with pytest.raises(ValueError, match="not the release of model_alias and model_target"):
        await start(session, model_release=release)

    assert await get_agent_run(session, "run-1") is None


async def test_a_release_that_skipped_validation_is_refused(session: AsyncSession) -> None:
    broken = payloads.model_release().model_copy(update={"max_context": "128k"})

    with pytest.raises(ValidationError):
        await start(session, model_release=broken)


async def test_a_release_written_behind_the_repository_fails_on_read(
    session: AsyncSession,
) -> None:
    await start(session)
    await session.commit()
    await session.execute(text('UPDATE agent_runs SET model_release = \'{"alias": "soc-fast"}\''))
    await session.commit()
    session.expunge_all()

    with pytest.raises(ValidationError):
        await get_agent_run(session, "run-1")


async def test_latest_model_releases_gives_the_last_release_of_each_alias(
    session: AsyncSession,
) -> None:
    old = payloads.model_release(engine_version="0.10.1")
    new = payloads.model_release(engine_version="0.11.2")
    report = payloads.model_release(alias="soc-report")
    # Recorded out of order: the start time decides, not the order of the rows.
    await start(session, "run-2", started_at=T1, model_release=new)
    await start(session, "run-1", started_at=T0, model_release=old)
    await start(session, "run-3", model_alias="soc-report", model_release=report)
    # A run without a model (an intake run) is the latest one, and records no release.
    await start(
        session, "run-4", model_alias="none", model_target="none", started_at=T1 + timedelta(1)
    )

    assert await latest_model_releases(session) == {"soc-fast": new, "soc-report": report}


async def test_latest_model_releases_of_an_empty_table(session: AsyncSession) -> None:
    await start(session)

    assert await latest_model_releases(session) == {}
