"""The audit rows the API's write endpoints leave (criterion 3, api.md).

Every changing request appends one `audit_log` row **in the same transaction as the change**, with
`actor_kind=user`, `actor_id` the session's subject, the endpoint's `action`, the object's type
and ID and a short summary of what changed in `details`. A request the API rejects (4xx) writes
nothing, because it either never opens the transaction or rolls it back.

The action names are the ones the data model names (`audit_log.action`), one per endpoint.
"""

from typing import Final

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_storage.enums import ActorKind
from ais0c_storage.repositories import append_audit

# `action` and `object_type` of each write endpoint. `object_id` is filled by the caller: a case
# ID, a QA item ID, a catalog ID, a flag name.
ACTION_CASE_FEEDBACK: Final = "case.feedback"
OBJECT_CASE: Final = "case"
ACTION_QA_RESOLVE: Final = "qa.resolve"
OBJECT_QA_ITEM: Final = "qa_item"
ACTION_CATALOG_RULE_UPDATE: Final = "catalog.rule.update"
ACTION_CATALOG_RULE_ACCEPT_DRAFT: Final = "catalog.rule.accept_draft"
ACTION_CATALOG_LOG_SOURCE_UPDATE: Final = "catalog.log_source.update"
OBJECT_CATALOG_RULE: Final = "catalog_rule"
OBJECT_CATALOG_LOG_SOURCE: Final = "catalog_log_source"
ACTION_CATALOG_SYNC: Final = "catalog.sync"
OBJECT_SCHEDULE: Final = "schedule"
ACTION_CRITICAL_ASSET_ADD: Final = "critical_asset.add"
ACTION_CRITICAL_ASSET_DELETE: Final = "critical_asset.delete"
OBJECT_CRITICAL_ASSET: Final = "critical_asset"
ACTION_RECIPIENTS_REPLACE: Final = "notification_recipients.replace"
OBJECT_RECIPIENT_GROUP: Final = "notification_recipient_group"
ACTION_ROUTES_REPLACE: Final = "notification_routes.replace"
OBJECT_ROUTES_TABLE: Final = "notification_routes"
# Double control (D-36): the audit rows of a request and its end. The object's own action
# (`catalog.rule.update`, ...) is written by the approval, with the approver as actor.
ACTION_CHANGE_REQUEST: Final = "change.request"
ACTION_CHANGE_APPROVE: Final = "change.approve"
ACTION_CHANGE_REJECT: Final = "change.reject"
ACTION_CHANGE_WITHDRAW: Final = "change.withdraw"
ACTION_CHANGE_STALE: Final = "change.stale"
OBJECT_CHANGE: Final = "change_approval"
# A platform flag's own row is written by `set_platform_flag`, which appends its audit entry in
# the same transaction (T-017); this module does not write a second one. Switching the kill
# switch on is a request first (T-033); its approval writes that row.


async def record(
    session: AsyncSession,
    *,
    actor_id: str,
    action: str,
    object_type: str,
    object_id: str,
    details: dict[str, JsonValue] | None = None,
) -> None:
    """Append one `audit_log` row for `actor_id`'s change, in the caller's transaction."""
    await append_audit(
        session,
        actor_kind=ActorKind.USER,
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        details=details or {},
    )
