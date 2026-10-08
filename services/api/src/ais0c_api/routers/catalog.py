"""The Analysis Catalog: the rules and log sources, their definitions and the sync trigger
(api.md "Analiz Kataloğu"; T-028 criterion 7, D-25, T-37).

Reading is an operator's job, editing an admin's. Notes reach prompts as trusted context, so every
edit goes through double control (D-36, T-033): the endpoint writes a pending request (202) and a
second admin's approval applies and audits it (`ais0c_api.changes`). A request that cannot be
valid is refused here, so only a change that could be applied waits.

`POST /catalog/sync` asks Temporal to start the KnowledgeSync Schedule now. The API itself never
talks to QRadar; the batch worker does that (architecture §25, api.md).
"""

from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import ValidationError

from ais0c_api import audit
from ais0c_api.changes import (
    ACTION_ACCEPT_DRAFT,
    ACTION_UPDATE,
    log_source_values,
    log_source_version,
    queue_change,
    rule_values,
    rule_version,
)
from ais0c_api.dependencies import ADMIN, OPERATOR, ReadSession, Trigger, WriteSession
from ais0c_api.models import (
    CatalogLogSource,
    CatalogLogSourceUpdate,
    CatalogRule,
    CatalogRuleUpdate,
    ChangeAccepted,
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
from ais0c_contracts import CatalogLogSource as CatalogLogSourceContract
from ais0c_contracts import CatalogMode
from ais0c_contracts import CatalogRule as CatalogRuleContract
from ais0c_storage.enums import ChangeObjectType, TelemetryClass
from ais0c_storage.models import CatalogLogSourceRow, CatalogRuleRow
from ais0c_storage.repositories import (
    effective_telemetry_classes,
    get_catalog_log_source,
    get_catalog_rule,
    list_catalog_log_sources,
    list_catalog_rules,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])

BoolParam = Annotated[bool | None, Query()]
ModeParam = Annotated[CatalogMode | None, Query()]
TextParam = Annotated[str | None, Query(max_length=200)]
TelemetryClassParam = Annotated[TelemetryClass | None, Query()]


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
        qradar_enabled=row.qradar_enabled,
        missing_since=row.missing_since,
        default_telemetry_classes=sorted_classes(row.default_telemetry_classes),
        telemetry_classes=(
            None if row.telemetry_classes is None else sorted_classes(row.telemetry_classes)
        ),
        effective_telemetry_classes=sorted_classes(effective_telemetry_classes(row)),
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def sorted_classes(values: Iterable[str]) -> list[TelemetryClass]:
    return sorted((TelemetryClass(value) for value in values), key=lambda item: item.value)


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


@router.put("/rules/{rule_id}", response_model=ChangeAccepted, status_code=202)
async def put_rule(
    rule_id: int, body: CatalogRuleUpdate, user: ADMIN, session: WriteSession
) -> ChangeAccepted:
    """An admin's edit of a rule, waiting for a second admin (202).

    An unknown `rule_id` is a 404, a body that breaks the catalog contract a 422, and a rule that
    already has a pending request a 409. The rule changes when the request is approved.
    """
    row = await get_catalog_rule(session, rule_id)
    if row is None:
        raise not_found("catalog.rule_not_found")
    techniques = sorted(set(body.attack_techniques))
    try:
        CatalogRuleContract(
            rule_id=rule_id,
            mode=body.mode,
            min_level=body.min_level,
            context_note=body.context_note,
            attack_techniques=techniques,
        )
    except ValidationError as error:
        raise Problem(
            422, "catalog.rule_invalid", detail="the rule does not match the catalog contract"
        ) from error
    after = body.model_dump(mode="json")
    after["attack_techniques"] = list(techniques)
    return await queue_change(
        session,
        user,
        object_type=ChangeObjectType.CATALOG_RULE,
        object_id=str(rule_id),
        object_version=rule_version(row),
        action=ACTION_UPDATE,
        before=rule_values(row),
        after=after,
    )


@router.post("/rules/{rule_id}/accept-draft", response_model=ChangeAccepted, status_code=202)
async def post_accept_draft(rule_id: int, user: ADMIN, session: WriteSession) -> ChangeAccepted:
    """Accepting the note the AI suggested, waiting for a second admin (202): on approval it
    becomes the rule's `context_note` and the draft is cleared. A rule with no draft, or an
    unknown `rule_id`, is a 404."""
    row = await get_catalog_rule(session, rule_id)
    if row is None or row.ai_draft_note is None:
        raise not_found("catalog.rule_draft_not_found")
    return await queue_change(
        session,
        user,
        object_type=ChangeObjectType.CATALOG_RULE,
        object_id=str(rule_id),
        object_version=rule_version(row),
        action=ACTION_ACCEPT_DRAFT,
        before={"context_note": row.context_note, "ai_draft_note": row.ai_draft_note},
        after={"context_note": row.ai_draft_note, "ai_draft_note": None},
    )


@router.get("/log-sources", response_model=Page[CatalogLogSource])
async def get_log_sources(
    _user: OPERATOR,
    session: ReadSession,
    defined: BoolParam = None,
    in_scope: BoolParam = None,
    missing: BoolParam = None,
    qradar_enabled: BoolParam = None,
    telemetry_class: TelemetryClassParam = None,
    unclassified: BoolParam = None,
    q: TextParam = None,
    cursor: CursorParam = None,
    limit: LimitParam = DEFAULT_LIMIT,
) -> Page[CatalogLogSource]:
    """The log sources by ID. `q` matches part of the name or the type name. `telemetry_class`
    keeps the log sources with that effective class and `unclassified=true` those QRadar counts
    that have none (T-95)."""
    rows = await list_catalog_log_sources(
        session,
        defined=defined,
        in_scope=in_scope,
        missing=missing,
        qradar_enabled=qradar_enabled,
        telemetry_class=telemetry_class,
        unclassified=unclassified,
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


@router.put("/log-sources/{log_source_id}", response_model=ChangeAccepted, status_code=202)
async def put_log_source(
    log_source_id: int, body: CatalogLogSourceUpdate, user: ADMIN, session: WriteSession
) -> ChangeAccepted:
    """An admin's edit of a log source, waiting for a second admin (202). The log source becomes
    `defined` when the request is approved."""
    row = await get_catalog_log_source(session, log_source_id)
    if row is None:
        raise not_found("catalog.log_source_not_found")
    try:
        CatalogLogSourceContract(
            log_source_id=log_source_id,
            description=body.description,
            criticality=body.criticality,
            context_note=body.context_note,
        )
    except ValidationError as error:
        raise Problem(
            422,
            "catalog.log_source_invalid",
            detail="the log source does not match the catalog contract",
        ) from error
    after = body.model_dump(mode="json")
    if "telemetry_classes" not in body.model_fields_set:
        # An edit that leaves the field out keeps the assigned classes.
        after["telemetry_classes"] = log_source_values(row)["telemetry_classes"]
    elif body.telemetry_classes is not None:
        if len(set(body.telemetry_classes)) != len(body.telemetry_classes):
            raise Problem(
                422,
                "catalog.log_source_invalid",
                detail="a telemetry class is given more than once",
            )
        after["telemetry_classes"] = sorted(item.value for item in body.telemetry_classes)
    return await queue_change(
        session,
        user,
        object_type=ChangeObjectType.CATALOG_LOG_SOURCE,
        object_id=str(log_source_id),
        object_version=log_source_version(row),
        action=ACTION_UPDATE,
        before=log_source_values(row),
        after=after,
    )


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
