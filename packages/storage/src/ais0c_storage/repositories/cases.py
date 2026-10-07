"""`cases`: one row per case workflow, holding its latest decision.

The list functions carry the filters and the keyset the analyst API pages with (T-028):
`list_cases` and `sla_metrics`.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
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
        # The order is `created_at` descending, then `case_id` ascending, so a row-value
        # comparison would not follow it: a row after the cursor is older, or as old with a
        # larger ID.
        statement = statement.where(
            or_(
                CaseRow.created_at < after.created_at,
                and_(CaseRow.created_at == after.created_at, CaseRow.case_id > after.case_id),
            )
        )
    statement = statement.order_by(CaseRow.created_at.desc(), CaseRow.case_id).limit(limit)
    return await fetch_all(session, statement)


async def list_cases_by_ids(session: AsyncSession, case_ids: Collection[str]) -> dict[str, CaseRow]:
    """The cases among `case_ids`, by case ID; unknown IDs are left out.

    One query for a page of rows that point at cases (the analyst API's QA queue, T-028).
    """
    wanted = set(case_ids)
    if not wanted:
        return {}
    statement = select(CaseRow).where(CaseRow.case_id.in_(list(wanted)))
    return {row.case_id: row for row in await fetch_all(session, statement)}


@dataclass(frozen=True)
class SlaBucket:
    """The SLA outcome of one `floor_level` over a set of cases.

    `floor_level` is None for the cases whose floor is empty. The numbers are the cases with that
    floor: how many there are, and how their current evaluation came out. `on_time + late +
    undecided + running + closed == total`.
    """

    floor_level: Level | None
    total: int
    on_time: int
    late: int
    undecided: int
    running: int
    closed: int


async def sla_metrics(
    session: AsyncSession, *, sla_due_from: datetime, sla_due_to: datetime
) -> list[SlaBucket]:
    """How the cases whose SLA deadline is in `[sla_due_from, sla_due_to)` met it, per floor.

    Only the case's latest evaluation counts (T-63 (4)), and the status says where it stands: a
    re-evaluation keeps the previous decision's `decided_at` until it records its own
    (`begin_case_reevaluation`), so `decided_at` alone would count a running re-evaluation as
    decided.

    - `on_time` / `late`: decided (`decided`, or `closed` after a decision) at or before / after
      the deadline;
    - `undecided`: `no_ai_decision` (D-30);
    - `running`: the evaluation is still going;
    - `closed`: the offense was closed in QRadar before the AI decided.

    A re-evaluation that the offense's closing abandoned still holds the previous decision's
    `decided_at` and is counted by it: the table keeps no evaluation start to tell them apart.
    Ordered by severity, the floor-less bucket last.
    """
    decided = CaseRow.status.in_(
        [CaseStatus.DECIDED, CaseStatus.CLOSED]
    ) & CaseRow.decided_at.is_not(None)
    on_time = decided & (CaseRow.decided_at <= CaseRow.sla_due_at)
    late = decided & (CaseRow.decided_at > CaseRow.sla_due_at)
    closed = (CaseRow.status == CaseStatus.CLOSED) & CaseRow.decided_at.is_(None)
    # `count(*) FILTER (WHERE ...)` rather than `sum(boolean)`: PostgreSQL has no sum of boolean.
    statement = (
        select(
            CaseRow.floor_level,
            func.count(),
            func.count().filter(on_time),
            func.count().filter(late),
            func.count().filter(CaseRow.status == CaseStatus.NO_AI_DECISION),
            func.count().filter(CaseRow.status == CaseStatus.RUNNING),
            func.count().filter(closed),
        )
        .where(CaseRow.sla_due_at >= sla_due_from, CaseRow.sla_due_at < sla_due_to)
        .group_by(CaseRow.floor_level)
    )
    rows = await session.execute(statement)
    buckets = [
        SlaBucket(
            floor_level=floor_level,
            total=total,
            on_time=on_time_count,
            late=late_count,
            undecided=undecided_count,
            running=running_count,
            closed=closed_count,
        )
        for (
            floor_level,
            total,
            on_time_count,
            late_count,
            undecided_count,
            running_count,
            closed_count,
        ) in rows
    ]
    return sorted(buckets, key=_floor_order)


def _floor_order(bucket: SlaBucket) -> tuple[int, str]:
    """Critical first, low last, and the floor-less bucket after every level."""
    order = (Level.CRITICAL, Level.HIGH, Level.MEDIUM, Level.LOW)
    if bucket.floor_level is None:
        return (len(order), "")
    return (order.index(bucket.floor_level), bucket.floor_level.value)
