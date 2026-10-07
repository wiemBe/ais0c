"""The Analysis Catalog: the rules and log sources, their definitions and the sync trigger
(api.md "Analiz Kataloğu"; T-028 criterion 7, D-25, T-37).

Reading is an operator's job, editing an admin's. Notes reach prompts as trusted context, so every
edit is audited in the same transaction. Double control (D-36) comes with T-033: here a change is
written directly.

`POST /catalog/sync` asks Temporal to start the KnowledgeSync Schedule now. The API itself never
talks to QRadar; the batch worker does that (architecture §25, api.md).
"""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import ValidationError

from ais0c_api import audit
from ais0c_api.dependencies import ADMIN, OPERATOR, ReadSession, Trigger, WriteSession, now
from ais0c_api.models import (
    CatalogLogSource,
    CatalogLogSourceUpdate,
    CatalogRule,
    CatalogRuleUpdate,
    Page,
    SyncAccepted,
)
from ais0c_api.pagination import (
    DEFAULT_LIMIT,
    CursorParam,
    LimitParam,
    paginate,
    string_cursor,
)
from ais0c_api.problems import Problem, invalid_cursor, not_found
from ais0c_api.temporal import KNOWLEDGE_SYNC_SCHEDULE_ID, ScheduleNotFound, TemporalUnavailable
from ais0c_contracts import CatalogMode
from ais0c_storage.errors import NotFoundError
from ais0c_storage.models import CatalogLogSourceRow, CatalogRuleRow
from ais0c_storage.repositories import (
    accept_catalog_rule_draft,
    get_catalog_log_source,
    get_catalog_rule,
    list_catalog_log_sources,
    list_catalog_rules,
    update_catalog_log_source,
    update_catalog_rule,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])

BoolParam = Annotated[bool | None, Query()]
ModeParam = Annotated[CatalogMode | None, Query()]
TextParam = Annotated[str | None, Query(max_length=200)]


def rule(row: CatalogRuleRow) -> CatalogRule:
    return CatalogRule(
        rule_id=row.rule_id,
        rule_name=row.rule_name,
        defined=row.defined,
        mode=row.mode,
        min_level=row.min_level,
        has_automated_action=row.has_automated_action,
        context_note=row.context_note,
        ai_draft_note=row.ai_draft_note,
        attack_techniques=list(row.attack_techniques),
        qradar_enabled=row.qradar_enabled,
        missing_since=row.missing_since,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def log_source(row: CatalogLogSourceRow) -> CatalogLogSource:
    return CatalogLogSource(
        log_source_id=row.log_source_id,
        name=row.name,
        type_name=row.type_name,
        defined=row.defined,
        description=row.description,
        owner=row.owner,
        criticality=row.criticality,
        in_scope=row.in_scope,
        context_note=row.context_note,
        missing_since=row.missing_since,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def id_cursor(cursor: str | None) -> int | None:
    """The `?cursor=` value as a QRadar rule or log source ID; anything unreadable is a 400.

    The ID is written into the cursor as its own text, so `string_cursor` reads it back.
    """
    value = string_cursor(cursor)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise invalid_cursor() from None


@router.get("/rules", response_model=Page[CatalogRule])
async def get_rules(
    _user: OPERATOR,
    session: ReadSession,
    defined: BoolParam = None,
    mode: ModeParam = None,
    qradar_enabled: BoolParam = None,
    missing: BoolParam = None,
    q: TextParam = None,
    cursor: CursorParam = None,
    limit: LimitParam = DEFAULT_LIMIT,
) -> Page[CatalogRule]:
    """The rules by ID. `q` matches part of the name; `missing` keeps what QRadar stopped listing
    (`missing_since` set) or only what it lists (`missing=false`) (T-37)."""
    rows = await list_catalog_rules(
        session,
        defined=defined,
        mode=mode,
        qradar_enabled=qradar_enabled,
        missing=missing,
        search=q,
        after_rule_id=id_cursor(cursor),
        limit=limit + 1,
    )
    page, next_cursor = paginate(rows, limit, lambda row: [str(row.rule_id)])
    return Page(items=[rule(row) for row in page], next_cursor=next_cursor)


@router.get("/rules/{rule_id}", response_model=CatalogRule)
async def get_rule(rule_id: int, _user: OPERATOR, session: ReadSession) -> CatalogRule:
    """One catalog rule; an unknown `rule_id` is a 404."""
    row = await get_catalog_rule(session, rule_id)
    if row is None:
        raise not_found("catalog.rule_not_found")
    return rule(row)


@router.put("/rules/{rule_id}", response_model=CatalogRule)
async def put_rule(
    rule_id: int, body: CatalogRuleUpdate, user: ADMIN, session: WriteSession
) -> CatalogRule:
    """An admin's edit of a rule. The rule becomes `defined` and the change is audited.

    An unknown `rule_id` is a 404 and nothing is written.
    """
    try:
        row = await update_catalog_rule(
            session,
            rule_id,
            mode=body.mode,
            min_level=body.min_level,
            has_automated_action=body.has_automated_action,
            context_note=body.context_note,
            attack_techniques=list(body.attack_techniques),
            updated_by=user.subject,
            updated_at=now(),
        )
    except NotFoundError:
        raise not_found("catalog.rule_not_found") from None
    except ValidationError as error:
        raise Problem(
            422, "catalog.rule_invalid", detail="the rule does not match the catalog contract"
        ) from error
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CATALOG_RULE_UPDATE,
        object_type=audit.OBJECT_CATALOG_RULE,
        object_id=str(rule_id),
        details={
            "mode": row.mode.value,
            "min_level": None if row.min_level is None else row.min_level.value,
            "has_automated_action": row.has_automated_action,
            "context_note": row.context_note,
            "attack_techniques": list(row.attack_techniques),
        },
    )
    return rule(row)


@router.post("/rules/{rule_id}/accept-draft", response_model=CatalogRule)
async def post_accept_draft(rule_id: int, user: ADMIN, session: WriteSession) -> CatalogRule:
    """Accept the note the AI suggested: it becomes the rule's `context_note` and the draft is
    cleared. A rule with no draft, or an unknown `rule_id`, is a 404."""
    try:
        row = await accept_catalog_rule_draft(
            session, rule_id, updated_by=user.subject, updated_at=now()
        )
    except NotFoundError:
        raise not_found("catalog.rule_draft_not_found") from None
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CATALOG_RULE_ACCEPT_DRAFT,
        object_type=audit.OBJECT_CATALOG_RULE,
        object_id=str(rule_id),
        details={"context_note": row.context_note},
    )
    return rule(row)


@router.get("/log-sources", response_model=Page[CatalogLogSource])
async def get_log_sources(
    _user: OPERATOR,
    session: ReadSession,
    defined: BoolParam = None,
    in_scope: BoolParam = None,
    missing: BoolParam = None,
    q: TextParam = None,
    cursor: CursorParam = None,
    limit: LimitParam = DEFAULT_LIMIT,
) -> Page[CatalogLogSource]:
    """The log sources by ID. `q` matches part of the name or the type name."""
    rows = await list_catalog_log_sources(
        session,
        defined=defined,
        in_scope=in_scope,
        missing=missing,
        search=q,
        after_log_source_id=id_cursor(cursor),
        limit=limit + 1,
    )
    page, next_cursor = paginate(rows, limit, lambda row: [str(row.log_source_id)])
    return Page(items=[log_source(row) for row in page], next_cursor=next_cursor)


@router.get("/log-sources/{log_source_id}", response_model=CatalogLogSource)
async def get_log_source(
    log_source_id: int, _user: OPERATOR, session: ReadSession
) -> CatalogLogSource:
    """One catalog log source; an unknown ID is a 404."""
    row = await get_catalog_log_source(session, log_source_id)
    if row is None:
        raise not_found("catalog.log_source_not_found")
    return log_source(row)


@router.put("/log-sources/{log_source_id}", response_model=CatalogLogSource)
async def put_log_source(
    log_source_id: int, body: CatalogLogSourceUpdate, user: ADMIN, session: WriteSession
) -> CatalogLogSource:
    """An admin's edit of a log source. It becomes `defined` and the change is audited."""
    try:
        row = await update_catalog_log_source(
            session,
            log_source_id,
            description=body.description,
            owner=body.owner,
            criticality=body.criticality,
            in_scope=body.in_scope,
            context_note=body.context_note,
            updated_by=user.subject,
            updated_at=now(),
        )
    except NotFoundError:
        raise not_found("catalog.log_source_not_found") from None
    except ValidationError as error:
        raise Problem(
            422,
            "catalog.log_source_invalid",
            detail="the log source does not match the catalog contract",
        ) from error
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CATALOG_LOG_SOURCE_UPDATE,
        object_type=audit.OBJECT_CATALOG_LOG_SOURCE,
        object_id=str(log_source_id),
        details={
            "description": row.description,
            "owner": row.owner,
            "criticality": None if row.criticality is None else row.criticality.value,
            "in_scope": row.in_scope,
            "context_note": row.context_note,
        },
    )
    return log_source(row)


@router.post("/sync", response_model=SyncAccepted, status_code=202)
async def post_sync(user: ADMIN, trigger: Trigger, session: WriteSession) -> SyncAccepted:
    """Start the KnowledgeSync Schedule now; Temporal runs it, and 202 says it was accepted.

    The API itself reads nothing from QRadar. The audit row is written first and committed only
    when Temporal took the trigger, so a sync never starts without one. Temporal being
    unreachable is a 503 (`temporal.unavailable`); a Schedule the batch worker has not created
    yet is a 409 (`catalog.sync_not_scheduled`). Neither leaves an audit row.
    """
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CATALOG_SYNC,
        object_type=audit.OBJECT_SCHEDULE,
        object_id=KNOWLEDGE_SYNC_SCHEDULE_ID,
        details={"triggered_by": user.subject},
    )
    try:
        await trigger.trigger(KNOWLEDGE_SYNC_SCHEDULE_ID)
    except ScheduleNotFound:
        raise Problem(
            409,
            "catalog.sync_not_scheduled",
            detail="the batch worker has not created the sync Schedule",
        ) from None
    except TemporalUnavailable:
        raise Problem(503, "temporal.unavailable") from None
    return SyncAccepted(schedule_id=KNOWLEDGE_SYNC_SCHEDULE_ID)
