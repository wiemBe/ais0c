"""Double control (D-36, T-77): requests that wait for a second admin, and what approving one does.

A change the API takes from an admin is not written. It becomes a `pending` `change_approvals` row
that names the object, the object's version at that moment (`object_version`) and what was asked
(`change`: `{ action, before, after }`). A second admin approves it, and the approval writes the
change, the request's end and both audit rows in one transaction.

An object's version is a hash of the fields an admin can change, so a sync that touches other
columns does not make a request stale, but any edit of what the request is about does. A request
whose object changed after it was made is `stale`: approving it ends it (409) and writes nothing.

Closing the kill switch is not a request (T-63): it is written at once and ends a pending request
to open it as `stale`. Nothing here is a model's: only an admin's session reaches this module.
"""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi.responses import JSONResponse
from pydantic import JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_api import audit
from ais0c_api.auth import Session
from ais0c_api.dependencies import now
from ais0c_api.models import (
    CatalogLogSourceUpdate,
    CatalogRuleUpdate,
    ChangeAccepted,
    ChangeItem,
    CriticalAssetAdd,
)
from ais0c_api.problems import Problem, problem_response
from ais0c_storage.enums import (
    ActorKind,
    ChangeObjectType,
    ChangeRejectReason,
    ChangeStatus,
    PlatformFlag,
)
from ais0c_storage.errors import DuplicateError, NotFoundError
from ais0c_storage.models import (
    CatalogLogSourceRow,
    CatalogRuleRow,
    ChangeApprovalRow,
    CriticalAssetRow,
    PlatformFlagRow,
)
from ais0c_storage.repositories import (
    accept_catalog_rule_draft,
    add_critical_asset,
    decide_change,
    delete_critical_asset,
    find_critical_asset,
    get_catalog_log_source,
    get_catalog_rule,
    get_critical_asset,
    get_pending_change,
    get_platform_flag,
    request_change,
    set_platform_flag,
    update_catalog_log_source,
    update_catalog_rule,
)

# `change.action` values.
ACTION_UPDATE: Final = "update"
ACTION_ACCEPT_DRAFT: Final = "accept_draft"
ACTION_ADD: Final = "add"
ACTION_DELETE: Final = "delete"
ACTION_ENABLE: Final = "enable"

# `object_version` of an asset that is not in the list yet.
ABSENT: Final = "absent"
# `object_version` of a flag that was never set.
UNSET: Final = "unset"


def _digest(value: JsonValue) -> str:
    """A short, stable hash of `value`; the key order and the spacing do not matter."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:32]


# --- what an admin can change of each object, and its version -----------------------------------


def rule_values(row: CatalogRuleRow) -> dict[str, JsonValue]:
    """The rule's fields an edit changes (`PUT /catalog/rules/{id}`), as a request shows them."""
    return {
        "mode": row.mode.value,
        "min_level": None if row.min_level is None else row.min_level.value,
        "has_automated_action": row.has_automated_action,
        "context_note": row.context_note,
        "attack_techniques": list(row.attack_techniques),
    }


def rule_version(row: CatalogRuleRow) -> str:
    """Everything an admin or the AI can change of a rule: the edit's fields, `defined` and the
    AI's draft. What the sync writes (names, `qradar_enabled`, `missing_since`) is not in it."""
    return _digest({**rule_values(row), "defined": row.defined, "ai_draft_note": row.ai_draft_note})


def log_source_values(row: CatalogLogSourceRow) -> dict[str, JsonValue]:
    return {
        "description": row.description,
        "owner": row.owner,
        "criticality": None if row.criticality is None else row.criticality.value,
        "in_scope": row.in_scope,
        "context_note": row.context_note,
        "telemetry_classes": (
            None
            if row.telemetry_classes is None
            else list[JsonValue](sorted(row.telemetry_classes))
        ),
    }


def log_source_version(row: CatalogLogSourceRow) -> str:
    return _digest({**log_source_values(row), "defined": row.defined})


def asset_values(row: CriticalAssetRow) -> dict[str, JsonValue]:
    return {
        "kind": row.kind.value,
        "value": row.value,
        "label": row.label,
        "level": row.level.value,
    }


def asset_version(row: CriticalAssetRow) -> str:
    return _digest(asset_values(row))


def asset_key(kind: str, value: str) -> str:
    """The `object_id` of an asset that is not in the list yet: its kind and normalized value, so
    two requests to add the same asset meet at one pending row."""
    return f"{kind}:{value}"


def flag_version(row: PlatformFlagRow | None) -> str:
    if row is None:
        return UNSET
    return _digest({"enabled": row.enabled, "changed_at": row.changed_at.isoformat()})


# --- asking -------------------------------------------------------------------------------------


async def queue_change(
    session: AsyncSession,
    user: Session,
    *,
    object_type: ChangeObjectType,
    object_id: str,
    object_version: str,
    action: str,
    before: JsonValue,
    after: JsonValue,
    extra: dict[str, JsonValue] | None = None,
) -> ChangeAccepted:
    """Write a `pending` request and its `change.request` audit row.

    An object that already has a pending request is a 409 (`change.pending_exists`) that names it.
    """
    change: dict[str, JsonValue] = {"action": action, "before": before, "after": after}
    change.update(extra or {})
    try:
        row = await request_change(
            session,
            object_type=object_type,
            object_id=object_id,
            object_version=object_version,
            change=change,
            requested_by=user.subject,
        )
    except DuplicateError:
        existing = await get_pending_change(session, object_type, object_id)
        raise Problem(
            409,
            "change.pending_exists",
            detail="the object already has a change waiting for approval",
            extra={"change_id": None if existing is None else str(existing.id)},
        ) from None
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CHANGE_REQUEST,
        object_type=audit.OBJECT_CHANGE,
        object_id=str(row.id),
        details={"object_type": object_type.value, "object_id": object_id, "action": action},
    )
    return ChangeAccepted(change_id=row.id)


def accepted(change: ChangeAccepted) -> JSONResponse:
    """The 202 answer of a request that now waits."""
    return JSONResponse(change.model_dump(mode="json"), status_code=202)


# --- the end of a request -----------------------------------------------------------------------


def item(row: ChangeApprovalRow) -> ChangeItem:
    change = row.change if isinstance(row.change, dict) else {}
    return ChangeItem(
        id=row.id,
        object_type=row.object_type,
        object_id=row.object_id,
        object_version=row.object_version,
        change=change,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        status=row.status,
        reason=row.reason,
        comment=row.comment,
    )


async def mark_stale(
    session: AsyncSession, row: ChangeApprovalRow, *, actor_id: str, cause: str
) -> ChangeApprovalRow:
    """End a pending request whose object changed; nobody decided it, so `decided_by` stays empty."""
    ended = await decide_change(
        session, row.id, status=ChangeStatus.REJECTED, reason=ChangeRejectReason.STALE
    )
    await audit.record(
        session,
        actor_id=actor_id,
        action=audit.ACTION_CHANGE_STALE,
        object_type=audit.OBJECT_CHANGE,
        object_id=str(row.id),
        details={
            "object_type": row.object_type.value,
            "object_id": row.object_id,
            "requested_by": row.requested_by,
            "cause": cause,
        },
    )
    return ended


def stale_response() -> JSONResponse:
    """The 409 of an approval whose object changed. It is returned, not raised: the request's end
    (`stale`) has to be committed, and a raised problem would roll it back."""
    return problem_response(
        409, "change.stale", detail="the object changed after the change was requested"
    )


class Stale(Exception):
    """The object is not what the request saw."""


type Apply = Callable[[AsyncSession, ChangeApprovalRow, Session], Awaitable[None]]


async def apply_change(session: AsyncSession, row: ChangeApprovalRow, approver: Session) -> None:
    """Write the requested change as `approver`, with the object's own audit row.

    Raises `Stale` when the object is not at the version the request saw. The caller holds the
    request's row lock, so the object cannot change between this check and the write except
    through another request, which the one-pending-request rule orders.
    """
    appliers: dict[ChangeObjectType, Apply] = {
        ChangeObjectType.CATALOG_RULE: _apply_rule,
        ChangeObjectType.CATALOG_LOG_SOURCE: _apply_log_source,
        ChangeObjectType.CRITICAL_ASSET: _apply_asset,
        ChangeObjectType.PLATFORM_FLAG: _apply_flag,
    }
    applier = appliers.get(row.object_type)
    if applier is None:
        raise Problem(409, "change.unsupported", detail="no endpoint applies this kind of change")
    await applier(session, row, approver)


def _change(row: ChangeApprovalRow) -> dict[str, JsonValue]:
    return row.change if isinstance(row.change, dict) else {}


def _after(row: ChangeApprovalRow) -> dict[str, JsonValue]:
    after = _change(row).get("after")
    return after if isinstance(after, dict) else {}


def _requested(row: ChangeApprovalRow) -> dict[str, JsonValue]:
    """What an approval's audit rows say of the request: who asked and which request it was."""
    return {"requested_by": row.requested_by, "change_id": str(row.id)}


async def _apply_rule(session: AsyncSession, row: ChangeApprovalRow, approver: Session) -> None:
    rule_id = int(row.object_id)
    current = await get_catalog_rule(session, rule_id)
    if current is None or rule_version(current) != row.object_version:
        raise Stale
    action = _change(row).get("action")
    try:
        if action == ACTION_ACCEPT_DRAFT:
            updated = await accept_catalog_rule_draft(
                session, rule_id, updated_by=approver.subject, updated_at=now()
            )
            audit_action = audit.ACTION_CATALOG_RULE_ACCEPT_DRAFT
            details: dict[str, JsonValue] = {"context_note": updated.context_note}
        else:
            body = CatalogRuleUpdate.model_validate(_after(row))
            updated = await update_catalog_rule(
                session,
                rule_id,
                mode=body.mode,
                min_level=body.min_level,
                has_automated_action=body.has_automated_action,
                context_note=body.context_note,
                attack_techniques=list(body.attack_techniques),
                updated_by=approver.subject,
                updated_at=now(),
            )
            audit_action = audit.ACTION_CATALOG_RULE_UPDATE
            details = rule_values(updated)
    except (NotFoundError, ValidationError):
        raise Stale from None
    await audit.record(
        session,
        actor_id=approver.subject,
        action=audit_action,
        object_type=audit.OBJECT_CATALOG_RULE,
        object_id=row.object_id,
        details={**details, **_requested(row)},
    )


async def _apply_log_source(
    session: AsyncSession, row: ChangeApprovalRow, approver: Session
) -> None:
    log_source_id = int(row.object_id)
    current = await get_catalog_log_source(session, log_source_id)
    if current is None or log_source_version(current) != row.object_version:
        raise Stale
    body = CatalogLogSourceUpdate.model_validate(_after(row))
    try:
        updated = await update_catalog_log_source(
            session,
            log_source_id,
            description=body.description,
            owner=body.owner,
            criticality=body.criticality,
            in_scope=body.in_scope,
            context_note=body.context_note,
            telemetry_classes=body.telemetry_classes,
            updated_by=approver.subject,
            updated_at=now(),
        )
    except (NotFoundError, ValidationError):
        raise Stale from None
    await audit.record(
        session,
        actor_id=approver.subject,
        action=audit.ACTION_CATALOG_LOG_SOURCE_UPDATE,
        object_type=audit.OBJECT_CATALOG_LOG_SOURCE,
        object_id=row.object_id,
        details={**log_source_values(updated), **_requested(row)},
    )


async def _apply_asset(session: AsyncSession, row: ChangeApprovalRow, approver: Session) -> None:
    if _change(row).get("action") == ACTION_DELETE:
        asset_id = uuid.UUID(row.object_id)
        current = await get_critical_asset(session, asset_id)
        if current is None or asset_version(current) != row.object_version:
            raise Stale
        await delete_critical_asset(session, asset_id)
        await audit.record(
            session,
            actor_id=approver.subject,
            action=audit.ACTION_CRITICAL_ASSET_DELETE,
            object_type=audit.OBJECT_CRITICAL_ASSET,
            object_id=row.object_id,
            details={**asset_values(current), **_requested(row)},
        )
        return
    body = CriticalAssetAdd.model_validate(_after(row))
    # The asset was not in the list when it was requested; one added since makes the request stale.
    if await find_critical_asset(session, kind=body.kind, value=body.value) is not None:
        raise Stale
    try:
        created = await add_critical_asset(
            session, kind=body.kind, value=body.value, label=body.label, level=body.level
        )
    except (ValueError, ValidationError):
        raise Stale from None
    await audit.record(
        session,
        actor_id=approver.subject,
        action=audit.ACTION_CRITICAL_ASSET_ADD,
        object_type=audit.OBJECT_CRITICAL_ASSET,
        object_id=str(created.id),
        details={**asset_values(created), **_requested(row)},
    )


async def _apply_flag(session: AsyncSession, row: ChangeApprovalRow, approver: Session) -> None:
    flag = PlatformFlag(row.object_id)
    if flag_version(await get_platform_flag(session, flag)) != row.object_version:
        raise Stale
    reason = _change(row).get("reason")
    await set_platform_flag(
        session,
        flag,
        enabled=True,
        reason=reason if isinstance(reason, str) else "",
        actor_kind=ActorKind.USER,
        actor_id=approver.subject,
        extra_details=_requested(row),
    )
