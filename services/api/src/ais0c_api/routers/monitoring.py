"""Monitoring and administration: the SLA metric, the platform flags, `/me` and `/health`
(api.md "İzleme ve yönetim"; T-028 criteria 1, 2, 10 and 11, T-23, T-63 (2)).

The kill switch lives here: `GET /admin/platform-flags` is an operator's, `PUT` is an admin's and
needs a reason. Switching off is always one step (an emergency stop); whether switching on goes
through double control (D-36) is T-033's decision (T-63 (2)). The flag row and its audit entry are
written by `set_platform_flag`, in one transaction, so this module writes no second one.

`/health` asks for nothing and says nothing about the database, the version or the settings.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from ais0c_api.dependencies import ADMIN, OPERATOR, ReadSession, WriteSession, aware
from ais0c_api.models import (
    Health,
    Me,
    PlatformFlagState,
    PlatformFlagUpdate,
    SLAMetrics,
    SlaRow,
)
from ais0c_api.problems import Problem, not_found
from ais0c_contracts import Level
from ais0c_storage.enums import ActorKind, PlatformFlag
from ais0c_storage.models import PlatformFlagRow
from ais0c_storage.repositories import list_platform_flags, set_platform_flag, sla_metrics

router = APIRouter(tags=["monitoring"])

# Without a range, the metric covers the last day.
DEFAULT_SLA_WINDOW = timedelta(days=1)

FromParam = Annotated[datetime | None, Query(alias="from")]
ToParam = Annotated[datetime | None, Query()]


@router.get("/health", response_model=Health)
async def health() -> Health:
    """Liveness only: no authentication, no version, no database detail."""
    return Health()


@router.get("/me", response_model=Me)
async def me(user: OPERATOR) -> Me:
    """The session's subject, display name and roles, most privileged first."""
    return Me(subject=user.subject, display_name=user.display_name, roles=user.role_names)


@router.get("/metrics/sla", response_model=SLAMetrics)
async def get_sla(
    session: ReadSession,
    _user: OPERATOR,
    from_: FromParam = None,
    to: ToParam = None,
) -> SLAMetrics:
    """How the cases whose SLA deadline falls in the range met it, per `floor_level`.

    `from` is inclusive and `to` exclusive; both are ISO 8601 with a time zone. Without a range the
    last day is used. The cases with no floor are the `none` bucket, and only a case's latest
    evaluation counts (T-63 (4)): there is no evaluation history yet.
    """
    # `aware` returns a UTC datetime or raises, so neither of these can be None here.
    end = aware(to, "to") or datetime.now(UTC)
    start = aware(from_, "from") or end - DEFAULT_SLA_WINDOW
    if start >= end:
        raise Problem(400, "request.invalid_range", detail="from must be before to")
    buckets = await sla_metrics(session, sla_due_from=start, sla_due_to=end)
    return SLAMetrics(
        from_=start,
        to=end,
        buckets=[
            SlaRow(
                floor_level="none" if bucket.floor_level is None else Level(bucket.floor_level),
                total=bucket.total,
                on_time=bucket.on_time,
                late=bucket.late,
                undecided=bucket.undecided,
                running=bucket.running,
            )
            for bucket in buckets
        ],
    )


def flag_state(name: PlatformFlag, row: PlatformFlagRow | None) -> PlatformFlagState:
    """The state of `name`; `row` is None for a flag that was never set, which means off."""
    if row is None:
        return PlatformFlagState(name=name, enabled=False)
    return PlatformFlagState(
        name=name,
        enabled=row.enabled,
        reason=row.reason,
        changed_by=row.changed_by,
        changed_at=row.changed_at,
    )


@router.get("/admin/platform-flags", response_model=list[PlatformFlagState])
async def get_flags(session: ReadSession, _user: OPERATOR) -> list[PlatformFlagState]:
    """Every flag the platform knows, on or off. A flag with no row is off and `changed_by` empty.

    Today the only flag is the kill switch `writes_enabled` (T-23).
    """
    rows = {row.name: row for row in await list_platform_flags(session)}
    return [flag_state(name, rows.get(name)) for name in PlatformFlag]


@router.put("/admin/platform-flags/{name}", response_model=PlatformFlagState)
async def put_flag(
    name: str, body: PlatformFlagUpdate, session: WriteSession, user: ADMIN
) -> PlatformFlagState:
    """Switch a platform flag (admin). `reason` is required and may not be blank. An unknown flag
    name is a 404 and nothing is written."""
    try:
        flag = PlatformFlag(name)
    except ValueError:
        raise not_found("platform_flag.not_found") from None
    if not body.reason.strip():
        raise Problem(
            422, "platform_flag.reason_required", detail="a reason is required to change a flag"
        )
    try:
        row = await set_platform_flag(
            session,
            flag,
            enabled=body.enabled,
            reason=body.reason,
            actor_kind=ActorKind.USER,
            actor_id=user.subject,
        )
    except ValueError as error:
        raise Problem(
            422, "platform_flag.invalid", detail="the flag change is not acceptable"
        ) from error
    return flag_state(flag, row)
