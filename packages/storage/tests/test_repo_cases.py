"""Repository functions of `cases` (criterion 6)."""

from datetime import timedelta

import pytest
import storage_payloads as payloads
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import CASE_ID, OFFENSE_ID, T0, T1

from ais0c_contracts import CaseSource, CaseVerdict, Confidence, Level
from ais0c_storage.enums import CaseStatus
from ais0c_storage.errors import DuplicateError, NotFoundError
from ais0c_storage.models import CaseRow
from ais0c_storage.repositories import (
    begin_case_reevaluation,
    create_case,
    list_cases,
    record_case_decision,
    set_case_run_id,
    set_case_status,
)

pytestmark = pytest.mark.anyio


async def open_case(
    session: AsyncSession, case_id: str = CASE_ID, offense_id: int = OFFENSE_ID
) -> CaseRow:
    return await create_case(
        session,
        case_id=case_id,
        source=CaseSource.OFFENSE,
        offense_id=offense_id,
        sla_due_at=T1,
        workflow_id=case_id,
        run_id="run-1",
    )


async def decide(
    session: AsyncSession,
    case_id: str = CASE_ID,
    notify_level: Level = Level.HIGH,
    verdict: CaseVerdict = CaseVerdict.SUSPICIOUS,
) -> CaseRow:
    return await record_case_decision(
        session,
        case_id,
        verdict=verdict,
        confidence=Confidence.MEDIUM,
        ai_level=Level.MEDIUM,
        notify_level=notify_level,
        floor_level=Level.HIGH,
        decided_at=T1,
    )


async def test_create_case(session: AsyncSession) -> None:
    case = await open_case(session)

    assert (case.status, case.evaluation_no, case.source) == (
        CaseStatus.RUNNING,
        1,
        CaseSource.OFFENSE,
    )
    assert case.verdict is None
    assert case.decided_at is None
    assert case.created_at is not None
    with pytest.raises(DuplicateError):
        await open_case(session)
    with pytest.raises(ValueError, match="offense ID"):
        await create_case(
            session,
            case_id="case-1",
            source=CaseSource.OFFENSE,
            sla_due_at=T1,
            workflow_id="case-1",
            run_id="run-1",
        )
    hunt_case = await create_case(
        session,
        case_id="case-hunt-hunt-1-1",
        source=CaseSource.HUNT,
        hunt_id="hunt-1",
        sla_due_at=T1,
        workflow_id="case-hunt-hunt-1-1",
        run_id="run-2",
    )
    assert (hunt_case.hunt_id, hunt_case.offense_id) == ("hunt-1", None)


async def test_record_decision_with_and_without_report(session: AsyncSession) -> None:
    await open_case(session)

    decided = await decide(session)
    assert (decided.status, decided.verdict, decided.notify_level, decided.floor_level) == (
        CaseStatus.DECIDED,
        CaseVerdict.SUSPICIOUS,
        Level.HIGH,
        Level.HIGH,
    )
    assert (decided.decided_at, decided.report) == (T1, None)

    report = payloads.case_report()
    decided = await record_case_decision(
        session,
        CASE_ID,
        verdict=report.verdict,
        confidence=report.confidence,
        ai_level=Level.HIGH,
        notify_level=report.notify_level,
        floor_level=None,
        decided_at=T1,
        report=report,
    )
    assert decided.report == report

    with pytest.raises(ValueError, match="disagrees"):
        await record_case_decision(
            session,
            CASE_ID,
            verdict=CaseVerdict.FP,
            confidence=report.confidence,
            ai_level=Level.LOW,
            notify_level=report.notify_level,
            floor_level=None,
            decided_at=T1,
            report=report,
        )
    with pytest.raises(NotFoundError):
        await decide(session, case_id="case-404")


async def test_status_changes(session: AsyncSession) -> None:
    await open_case(session)

    assert (await set_case_status(session, CASE_ID, CaseStatus.NO_AI_DECISION)).status is (
        CaseStatus.NO_AI_DECISION
    )
    assert (await set_case_status(session, CASE_ID, CaseStatus.CLOSED)).status is (
        CaseStatus.CLOSED
    )
    assert (await set_case_run_id(session, CASE_ID, "run-2")).run_id == "run-2"
    with pytest.raises(NotFoundError):
        await set_case_status(session, "case-404", CaseStatus.CLOSED)


async def test_reevaluation_keeps_the_previous_decision_until_the_next(
    session: AsyncSession,
) -> None:
    await open_case(session)
    await decide(session, notify_level=Level.MEDIUM)
    new_deadline = T1 + timedelta(hours=1)

    case = await begin_case_reevaluation(session, CASE_ID, sla_due_at=new_deadline)

    assert (case.evaluation_no, case.status, case.sla_due_at) == (
        2,
        CaseStatus.RUNNING,
        new_deadline,
    )
    assert case.notify_level is Level.MEDIUM
    case = await begin_case_reevaluation(session, CASE_ID, sla_due_at=new_deadline)
    assert case.evaluation_no == 3
    with pytest.raises(NotFoundError):
        await begin_case_reevaluation(session, "case-404", sla_due_at=new_deadline)


async def test_list_cases_filters_and_orders_newest_first(session: AsyncSession) -> None:
    for offense_id in (1, 2, 3):
        await open_case(session, case_id=f"case-{offense_id}", offense_id=offense_id)
        # One transaction per case, so created_at (transaction time) differs.
        await session.commit()
    await decide(session, case_id="case-1", verdict=CaseVerdict.FP, notify_level=Level.LOW)
    await decide(session, case_id="case-2", notify_level=Level.CRITICAL)
    await session.commit()

    def ids(rows: list[CaseRow]) -> list[str]:
        return [row.case_id for row in rows]

    assert ids(await list_cases(session)) == ["case-3", "case-2", "case-1"]
    assert ids(await list_cases(session, limit=2)) == ["case-3", "case-2"]
    assert ids(await list_cases(session, statuses={CaseStatus.RUNNING})) == ["case-3"]
    assert ids(await list_cases(session, verdicts={CaseVerdict.FP})) == ["case-1"]
    high = {Level.HIGH, Level.CRITICAL}
    assert ids(await list_cases(session, notify_levels=high)) == ["case-2"]
    assert ids(await list_cases(session, sources={CaseSource.HUNT})) == []
    newest = (await list_cases(session, limit=1))[0].created_at
    assert ids(await list_cases(session, created_from=newest)) == ["case-3"]
    assert ids(await list_cases(session, created_to=newest)) == ["case-2", "case-1"]
    assert ids(await list_cases(session, created_from=T0 - timedelta(days=3650))) == [
        "case-3",
        "case-2",
        "case-1",
    ]
