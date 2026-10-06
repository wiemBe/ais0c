"""Critical assets, the named recipient groups and the routing table (D-41, T-43;
api.md "Kritik varlıklar ve alıcılar"; T-028 criterion 9).

An address whose domain is not on the allowlist refuses the whole request (422) and nothing
changes: one address outside the list is enough. Routing to a group that has no members is refused
the same way, so `notification_routes` never names a group `notification_recipients` has not got.
"""

import uuid

from fastapi import APIRouter, Response
from pydantic import ValidationError

from ais0c_api import audit
from ais0c_api.dependencies import ADMIN, OPERATOR, ReadSession, WriteSession
from ais0c_api.models import (
    CriticalAsset,
    CriticalAssetAdd,
    NotificationRoute,
    NotificationRoutesUpdate,
    RecipientGroup,
    RecipientGroupUpdate,
    RecipientsView,
)
from ais0c_api.problems import Problem, not_found
from ais0c_contracts import EmailKind, Level
from ais0c_storage.repositories import (
    add_critical_asset,
    check_recipient_domains,
    delete_critical_asset,
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
async def get_critical_assets(session: ReadSession, _user: OPERATOR) -> list[CriticalAsset]:
    """The hand-kept critical asset list, by kind then value (architecture §9)."""
    return [
        CriticalAsset(id=row.id, kind=row.kind, value=row.value, label=row.label, level=row.level)
        for row in await list_critical_assets(session)
    ]


@router.post("/critical-assets", response_model=CriticalAsset, status_code=201)
async def post_critical_asset(
    body: CriticalAssetAdd, session: WriteSession, user: ADMIN
) -> CriticalAsset:
    """Add a critical asset (admin). Storage normalizes an IP or CIDR and validates the level; a
    value it refuses is a 422 and nothing is written."""
    try:
        row = await add_critical_asset(
            session, kind=body.kind, value=body.value, label=body.label, level=body.level
        )
    except (ValueError, ValidationError) as error:
        raise Problem(
            422, "critical_asset.invalid", detail="the asset is not a usable critical asset"
        ) from error
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CRITICAL_ASSET_ADD,
        object_type=audit.OBJECT_CRITICAL_ASSET,
        object_id=str(row.id),
        details={
            "kind": row.kind.value,
            "value": row.value,
            "label": row.label,
            "level": row.level.value,
        },
    )
    return CriticalAsset(
        id=row.id, kind=row.kind, value=row.value, label=row.label, level=row.level
    )


@router.delete("/critical-assets/{asset_id}", status_code=204, response_class=Response)
async def delete_asset(asset_id: uuid.UUID, session: WriteSession, user: ADMIN) -> Response:
    """Remove a critical asset (admin); an unknown ID is a 404 and nothing is written."""
    row = await get_critical_asset(session, asset_id)
    if row is None:
        raise not_found("critical_asset.not_found")
    await delete_critical_asset(session, asset_id)
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CRITICAL_ASSET_DELETE,
        object_type=audit.OBJECT_CRITICAL_ASSET,
        object_id=str(asset_id),
        details={
            "kind": row.kind.value,
            "value": row.value,
            "label": row.label,
            "level": row.level.value,
        },
    )
    return Response(status_code=204)


@router.get("/notification-recipients", response_model=RecipientsView)
async def get_recipients(session: ReadSession, _user: ADMIN) -> RecipientsView:
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
    list_name: str, body: RecipientGroupUpdate, session: WriteSession, user: ADMIN
) -> RecipientGroup:
    """Replace a recipient group's whole membership (admin). A new group name creates the group.

    One address whose domain is not on the allowlist refuses the whole request: nothing is
    written, and the group keeps the members it had. The address's local part is kept and its
    domain is lowercased, so `Soc-1@EXAMPLE.COM` is stored as `Soc-1@example.com`.
    """
    refused = await check_recipient_domains(session, domains_of(body.emails))
    if refused:
        raise Problem(
            422,
            "notification_recipients.domain_not_allowed",
            detail="an address is outside the allowed e-mail domains",
            extra={"domains": refused},
        )
    try:
        rows = await replace_notification_recipients(session, list_name, body.emails)
    except ValueError as error:
        raise Problem(
            422,
            "notification_recipients.invalid",
            detail="the group name or an address is not acceptable",
        ) from error
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_RECIPIENTS_REPLACE,
        object_type=audit.OBJECT_RECIPIENT_GROUP,
        object_id=list_name,
        details={"members": len(rows)},
    )
    return RecipientGroup(list_name=list_name, emails=[row.email for row in rows])


@router.get("/notification-routes", response_model=list[NotificationRoute])
async def get_routes(session: ReadSession, _user: ADMIN) -> list[NotificationRoute]:
    """The routing table: which alert kind and level goes to which groups (admin)."""
    return [
        NotificationRoute(kind=row.kind, level=row.level, list_name=row.list_name)
        for row in await list_notification_routes(session)
    ]


@router.put("/notification-routes", response_model=list[NotificationRoute])
async def put_routes(
    body: NotificationRoutesUpdate, session: WriteSession, user: ADMIN
) -> list[NotificationRoute]:
    """Replace the whole routing table (admin).

    A route to a group that has no members is refused (422) and the table keeps what it had. The
    replacement is one transaction, so the executor never reads a half-written table.
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
    rows = await replace_notification_routes(
        session,
        [
            (
                EmailKind(route.kind),
                None if route.level is None else Level(route.level),
                route.list_name,
            )
            for route in body.routes
        ],
    )
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_ROUTES_REPLACE,
        object_type=audit.OBJECT_ROUTES_TABLE,
        object_id="notification_routes",
        details={"routes": len(rows)},
    )
    return [
        NotificationRoute(kind=row.kind, level=row.level, list_name=row.list_name) for row in rows
    ]
