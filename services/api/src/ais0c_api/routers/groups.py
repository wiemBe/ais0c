"""Offense groups: the open groups and storms, what they hold and how they were decided
(api.md "Gruplar"; T-028 criterion 6).

The deterministic summary of a group comes with T-027; this task returns the group row, its
offenses and whatever the group's own case holds.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from ais0c_api.dependencies import OPERATOR, FromParam, ReadSession, ToParam, aware
from ais0c_api.models import GroupDetail, GroupOffense, GroupSummary, Page
from ais0c_api.pagination import DEFAULT_LIMIT, CursorParam, LimitParam, paginate, timestamp_cursor
from ais0c_api.problems import not_found
from ais0c_storage.enums import GroupStatus
from ais0c_storage.models import OffenseGroupRow, OffenseSeenRow
from ais0c_storage.repositories import (
    get_case,
    get_offense_group,
    list_group_offenses,
    list_offense_groups,
)
from ais0c_storage.repositories.offenses import GroupCursor

router = APIRouter(prefix="/groups", tags=["groups"])

StatusFilter = Annotated[list[GroupStatus] | None, Query()]


def group_cursor(cursor: str | None) -> GroupCursor | None:
    """The `?cursor=` value as a group cursor; anything unreadable is a 400."""
    key = timestamp_cursor(cursor, field="cursor")
    if key is None:
        return None
    window_start, group_id = key
    return GroupCursor(window_start=window_start, group_id=group_id)


def summary(row: OffenseGroupRow) -> GroupSummary:
    return GroupSummary(
        group_id=row.group_id,
        rule_set_hash=row.rule_set_hash,
        window_start=row.window_start,
        window_end=row.window_end,
        offense_count=row.offense_count,
        status=row.status,
        case_id=row.case_id,
    )


def offense_row(row: OffenseSeenRow) -> GroupOffense:
    return GroupOffense(
        offense_id=row.offense_id,
        description=row.description,
        status=row.status,
        case_id=row.case_id,
        first_seen_at=row.first_seen_at,
    )


@router.get("", response_model=Page[GroupSummary])
async def get_groups(
    _user: OPERATOR,
    session: ReadSession,
    status: StatusFilter = None,
    from_: FromParam = None,
    to: ToParam = None,
    cursor: CursorParam = None,
    limit: LimitParam = DEFAULT_LIMIT,
) -> Page[GroupSummary]:
    """The groups, newest window first. `from` and `to` bound `window_start`."""
    rows = await list_offense_groups(
        session,
        statuses=set(status) if status else None,
        window_from=aware(from_, "from"),
        window_to=aware(to, "to"),
        after=group_cursor(cursor),
        limit=limit + 1,
    )
    page, next_cursor = paginate(
        rows, limit, lambda row: [row.window_start.isoformat(), row.group_id]
    )
    return Page(items=[summary(row) for row in page], next_cursor=next_cursor)


@router.get("/{group_id}", response_model=GroupDetail)
async def get_group(group_id: str, _user: OPERATOR, session: ReadSession) -> GroupDetail:
    """The group, the offenses it holds and the decision of its own case.

    A group with no case yet (still being filled) answers with the group and its offenses; an
    unknown `group_id` is a 404.
    """
    row = await get_offense_group(session, group_id)
    if row is None:
        raise not_found("group.not_found")
    offenses = [offense_row(offense) for offense in await list_group_offenses(session, group_id)]
    case = None if row.case_id is None else await get_case(session, row.case_id)
    return GroupDetail(
        group=summary(row),
        offenses=offenses,
        case_id=case.case_id if case is not None else None,
        case_status=case.status if case is not None else None,
        verdict=case.verdict if case is not None else None,
        notify_level=case.notify_level if case is not None else None,
        report=case.report if case is not None else None,
    )
