"""Critical assets, the named recipient groups and the routing table (D-41, T-43;
api.md "Kritik varlıklar ve alıcılar"; T-028 criterion 9).

An address whose domain is not on the allowlist refuses the whole request (422) and nothing
changes: one address outside the list is enough. Routing to a group that has no members is refused
the same way, and so is emptying a group a route names (409), so a change made here never leaves
`notification_routes` naming a group `notification_recipients` has not got. (The seed of 0007
routes to groups nobody has filled yet; an admin fills them or drops their routes.)

The audit rows say what changed: the addresses added to and removed from a group, and the routing
table before and after.
"""

import uuid

from fastapi import APIRouter
from pydantic import JsonValue, ValidationError

from ais0c_api import audit
from ais0c_api.changes import (
    ABSENT,
    ACTION_ADD,
    ACTION_DELETE,
    asset_key,
    asset_values,
    asset_version,
    queue_change,
)
from ais0c_api.dependencies import ADMIN, OPERATOR, ReadSession, WriteSession
from ais0c_api.models import (
    ChangeAccepted,
    CriticalAsset,
    CriticalAssetAdd,
    NotificationRoute,
    NotificationRoutesUpdate,
    RecipientGroup,
    RecipientGroupUpdate,
    RecipientsView,
)
from ais0c_api.problems import Problem, not_found
from ais0c_storage.enums import ChangeObjectType
from ais0c_storage.models import NotificationRouteRow
from ais0c_storage.repositories import (
    check_critical_asset,
    check_recipient_domains,
    find_critical_asset,
    get_critical_asset,
    list_allowed_email_domains,
    list_critical_assets,
    list_notification_recipients,
    list_notification_routes,
    replace_notification_recipients,
    replace_notification_routes,
)

router = APIRouter(tags=["administration"])


def domains_of(emails: list[str]) -> list[str]:
    """The domain of every address; an address with no `@` keeps its own text, so it is refused."""
    return [email.rpartition("@")[2] or email for email in emails]


@router.get("/critical-assets", response_model=list[CriticalAsset])
async def get_critical_assets(_user: OPERATOR, session: ReadSession) -> list[CriticalAsset]:
    """The hand-kept critical asset list, by kind then value (architecture §9)."""
    return [
        CriticalAsset(id=row.id, kind=row.kind, value=row.value, label=row.label, level=row.level)
        for row in await list_critical_assets(session)
    ]


@router.post("/critical-assets", response_model=ChangeAccepted, status_code=202)
async def post_critical_asset(
    body: CriticalAssetAdd, user: ADMIN, session: WriteSession
) -> ChangeAccepted:
    """Ask to add a critical asset (admin); a second admin's approval adds it (202).

    Storage's checks run now, so a value it refuses is a 422 and nothing waits. An asset of this
    kind and value that is already listed is a 409 (`critical_asset.exists`), and one that already
    waits for approval a 409 (`change.pending_exists`).
    """
    try:
        value = check_critical_asset(
            kind=body.kind, value=body.value, label=body.label, level=body.level
        )
    except (ValueError, ValidationError) as error:
        raise Problem(
            422, "critical_asset.invalid", detail="the asset is not a usable critical asset"
        ) from error
    if await find_critical_asset(session, kind=body.kind, value=value) is not None:
        raise Problem(409, "critical_asset.exists", detail="the asset is already listed")
    after = body.model_dump(mode="json")
    after["value"] = value
    return await queue_change(
        session,
        user,
        object_type=ChangeObjectType.CRITICAL_ASSET,
        object_id=asset_key(body.kind.value, value),
        object_version=ABSENT,
        action=ACTION_ADD,
        before=None,
        after=after,
    )


@router.delete("/critical-assets/{asset_id}", response_model=ChangeAccepted, status_code=202)
async def delete_asset(asset_id: uuid.UUID, user: ADMIN, session: WriteSession) -> ChangeAccepted:
    """Ask to remove a critical asset (admin); a second admin's approval removes it (202). An
    unknown ID is a 404 and nothing waits."""
    row = await get_critical_asset(session, asset_id)
    if row is None:
        raise not_found("critical_asset.not_found")
    return await queue_change(
        session,
        user,
        object_type=ChangeObjectType.CRITICAL_ASSET,
        object_id=str(asset_id),
        object_version=asset_version(row),
        action=ACTION_DELETE,
        before=asset_values(row),
        after=None,
    )


@router.get("/notification-recipients", response_model=RecipientsView)
async def get_recipients(_user: ADMIN, session: ReadSession) -> RecipientsView:
    """The named recipient groups and the domain allowlist that guards them (admin)."""
    groups: dict[str, list[str]] = {}
    for row in await list_notification_recipients(session):
        groups.setdefault(row.list_name, []).append(row.email)
    return RecipientsView(
        groups=[RecipientGroup(list_name=name, emails=groups[name]) for name in sorted(groups)],
        allowed_domains=[row.domain for row in await list_allowed_email_domains(session)],
    )


@router.put("/notification-recipients/{list_name}", response_model=RecipientGroup)
async def put_recipients(
    list_name: str, body: RecipientGroupUpdate, user: ADMIN, session: WriteSession
) -> RecipientGroup:
    """Replace a recipient group's whole membership (admin). A new group name creates the group.

    One address whose domain is not on the allowlist refuses the whole request: nothing is
    written, and the group keeps the members it had. The address's local part is kept and its
    domain is lowercased, so `Soc-1@EXAMPLE.COM` is stored as `Soc-1@example.com`. An empty list
    removes the group, unless a route names it (409 `notification_recipients.group_in_use`).
    """
    refused = await check_recipient_domains(session, domains_of(body.emails))
    if refused:
        raise Problem(
            422,
            "notification_recipients.domain_not_allowed",
            detail="an address is outside the allowed e-mail domains",
            extra={"domains": refused},
        )
    if not body.emails and any(
        route.list_name == list_name for route in await list_notification_routes(session)
    ):
        raise Problem(
            409,
            "notification_recipients.group_in_use",
            detail="a route names this group; change the routes before emptying it",
        )
    before = {
        row.email
        for row in await list_notification_recipients(session)
        if row.list_name == list_name
    }
    try:
        rows = await replace_notification_recipients(session, list_name, body.emails)
    except ValueError as error:
        raise Problem(
            422,
            "notification_recipients.invalid",
            detail="the group name or an address is not acceptable",
        ) from error
    after = [row.email for row in rows]
    added: list[JsonValue] = [email for email in sorted(set(after) - before)]
    removed: list[JsonValue] = [email for email in sorted(before - set(after))]
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_RECIPIENTS_REPLACE,
        object_type=audit.OBJECT_RECIPIENT_GROUP,
        object_id=list_name,
        details={
            "members": len(rows),
            "added": added,
            "removed": removed,
        },
    )
    return RecipientGroup(list_name=list_name, emails=after)


def route_items(rows: list[NotificationRouteRow]) -> list[NotificationRoute]:
    return [
        NotificationRoute(kind=row.kind, level=row.level, list_name=row.list_name) for row in rows
    ]


def route_details(routes: list[NotificationRoute]) -> list[JsonValue]:
    """The routing table as an audit row holds it."""
    return [route.model_dump(mode="json") for route in routes]


@router.get("/notification-routes", response_model=list[NotificationRoute])
async def get_routes(_user: ADMIN, session: ReadSession) -> list[NotificationRoute]:
    """The routing table: which alert kind and level goes to which groups (admin)."""
    return route_items(await list_notification_routes(session))


@router.put("/notification-routes", response_model=list[NotificationRoute])
async def put_routes(
    body: NotificationRoutesUpdate, user: ADMIN, session: WriteSession
) -> list[NotificationRoute]:
    """Replace the whole routing table (admin).

    A route to a group that has no members is refused (422) and the table keeps what it had. A
    case or group alert names a level and a hunt report none; another route would never match an
    e-mail and is a 422 as well. The replacement is one transaction, so the executor never reads a
    half-written table.
    """
    known = {row.list_name for row in await list_notification_recipients(session)}
    unknown = sorted({route.list_name for route in body.routes if route.list_name not in known})
    if unknown:
        raise Problem(
            422,
            "notification_routes.unknown_group",
            detail="a route names a recipient group that has no members",
            extra={"list_names": unknown},
        )
    before = route_items(await list_notification_routes(session))
    rows = await replace_notification_routes(
        session, [(route.kind, route.level, route.list_name) for route in body.routes]
    )
    after = route_items(rows)
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_ROUTES_REPLACE,
        object_type=audit.OBJECT_ROUTES_TABLE,
        object_id="notification_routes",
        details={
            "routes": len(rows),
            "before": route_details(before),
            "after": route_details(after),
        },
    )
    return after
