"""JSON columns hold only data that fits their contract model (criterion 5).

The check sits in the column type, so it applies to every write through SQLAlchemy, not only
to the repository functions; reads are checked too.
"""

import json
from datetime import datetime

import pytest
import storage_payloads as payloads
from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy import Column, insert, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import (
    AgentTask,
    CaseReport,
    CaseSource,
    CaseVerdict,
    Claim,
    Confidence,
    HuntReport,
    HuntRequest,
    Level,
    ModelRelease,
    Recommendation,
    RunStatus,
    SkillRef,
    ToolIntent,
    ToolStatus,
    TriageResult,
    TuningProposal,
    UrgentEvent,
)
from ais0c_storage.columns import ContractJSONB
from ais0c_storage.enums import CaseStatus, PolicyDecision
from ais0c_storage.models import AGENT_RUN_RESULT, Base, CaseRow
from ais0c_storage.repositories import (
    create_case,
    finish_agent_run,
    get_case,
    record_case_decision,
    record_tool_call,
    start_agent_run,
)

# The model data-model.md names for each JSON column.
DOCUMENTED_MODELS: dict[str, object] = {
    "cases.report": CaseReport,
    "agent_runs.task": AgentTask,
    # "İlgili sonuç modeli": the result model of the agent that ran.
    "agent_runs.result": AGENT_RUN_RESULT,
    "agent_runs.skill": SkillRef,
    "agent_runs.model_release": ModelRelease,
    "tool_calls.intent": ToolIntent,
    # The `identifiers` field of `EvidenceRef`.
    "evidence.identifiers": dict[str, str],
    "urgent_events.event": UrgentEvent,
    "recommendations.recommendation": Recommendation,
    "hunts.request": HuntRequest,
    "hunts.report": HuntReport,
    "hunt_findings.claims": list[Claim],
    "tuning_proposals.proposal": TuningProposal,
    # No model in data-model.md, but always a JSON object.
    "audit_log.details": dict[str, JsonValue],
}
# JSON columns for which data-model.md names no model.
UNMODELED = {
    "hunt_packs.content",
    "hunt_schedules.scope",
    "actors.sources",
    "fp_clusters.pattern",
    "health_alarms.details",
}

VALID: dict[str, object] = {
    "cases.report": payloads.case_report(),
    "agent_runs.task": payloads.agent_task(),
    "agent_runs.result": payloads.triage_result(),
    "agent_runs.skill": payloads.skill_ref(),
    "agent_runs.model_release": payloads.model_release(),
    "tool_calls.intent": payloads.tool_intent(),
    "evidence.identifiers": {"qid": "5000123"},
    "urgent_events.event": payloads.urgent_event(),
    "recommendations.recommendation": payloads.recommendation(),
    "hunts.request": payloads.hunt_request(),
    "hunts.report": payloads.hunt_report(),
    "hunt_findings.claims": [payloads.claim()],
    "tuning_proposals.proposal": payloads.tuning_proposal(),
    "audit_log.details": {"mode": "skip", "previous": None, "rule_ids": [100201]},
}
INVALID: dict[str, object] = {
    "cases.report": {"summary_tr": "Özet"},
    "agent_runs.task": payloads.agent_task().model_dump() | {"unknown": 1},
    "agent_runs.result": {"task_id": "task-1"},
    "agent_runs.skill": payloads.skill_ref().model_dump() | {"content_hash": "md5:0f0f"},
    "agent_runs.model_release": payloads.model_release().model_dump()
    | {"inference_params": {"stop": ["</answer>"]}},
    "tool_calls.intent": payloads.tool_intent().model_dump() | {"case_id": None},
    "evidence.identifiers": {"qid": 5000123},
    "urgent_events.event": payloads.urgent_event().model_dump() | {"rank": 0},
    "recommendations.recommendation": {"action_type": "isolate_host"},
    "hunts.request": payloads.hunt_request().model_dump() | {"window_end": "2026-03-01T00:00Z"},
    "hunts.report": payloads.hunt_report().model_dump() | {"outcome": "supported"},
    "hunt_findings.claims": [{"text": "No evidence.", "evidence_ids": []}],
    "tuning_proposals.proposal": payloads.tuning_proposal().model_dump() | {"risk_flag": True},
    "audit_log.details": ["not", "an", "object"],
}


def json_columns() -> dict[str, Column[object]]:
    return {
        f"{table.name}.{column.name}": column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, JSONB | ContractJSONB)
    }


def bind(column_name: str, value: object) -> object:
    column_type = json_columns()[column_name].type
    assert isinstance(column_type, ContractJSONB)
    return column_type.process_bind_param(value, postgresql.dialect())


def test_every_json_column_is_checked_against_its_documented_model() -> None:
    checked = {
        name: column.type.model
        for name, column in json_columns().items()
        if isinstance(column.type, ContractJSONB)
    }
    assert checked == DOCUMENTED_MODELS
    assert set(json_columns()) - set(checked) == UNMODELED


@pytest.mark.parametrize("column_name", sorted(DOCUMENTED_MODELS))
def test_valid_data_is_written_as_json(column_name: str) -> None:
    written = bind(column_name, VALID[column_name])

    assert json.loads(json.dumps(written)) == written


@pytest.mark.parametrize("column_name", sorted(DOCUMENTED_MODELS))
def test_data_outside_the_model_is_refused(column_name: str) -> None:
    with pytest.raises(ValidationError):
        bind(column_name, INVALID[column_name])


# A field and a value that breaks the model; `model_copy(update=...)` does not validate.
BROKEN_FIELD: dict[str, tuple[str, object]] = {
    "cases.report": ("summary_tr", "x" * 601),
    "agent_runs.task": ("objective", "x" * 301),
    "agent_runs.result": ("rationale", "x" * 601),
    "agent_runs.skill": ("content_hash", "sha256:" + "0F" * 32),
    "agent_runs.model_release": ("max_context", "128k"),
    "tool_calls.intent": ("reason", "x" * 301),
    "urgent_events.event": ("rank", 0),
    "recommendations.recommendation": ("target", "x" * 201),
    "hunts.request": ("window_end", payloads.hunt_request().window_start),
    "hunts.report": ("outcome", "supported"),
    "tuning_proposals.proposal": ("risk_flag", True),
}


@pytest.mark.parametrize("column_name", sorted(BROKEN_FIELD))
def test_an_instance_that_skipped_validation_is_refused(column_name: str) -> None:
    """The column validates instances again, so an instance changed with `model_copy` or
    built with `model_construct` cannot carry invalid data in."""
    valid = VALID[column_name]
    assert isinstance(valid, BaseModel)
    field, value = BROKEN_FIELD[column_name]
    broken = valid.model_copy(update={field: value})

    with pytest.raises(ValidationError):
        bind(column_name, broken)


def test_another_contract_model_is_not_accepted_as_case_report() -> None:
    with pytest.raises(ValidationError):
        bind("cases.report", payloads.triage_result())


# --- Through the database


async def open_case(session: AsyncSession) -> None:
    await create_case(
        session,
        case_id=payloads.CASE_ID,
        source=CaseSource.OFFENSE,
        offense_id=payloads.OFFENSE_ID,
        sla_due_at=payloads.T1,
        workflow_id=payloads.CASE_ID,
        run_id="run-1",
    )


async def decide(session: AsyncSession, report: object) -> CaseRow:
    return await record_case_decision(
        session,
        payloads.CASE_ID,
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        ai_level=Level.HIGH,
        notify_level=Level.HIGH,
        floor_level=None,
        decided_at=payloads.T1,
        report=report,  # type: ignore[arg-type]
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "report",
    [
        pytest.param(payloads.triage_result(), id="another-model"),
        pytest.param({"summary_tr": "Özet"}, id="plain-dict"),
        pytest.param(
            payloads.case_report().model_copy(update={"summary_tr": "x" * 601}),
            id="copied-past-the-limit",
        ),
    ],
)
async def test_repository_refuses_a_report_that_is_not_a_case_report(
    session: AsyncSession, report: object
) -> None:
    await open_case(session)

    with pytest.raises(ValidationError):
        await decide(session, report)


@pytest.mark.anyio
async def test_orm_and_core_writes_are_checked_too(session: AsyncSession) -> None:
    await open_case(session)
    await session.commit()
    row = await get_case(session, payloads.CASE_ID)
    assert row is not None

    row.report = {"summary_tr": "Özet"}  # type: ignore[assignment]
    with pytest.raises(StatementError) as raised:
        await session.flush()
    assert isinstance(raised.value.orig, ValidationError)
    await session.rollback()

    statement = update(CaseRow).where(CaseRow.case_id == payloads.CASE_ID).values(report={"x": 1})
    with pytest.raises(StatementError) as raised:
        await session.execute(statement)
    assert isinstance(raised.value.orig, ValidationError)


@pytest.mark.anyio
async def test_a_case_report_round_trips(session: AsyncSession) -> None:
    await open_case(session)
    report = payloads.case_report()
    await decide(session, report)
    await session.commit()
    session.expunge_all()

    stored = await get_case(session, payloads.CASE_ID)

    assert stored is not None
    assert isinstance(stored.report, CaseReport)
    assert stored.report == report
    assert stored.status is CaseStatus.DECIDED


@pytest.mark.anyio
async def test_invalid_json_written_behind_the_orm_fails_on_read(session: AsyncSession) -> None:
    await open_case(session)
    await session.commit()
    await session.execute(
        text("UPDATE cases SET report = '{\"summary_tr\": 1}' WHERE case_id = :case_id"),
        {"case_id": payloads.CASE_ID},
    )
    await session.commit()
    session.expunge_all()

    with pytest.raises(ValidationError):
        await get_case(session, payloads.CASE_ID)


@pytest.mark.anyio
async def test_agent_run_task_result_and_tool_intent_are_checked(session: AsyncSession) -> None:
    broken_task = payloads.agent_task().model_copy(update={"case_id": None})
    with pytest.raises(ValidationError):
        await start_agent_run(
            session,
            run_id="run-1",
            task=broken_task,
            prompt_version="v1",
            model_alias="soc-fast",
            model_target="lab-model",
            toolset_profile="qradar-triage-read",
            started_at=payloads.T0,
        )

    await start_agent_run(
        session,
        run_id="run-1",
        task=payloads.agent_task(),
        prompt_version="v1",
        model_alias="soc-fast",
        model_target="lab-model",
        toolset_profile="qradar-triage-read",
        started_at=payloads.T0,
    )
    with pytest.raises(ValidationError):
        await finish_agent_run(
            session,
            "run-1",
            status=RunStatus.COMPLETED,
            result=payloads.agent_task(),  # type: ignore[arg-type]
            tokens=10,
            tool_calls=1,
            ended_at=payloads.T1,
        )
    with pytest.raises(ValidationError):
        await record_tool_call(
            session,
            run_id="run-1",
            intent=payloads.tool_intent().model_copy(update={"reason": "x" * 301}),
            policy_decision=PolicyDecision.ALLOW,
            status=ToolStatus.OK,
            latency_ms=12,
        )
    row = await finish_agent_run(
        session,
        "run-1",
        status=RunStatus.COMPLETED,
        result=payloads.triage_result(),
        tokens=10,
        tool_calls=1,
        ended_at=payloads.T1,
    )
    await session.commit()
    session.expunge_all()

    stored = await session.get(type(row), "run-1")
    assert stored is not None
    assert isinstance(stored.result, TriageResult)
    assert isinstance(stored.task, AgentTask)


@pytest.mark.anyio
async def test_naive_datetime_and_unknown_status_are_refused(session: AsyncSession) -> None:
    await open_case(session)
    naive = datetime(2026, 10, 2, 10, 0)  # noqa: DTZ001 - the input under test

    with pytest.raises(StatementError) as raised:
        await session.execute(
            update(CaseRow).where(CaseRow.case_id == payloads.CASE_ID).values(sla_due_at=naive)
        )
    assert isinstance(raised.value.orig, ValueError)
    await session.rollback()

    with pytest.raises(StatementError) as raised:
        await session.execute(
            insert(CaseRow).values(
                case_id="case-1",
                source="offense",
                offense_id=1,
                status="paused",
                evaluation_no=1,
                sla_due_at=payloads.T1,
                workflow_id="case-1",
                run_id="run-1",
            )
        )
    assert isinstance(raised.value.orig, ValueError)
