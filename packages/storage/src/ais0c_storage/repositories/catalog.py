"""`catalog_rules` and `catalog_log_sources`: the Analysis Catalog (architecture §9, D-25).

Catalog notes reach prompts as trusted context, so only admins edit them and every change is
audited; the caller appends the audit entry in the same transaction (`append_audit`). The
fields that reach a prompt are checked against the `CatalogRule` and `CatalogLogSource`
contracts before they are stored.

The QRadar sync (`KnowledgeSync`) writes only what comes from QRadar: names, a log source's
type, a rule's enabled state (`qradar_enabled`), and `missing_since` on entries QRadar no
longer lists. An entry is never deleted (T-37).
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import batched

from pydantic import TypeAdapter
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from ais0c_contracts import CatalogLogSource, CatalogMode, CatalogRule, Level, Summary
from ais0c_storage.models import CatalogLogSourceRow, CatalogRuleRow
from ais0c_storage.repositories._common import fetch_all, get_row, update_one

# Rows per statement during a sync; keeps every statement well under PostgreSQL's limit of
# 65535 parameters.
_SYNC_BATCH = 1000

_SUMMARY: TypeAdapter[str] = TypeAdapter(Summary)


@dataclass(frozen=True)
class SyncedRule:
    """A rule as read from QRadar."""

    rule_id: int
    rule_name: str
    qradar_enabled: bool = True
    """QRadar's `enabled`; true, the column's default, when the reader does not know it."""


@dataclass(frozen=True)
class SyncedLogSource:
    """A log source as read from QRadar."""

    log_source_id: int
    name: str
    type_name: str


# --- Rules


async def get_catalog_rule(session: AsyncSession, rule_id: int) -> CatalogRuleRow | None:
    return await get_row(session, CatalogRuleRow, rule_id)


async def get_catalog_rules(
    session: AsyncSession, rule_ids: Collection[int]
) -> list[CatalogRuleRow]:
    """Catalog entries of `rule_ids`, by rule ID. A rule missing from the catalog is left
    out; it is analyzed without a floor (architecture §9)."""
    statement = (
        select(CatalogRuleRow)
        .where(CatalogRuleRow.rule_id.in_(list(rule_ids)))
        .order_by(CatalogRuleRow.rule_id)
    )
    return await fetch_all(session, statement)


async def list_catalog_rules(
    session: AsyncSession,
    *,
    defined: bool | None = None,
    mode: CatalogMode | None = None,
    search: str | None = None,
) -> list[CatalogRuleRow]:
    """Rules matching every given filter; `search` matches part of the rule name, ignoring
    case."""
    statement = select(CatalogRuleRow)
    if defined is not None:
        statement = statement.where(CatalogRuleRow.defined == defined)
    if mode is not None:
        statement = statement.where(CatalogRuleRow.mode == mode)
    if search:
        statement = statement.where(CatalogRuleRow.rule_name.icontains(search, autoescape=True))
    return await fetch_all(session, statement.order_by(CatalogRuleRow.rule_id))


async def sync_catalog_rules(
    session: AsyncSession, rules: Iterable[SyncedRule], *, synced_by: str, synced_at: datetime
) -> list[int]:
    """Bring the catalog in line with the rules read from QRadar (`KnowledgeSync`).

    A new rule is added undefined: mode `analyze`, no floor, no note, and QRadar's enabled
    state. A rule renamed, enabled or disabled in QRadar gets its new name and state. Operator
    fields are never changed and nothing is deleted. Returns the IDs of the new rules.
    """
    latest = {rule.rule_id: rule for rule in rules}
    new_ids: list[int] = []
    for batch in batched(latest.values(), _SYNC_BATCH):
        rows = [
            {
                "rule_id": rule.rule_id,
                "rule_name": rule.rule_name,
                "qradar_enabled": rule.qradar_enabled,
                "defined": False,
                "mode": CatalogMode.ANALYZE,
                "has_automated_action": False,
                "updated_by": synced_by,
                "updated_at": synced_at,
            }
            for rule in batch
        ]
        added = (
            insert(CatalogRuleRow)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[CatalogRuleRow.rule_id])
            .returning(CatalogRuleRow.rule_id)
        )
        new_ids.extend(await session.scalars(added))
        upsert = insert(CatalogRuleRow).values(rows)
        changed = upsert.on_conflict_do_update(
            index_elements=[CatalogRuleRow.rule_id],
            set_={
                "rule_name": upsert.excluded.rule_name,
                "qradar_enabled": upsert.excluded.qradar_enabled,
                "updated_by": upsert.excluded.updated_by,
                "updated_at": upsert.excluded.updated_at,
            },
            where=or_(
                CatalogRuleRow.rule_name.is_distinct_from(upsert.excluded.rule_name),
                CatalogRuleRow.qradar_enabled.is_distinct_from(upsert.excluded.qradar_enabled),
            ),
        )
        await session.execute(changed)
    return sorted(new_ids)


async def set_catalog_rules_missing(
    session: AsyncSession,
    rule_ids: Iterable[int],
    *,
    missing: bool,
    synced_by: str,
    synced_at: datetime,
) -> list[int]:
    """Mark rules QRadar no longer lists (`missing=True`), or clear the mark of rules it lists
    again (`missing=False`).

    A marked rule gets `missing_since = synced_at` unless it has one: the mark keeps the sync
    that first missed the rule. The rule, its operator fields and its audit history stay, and
    the enrichment goes on using it. Only rules whose mark changes get `updated_by` and
    `updated_at`. Returns their IDs, sorted.
    """
    return await _set_missing(
        session,
        CatalogRuleRow,
        CatalogRuleRow.rule_id,
        rule_ids,
        missing=missing,
        synced_by=synced_by,
        synced_at=synced_at,
    )


async def update_catalog_rule(
    session: AsyncSession,
    rule_id: int,
    *,
    mode: CatalogMode,
    min_level: Level | None,
    has_automated_action: bool,
    context_note: Summary | None,
    attack_techniques: Collection[str] = (),
    updated_by: str,
    updated_at: datetime,
) -> CatalogRuleRow:
    """An admin's edit of a rule (`PUT /catalog/rules/{rule_id}`); the rule becomes defined.

    Like the other operator fields, `attack_techniques` replaces what the rule had; they are
    stored sorted, each once.
    """
    techniques = sorted(set(attack_techniques))
    # Raises ValidationError if a field that reaches prompts or the skill router breaks the
    # contract.
    CatalogRule(
        rule_id=rule_id,
        mode=mode,
        min_level=min_level,
        context_note=context_note,
        attack_techniques=techniques,
    )
    statement = (
        update(CatalogRuleRow)
        .where(CatalogRuleRow.rule_id == rule_id)
        .values(
            defined=True,
            mode=mode,
            min_level=min_level,
            has_automated_action=has_automated_action,
            context_note=context_note,
            attack_techniques=techniques,
            updated_by=updated_by,
            updated_at=updated_at,
        )
    )
    return await update_one(session, statement, CatalogRuleRow, f"catalog rule {rule_id}")


async def set_catalog_rule_draft(
    session: AsyncSession, rule_id: int, ai_draft_note: Summary
) -> CatalogRuleRow:
    """Store the note the AI suggests. It is not used until an admin accepts it."""
    _SUMMARY.validate_python(ai_draft_note)
    statement = (
        update(CatalogRuleRow)
        .where(CatalogRuleRow.rule_id == rule_id)
        .values(ai_draft_note=ai_draft_note)
    )
    return await update_one(session, statement, CatalogRuleRow, f"catalog rule {rule_id}")


async def accept_catalog_rule_draft(
    session: AsyncSession, rule_id: int, *, updated_by: str, updated_at: datetime
) -> CatalogRuleRow:
    """The AI's draft becomes the rule's note (`POST /catalog/rules/{rule_id}/accept-draft`).

    Raises `NotFoundError` if the rule does not exist or has no draft.
    """
    statement = (
        update(CatalogRuleRow)
        .where(CatalogRuleRow.rule_id == rule_id, CatalogRuleRow.ai_draft_note.is_not(None))
        .values(
            context_note=CatalogRuleRow.ai_draft_note,
            ai_draft_note=None,
            updated_by=updated_by,
            updated_at=updated_at,
        )
    )
    return await update_one(session, statement, CatalogRuleRow, f"draft of catalog rule {rule_id}")


def to_catalog_rule(row: CatalogRuleRow) -> CatalogRule:
    """The part of a rule that goes into `CatalogContext`.

    `qradar_enabled` and `missing_since` stay out: the contract has no place for them, and the
    analysis does not look at them (T-37).
    """
    return CatalogRule(
        rule_id=row.rule_id,
        mode=row.mode,
        min_level=row.min_level,
        context_note=row.context_note,
        attack_techniques=list(row.attack_techniques) or None,
    )


# --- Log sources


async def get_catalog_log_source(
    session: AsyncSession, log_source_id: int
) -> CatalogLogSourceRow | None:
    return await get_row(session, CatalogLogSourceRow, log_source_id)


async def get_catalog_log_sources(
    session: AsyncSession, log_source_ids: Collection[int]
) -> list[CatalogLogSourceRow]:
    """Catalog entries of `log_source_ids`, by log source ID; unknown IDs are left out."""
    statement = (
        select(CatalogLogSourceRow)
        .where(CatalogLogSourceRow.log_source_id.in_(list(log_source_ids)))
        .order_by(CatalogLogSourceRow.log_source_id)
    )
    return await fetch_all(session, statement)


async def list_catalog_log_sources(
    session: AsyncSession,
    *,
    defined: bool | None = None,
    in_scope: bool | None = None,
    search: str | None = None,
) -> list[CatalogLogSourceRow]:
    """Log sources matching every given filter; `search` matches part of the name or the type
    name, ignoring case."""
    statement = select(CatalogLogSourceRow)
    if defined is not None:
        statement = statement.where(CatalogLogSourceRow.defined == defined)
    if in_scope is not None:
        statement = statement.where(CatalogLogSourceRow.in_scope == in_scope)
    if search:
        statement = statement.where(
            or_(
                CatalogLogSourceRow.name.icontains(search, autoescape=True),
                CatalogLogSourceRow.type_name.icontains(search, autoescape=True),
            )
        )
    return await fetch_all(session, statement.order_by(CatalogLogSourceRow.log_source_id))


async def sync_catalog_log_sources(
    session: AsyncSession,
    log_sources: Iterable[SyncedLogSource],
    *,
    synced_by: str,
    synced_at: datetime,
) -> list[int]:
    """Bring the catalog in line with the log sources read from QRadar (`KnowledgeSync`).

    A new log source is added undefined and in scope, so it is analyzed until an operator
    says otherwise, like an undefined rule. Changed names and type names are updated.
    Operator fields are never changed and nothing is deleted. Returns the IDs of the new log
    sources.
    """
    latest = {source.log_source_id: source for source in log_sources}
    new_ids: list[int] = []
    for batch in batched(latest.values(), _SYNC_BATCH):
        rows = [
            {
                "log_source_id": source.log_source_id,
                "name": source.name,
                "type_name": source.type_name,
                "defined": False,
                "in_scope": True,
                "updated_by": synced_by,
                "updated_at": synced_at,
            }
            for source in batch
        ]
        added = (
            insert(CatalogLogSourceRow)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[CatalogLogSourceRow.log_source_id])
            .returning(CatalogLogSourceRow.log_source_id)
        )
        new_ids.extend(await session.scalars(added))
        upsert = insert(CatalogLogSourceRow).values(rows)
        changed = upsert.on_conflict_do_update(
            index_elements=[CatalogLogSourceRow.log_source_id],
            set_={
                "name": upsert.excluded.name,
                "type_name": upsert.excluded.type_name,
                "updated_by": upsert.excluded.updated_by,
                "updated_at": upsert.excluded.updated_at,
            },
            where=or_(
                CatalogLogSourceRow.name.is_distinct_from(upsert.excluded.name),
                CatalogLogSourceRow.type_name.is_distinct_from(upsert.excluded.type_name),
            ),
        )
        await session.execute(changed)
    return sorted(new_ids)


async def set_catalog_log_sources_missing(
    session: AsyncSession,
    log_source_ids: Iterable[int],
    *,
    missing: bool,
    synced_by: str,
    synced_at: datetime,
) -> list[int]:
    """`set_catalog_rules_missing` for log sources."""
    return await _set_missing(
        session,
        CatalogLogSourceRow,
        CatalogLogSourceRow.log_source_id,
        log_source_ids,
        missing=missing,
        synced_by=synced_by,
        synced_at=synced_at,
    )


async def update_catalog_log_source(
    session: AsyncSession,
    log_source_id: int,
    *,
    description: str | None,
    owner: str | None,
    criticality: Level | None,
    in_scope: bool,
    context_note: Summary | None,
    updated_by: str,
    updated_at: datetime,
) -> CatalogLogSourceRow:
    """An admin's edit of a log source (`PUT /catalog/log-sources/{log_source_id}`); the log
    source becomes defined."""
    # Raises ValidationError if a field that reaches prompts breaks the contract.
    CatalogLogSource(
        log_source_id=log_source_id,
        description=description,
        criticality=criticality,
        context_note=context_note,
    )
    statement = (
        update(CatalogLogSourceRow)
        .where(CatalogLogSourceRow.log_source_id == log_source_id)
        .values(
            defined=True,
            description=description,
            owner=owner,
            criticality=criticality,
            in_scope=in_scope,
            context_note=context_note,
            updated_by=updated_by,
            updated_at=updated_at,
        )
    )
    what = f"catalog log source {log_source_id}"
    return await update_one(session, statement, CatalogLogSourceRow, what)


def to_catalog_log_source(row: CatalogLogSourceRow) -> CatalogLogSource:
    """The part of a log source that goes into `CatalogContext`; `missing_since` stays out, as
    in `to_catalog_rule`."""
    return CatalogLogSource(
        log_source_id=row.log_source_id,
        type_name=row.type_name,
        description=row.description,
        criticality=row.criticality,
        context_note=row.context_note,
    )


async def _set_missing(
    session: AsyncSession,
    entity: type[CatalogRuleRow] | type[CatalogLogSourceRow],
    key: InstrumentedAttribute[int],
    ids: Iterable[int],
    *,
    missing: bool,
    synced_by: str,
    synced_at: datetime,
) -> list[int]:
    marked = entity.missing_since.is_(None) if missing else entity.missing_since.is_not(None)
    changed: list[int] = []
    for batch in batched(sorted(set(ids)), _SYNC_BATCH):
        statement = (
            update(entity)
            .where(key.in_(batch), marked)
            .values(
                missing_since=synced_at if missing else None,
                updated_by=synced_by,
                updated_at=synced_at,
            )
            .returning(key)
        )
        changed.extend(await session.scalars(statement))
    return sorted(changed)
