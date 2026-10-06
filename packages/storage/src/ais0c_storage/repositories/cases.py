"""`cases`: one row per case workflow, holding its latest decision.

The list functions carry the filters and the keyset the analyst API pages with (T-028):
`list_cases` and `sla_metrics`.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import CaseReport, CaseSource, CaseVerdict, Confidence, Level
from ais0c_storage.columns import revalidate
from ais0c_storage.enums import CaseStatus
from ais0c_storage.models import CaseRow, OffenseSeenRow
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


@dataclass(frozen=True)
class CaseCursor:
    """Where a case page ended: the row's own `created_at` and `case_id`."""

    created_at: datetime
    case_id: str


def newest_case_cursor(row: CaseRow) -> CaseCursor:
    return CaseCursor(created_at=row.created_at, case_id=row.case_id)


async def list_cases(
    session: AsyncSession,
    *,
    statuses: Collection[CaseStatus] | None = None,
    sources: Collection[CaseSource] | None = None,
    verdicts: Collection[CaseVerdict] | None = None,
    notify_levels: Collection[Level] | None = None,
    rule_ids: Collection[int] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    after: CaseCursor | None = None,
    limit: int = 50,
) -> list[CaseRow]:
    """Cases matching every given filter, newest first.

    `rule_ids` keeps the cases whose offense carries any of the given QRadar rule IDs; a case
    of a hunt or a group has no rule and is never kept. `after` is the last row of the previous
    page (`newest_case_cursor`): the page starts after it in the same order, so a page never
    repeats or skips a row whose `created_at` is the same as its neighbours'.
    """
    statement = select(CaseRow)
    if statuses is not None:
        statement = statement.where(CaseRow.status.in_(list(statuses)))
    if sources is not None:
        statement = statement.where(CaseRow.source.in_(list(sources)))
    if verdicts is not None:
        statement = statement.where(CaseRow.verdict.in_(list(verdicts)))
    if notify_levels is not None:
        statement = statement.where(CaseRow.notify_level.in_(list(notify_levels)))
    if rule_ids is not None:
        statement = statement.where(
            CaseRow.offense_id.in_(
                select(OffenseSeenRow.offense_id).where(
                    OffenseSeenRow.rule_ids.overlap(list(rule_ids))
                )
            )
        )
    if created_from is not None:
        statement = statement.where(CaseRow.created_at >= created_from)
    if created_to is not None:
        statement = statement.where(CaseRow.created_at < created_to)
    if after is not None:
        statement = statement.where(
            tuple_(CaseRow.created_at, CaseRow.case_id) < tuple_(after.created_at, after.case_id)
        )
    statement = statement.order_by(CaseRow.created_at.desc(), CaseRow.case_id).limit(limit)
    return await fetch_all(session, statement)


@dataclass(frozen=True)
class SlaBucket:
    """The SLA outcome of one `floor_level` over a set of cases.

    `floor_level` is None for the cases whose floor is empty. The five numbers are the cases
    with that floor: how many there are, and how their decision came out. They add up to
    `total`.
    """

    floor_level: Level | None
    total: int
    on_time: int
    late: int
    undecided: int
    running: int


async def sla_metrics(
    session: AsyncSession, *, sla_due_from: datetime, sla_due_to: datetime
) -> list[SlaBucket]:
    """How the cases whose SLA deadline is in `[sla_due_from, sla_due_to)` met it, per floor.

    Only the case's own latest evaluation counts: `decided_at` is where the current decision was
    recorded, and a case that is still `running` has none yet. `on_time` decided at or before
    its deadline, `late` after it, `undecided` reached `no_ai_decision` (D-30) and `running` is
    still going. Ordered by severity, the floor-less bucket last.
    """
    decided = CaseRow.decided_at.is_not(None)
    on_time = decided & (CaseRow.decided_at <= CaseRow.sla_due_at)
    late = decided & (CaseRow.decided_at > CaseRow.sla_due_at)
    # `count(*) FILTER (WHERE ...)` rather than `sum(boolean)`: PostgreSQL has no sum of boolean.
    statement = (
        select(
            CaseRow.floor_level,
            func.count(),
            func.count().filter(on_time),
            func.count().filter(late),
            func.count().filter(CaseRow.status == CaseStatus.NO_AI_DECISION),
            func.count().filter(CaseRow.status == CaseStatus.RUNNING),
        )
        .where(CaseRow.sla_due_at >= sla_due_from, CaseRow.sla_due_at < sla_due_to)
        .group_by(CaseRow.floor_level)
    )
    rows = await session.execute(statement)
    buckets = [
        SlaBucket(
            floor_level=floor_level,
            total=total,
            on_time=int(on_time or 0),
            late=int(late or 0),
            undecided=int(undecided or 0),
            running=int(running or 0),
        )
        for floor_level, total, on_time, late, undecided, running in rows
    ]
    return sorted(buckets, key=_floor_order)


def _floor_order(bucket: SlaBucket) -> tuple[int, str]:
    """Critical first, low last, and the floor-less bucket after every level."""
    order = (Level.CRITICAL, Level.HIGH, Level.MEDIUM, Level.LOW)
    if bucket.floor_level is None:
        return (len(order), "")
    return (order.index(bucket.floor_level), bucket.floor_level.value)
