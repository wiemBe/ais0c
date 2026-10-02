"""`cases`: one row per case workflow, holding its latest decision."""

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import CaseReport, CaseSource, CaseVerdict, Confidence, Level
from ais0c_storage.columns import revalidate
from ais0c_storage.enums import CaseStatus
from ais0c_storage.models import CaseRow
from ais0c_storage.repositories._common import fetch_all, get_row, insert_new, update_one


async def create_case(
    session: AsyncSession,
    *,
    case_id: str,
    source: CaseSource,
    sla_due_at: datetime,
    workflow_id: str,
    run_id: str,
    offense_id: int | None = None,
    hunt_id: str | None = None,
    group_id: str | None = None,
    evaluation_no: int = 1,
    status: CaseStatus = CaseStatus.RUNNING,
) -> CaseRow:
    """Open a case. The ID of its source is required: `offense_id` for an offense case,
    `hunt_id` for a hunt case, `group_id` for a group evaluation.

    Raises `DuplicateError` if the case exists.
    """
    source_ids = {
        CaseSource.OFFENSE: offense_id,
        CaseSource.HUNT: hunt_id,
        CaseSource.GROUP: group_id,
    }
    if source_ids[source] is None:
        raise ValueError(f"a {source.value} case needs its {source.value} ID")
    values = dict(
        case_id=case_id,
        source=source,
        offense_id=offense_id,
        hunt_id=hunt_id,
        group_id=group_id,
        status=status,
        evaluation_no=evaluation_no,
        sla_due_at=sla_due_at,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    return await insert_new(session, CaseRow, values, f"case {case_id!r}")


async def get_case(session: AsyncSession, case_id: str) -> CaseRow | None:
    return await get_row(session, CaseRow, case_id)


async def record_case_decision(
    session: AsyncSession,
    case_id: str,
    *,
    verdict: CaseVerdict,
    confidence: Confidence,
    ai_level: Level,
    notify_level: Level,
    floor_level: Level | None,
    decided_at: datetime,
    report: CaseReport | None = None,
) -> CaseRow:
    """Store the decision of the current evaluation and mark the case `decided`.

    `report` replaces the stored report (None clears it). It must be a valid `CaseReport` whose
    verdict, confidence and notification level are the ones given here.
    """
    if report is not None:
        report = revalidate(CaseReport, report)
        stated = (report.verdict, report.confidence, report.notify_level)
        if stated != (verdict, confidence, notify_level):
            raise ValueError("report disagrees with the verdict, confidence or notify_level")
    statement = (
        update(CaseRow)
        .where(CaseRow.case_id == case_id)
        .values(
            status=CaseStatus.DECIDED,
            verdict=verdict,
            confidence=confidence,
            ai_level=ai_level,
            floor_level=floor_level,
            notify_level=notify_level,
            decided_at=decided_at,
            report=report,
        )
    )
    return await update_one(session, statement, CaseRow, f"case {case_id!r}")


async def set_case_status(session: AsyncSession, case_id: str, status: CaseStatus) -> CaseRow:
    """For example `no_ai_decision` when the SLA runs out, or `closed`."""
    statement = update(CaseRow).where(CaseRow.case_id == case_id).values(status=status)
    return await update_one(session, statement, CaseRow, f"case {case_id!r}")


async def begin_case_reevaluation(
    session: AsyncSession, case_id: str, *, sla_due_at: datetime
) -> CaseRow:
    """Start the next evaluation: `evaluation_no` + 1, status `running`, new SLA deadline.

    The previous decision stays until the next one is recorded, so the new notification level
    can be compared with it (architecture §9: e-mail again only if the level rises).
    """
    statement = (
        update(CaseRow)
        .where(CaseRow.case_id == case_id)
        .values(
            evaluation_no=CaseRow.evaluation_no + 1,
            status=CaseStatus.RUNNING,
            sla_due_at=sla_due_at,
        )
    )
    return await update_one(session, statement, CaseRow, f"case {case_id!r}")


async def set_case_run_id(session: AsyncSession, case_id: str, run_id: str) -> CaseRow:
    """Temporal run ID of the case workflow after Continue-As-New."""
    statement = update(CaseRow).where(CaseRow.case_id == case_id).values(run_id=run_id)
    return await update_one(session, statement, CaseRow, f"case {case_id!r}")


async def list_cases(
    session: AsyncSession,
    *,
    statuses: Collection[CaseStatus] | None = None,
    sources: Collection[CaseSource] | None = None,
    verdicts: Collection[CaseVerdict] | None = None,
    notify_levels: Collection[Level] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
) -> list[CaseRow]:
    """Cases matching every given filter, newest first."""
    statement = select(CaseRow)
    if statuses is not None:
        statement = statement.where(CaseRow.status.in_(list(statuses)))
    if sources is not None:
        statement = statement.where(CaseRow.source.in_(list(sources)))
    if verdicts is not None:
        statement = statement.where(CaseRow.verdict.in_(list(verdicts)))
    if notify_levels is not None:
        statement = statement.where(CaseRow.notify_level.in_(list(notify_levels)))
    if created_from is not None:
        statement = statement.where(CaseRow.created_at >= created_from)
    if created_to is not None:
        statement = statement.where(CaseRow.created_at < created_to)
    statement = statement.order_by(CaseRow.created_at.desc(), CaseRow.case_id).limit(limit)
    return await fetch_all(session, statement)
