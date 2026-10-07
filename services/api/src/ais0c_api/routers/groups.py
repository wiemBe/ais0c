"""Offense groups: the open groups and storms, what they hold and how they were decided
(api.md "Gruplar"; T-028 criterion 6).

The deterministic summary of a group comes with T-027; this task returns the group row, its
offenses and whatever the group's own case holds.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from ais0c_api.dependencies import (
    OPERATOR,
    FromParam,
    ReadSession,
    ToParam,
    aware,
    offense_url_of,
)
from ais0c_api.models import (
    GroupDetail,
    GroupDigest,
    GroupOffense,
    GroupSummary,
    GroupValueCount,
    GroupValueKindSummary,
    Page,
)
from ais0c_api.pagination import DEFAULT_LIMIT, CursorParam, LimitParam, paginate, timestamp_cursor
from ais0c_api.problems import not_found
from ais0c_storage.enums import GroupStatus, OffenseStatus
from ais0c_storage.models import OffenseGroupRow, OffenseSeenRow
from ais0c_storage.repositories import (
    count_group_values,
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


# The most frequent values of a kind the summary names.
TOP_VALUES = 5


def offense_row(row: OffenseSeenRow, url: str | None) -> GroupOffense:
    return GroupOffense(
        offense_id=row.offense_id,
        description=row.description,
        status=row.status,
        case_id=row.case_id,
        first_seen_at=row.first_seen_at,
        full_analysis_reason=row.full_analysis_reason,
        qradar_offense_url=url,
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
async def get_group(
    request: Request, group_id: str, _user: OPERATOR, session: ReadSession
) -> GroupDetail:
    """The group, the offenses it holds, the deterministic summary and the decision of its own
    case.

    The summary is counted from the group's rows (no model): the number of offenses, the time
    range, the rules, and for each kind of value the number of different values and the most
    frequent ones.

    A group with no case yet (still being filled) answers with the group and its offenses; an
    unknown `group_id` is a 404.
    """
    row = await get_offense_group(session, group_id)
    if row is None:
        raise not_found("group.not_found")
    members = await list_group_offenses(session, group_id)
    offenses = [
        offense_row(offense, offense_url_of(request, offense.offense_id)) for offense in members
    ]
    counts = await count_group_values(
        session, group_id, top=TOP_VALUES, statuses=list(OffenseStatus)
    )
    digest = GroupDigest(
        offense_count=len(members),
        first_seen_at=members[0].first_seen_at if members else None,
        last_seen_at=max((m.first_seen_at for m in members), default=None),
        rule_ids=sorted({rule for m in members for rule in m.rule_ids}),
        values=[
            GroupValueKindSummary(
                kind=kind,
                distinct=count.distinct,
                top=[GroupValueCount(value=value, offenses=n) for value, n in count.top],
            )
            for kind, count in sorted(counts.items(), key=lambda item: item[0].value)
        ],
    )
    case = None if row.case_id is None else await get_case(session, row.case_id)
    return GroupDetail(
        group=summary(row),
        offenses=offenses,
        summary=digest,
        case_id=case.case_id if case is not None else None,
        case_status=case.status if case is not None else None,
        verdict=case.verdict if case is not None else None,
        notify_level=case.notify_level if case is not None else None,
        report=case.report if case is not None else None,
    )
