"""Bringing the Analysis Catalog in line with QRadar (architecture §9, D-25; KnowledgeSync).

`sync_catalog` applies a complete read of QRadar (`read_inventory`) to `catalog_rules` and
`catalog_log_sources`:

- A new rule is added undefined (`defined=false`) in mode `analyze`, with no floor and no
  note; a new log source is added undefined and in scope. The UI lists undefined entries for
  an operator to fill in (T-029); until then they are analyzed (§9).
- An existing entry gets only what comes from QRadar: a rule its name and whether it is
  enabled (`qradar_enabled`), a log source its name and type name. The fields operators fill
  in never change: a rule's mode, floor, context note, automated action and ATT&CK techniques
  (T-26); a log source's description, owner, criticality, scope and context note; whether an
  entry is defined; the AI's draft note. A rule disabled in QRadar is still analyzed the way
  its catalog entry says (T-37).
- Nothing is deleted. An entry QRadar no longer lists is marked: its `missing_since` becomes
  the sync's time, unless it has one, so the mark keeps the sync that first missed it. An
  entry QRadar lists again loses the mark; a log source listed with a type QRadar's type list
  lacks counts as listed. The entry, its operator fields and its audit history stay, and the
  enrichment goes on using it (T-37).
- A sync that finds nothing to change writes nothing: no row, no `updated_at`, no audit
  entry. Running it twice in a row changes nothing the second time.

Only a complete read gets here: `read_inventory` raises when a list cannot be read in full,
and then no entry is added, changed or marked.

The rows are written by the storage repository (`sync_catalog_rules`,
`sync_catalog_log_sources`, `set_catalog_rules_missing`, `set_catalog_log_sources_missing`),
whose updates touch only the QRadar fields and the mark. Each change is audited as the system
actor `knowledge-sync`, under `catalog.rule.sync` or `catalog.log_source.sync`, with the change
in `details`: `added`, `changed` (with the previous values), `missing` or `returned`. Syncs take
turns on a transaction-scoped advisory lock: two runs, such as a retry that starts while a slow
attempt still runs, never interleave, and each one's report is exact.
"""

import hashlib
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_knowledge.catalog.inventory import QRadarInventory
from ais0c_storage import ActorKind
from ais0c_storage.repositories import (
    SyncedLogSource,
    SyncedRule,
    append_audit,
    list_catalog_log_sources,
    list_catalog_rules,
    set_catalog_log_sources_missing,
    set_catalog_rules_missing,
    sync_catalog_log_sources,
    sync_catalog_rules,
)

SYNC_ACTOR: Final = "knowledge-sync"
RULE_SYNC_ACTION: Final = "catalog.rule.sync"
LOG_SOURCE_SYNC_ACTION: Final = "catalog.log_source.sync"
RULE_OBJECT: Final = "catalog_rule"
LOG_SOURCE_OBJECT: Final = "catalog_log_source"
_LOCK_KEY: Final = int.from_bytes(
    hashlib.sha256(b"ais0c.catalog-sync").digest()[:8], "big", signed=True
)


@dataclass(frozen=True)
class CatalogSyncReport:
    """What one sync found and changed; IDs are sorted."""

    rules: int
    """Rules QRadar listed."""
    log_sources: int
    """Log sources QRadar listed, typed or not."""
    rules_added: tuple[int, ...] = ()
    rules_changed: tuple[int, ...] = ()
    """Renamed, enabled or disabled in QRadar."""
    rules_missing: tuple[int, ...] = ()
    """In the catalog but no longer listed by QRadar; kept, and marked (`missing_since`)."""
    rules_marked_missing: tuple[int, ...] = ()
    """The part of `rules_missing` this sync marked: QRadar listed them until now."""
    rules_returned: tuple[int, ...] = ()
    """Marked missing before and listed by QRadar again; the mark is gone."""
    log_sources_added: tuple[int, ...] = ()
    log_sources_changed: tuple[int, ...] = ()
    """Renamed, or given another type, in QRadar."""
    log_sources_missing: tuple[int, ...] = ()
    """In the catalog but no longer listed by QRadar; kept, and marked (`missing_since`)."""
    log_sources_marked_missing: tuple[int, ...] = ()
    """The part of `log_sources_missing` this sync marked: QRadar listed them until now."""
    log_sources_returned: tuple[int, ...] = ()
    """Marked missing before and listed by QRadar again; the mark is gone."""
    log_sources_untyped: tuple[int, ...] = ()
    """Listed with a type that QRadar's type list does not have; neither added nor changed."""

    @property
    def changed(self) -> bool:
        """Whether the sync changed the catalog."""
        return bool(
            self.rules_added
            or self.rules_changed
            or self.rules_marked_missing
            or self.rules_returned
            or self.log_sources_added
            or self.log_sources_changed
            or self.log_sources_marked_missing
            or self.log_sources_returned
        )

    def counts(self) -> dict[str, int]:
        """The report in numbers, e.g. for a workflow's result."""
        return {
            "rules": self.rules,
            "rules_added": len(self.rules_added),
            "rules_changed": len(self.rules_changed),
            "rules_missing": len(self.rules_missing),
            "rules_marked_missing": len(self.rules_marked_missing),
            "rules_returned": len(self.rules_returned),
            "log_sources": self.log_sources,
            "log_sources_added": len(self.log_sources_added),
            "log_sources_changed": len(self.log_sources_changed),
            "log_sources_missing": len(self.log_sources_missing),
            "log_sources_marked_missing": len(self.log_sources_marked_missing),
            "log_sources_returned": len(self.log_sources_returned),
            "log_sources_untyped": len(self.log_sources_untyped),
        }


@dataclass(frozen=True)
class _Known:
    """An entry as the catalog holds it before the sync."""

    qradar: tuple[str | bool, ...]
    """Its fields that come from QRadar."""
    missing_since: datetime | None


@dataclass(frozen=True)
class _Marks:
    """The known entries QRadar does not list, and the marks the sync changes."""

    missing: tuple[int, ...]
    """Not listed."""
    marked: tuple[int, ...]
    """Not listed, and not marked yet."""
    returned: tuple[int, ...]
    """Listed, and marked."""


async def sync_catalog(
    session: AsyncSession,
    inventory: QRadarInventory,
    *,
    synced_at: datetime,
    actor: str = SYNC_ACTOR,
) -> CatalogSyncReport:
    """Apply `inventory`, a complete read of QRadar, to the catalog in the caller's
    transaction. `synced_at` becomes the `updated_at` of the entries it adds, changes or marks,
    and the `missing_since` of the entries it marks."""
    await session.execute(select(func.pg_advisory_xact_lock(_LOCK_KEY)))
    # Plain values, taken before anything is written.
    known_rules = {
        row.rule_id: _Known((row.rule_name, row.qradar_enabled), row.missing_since)
        for row in await list_catalog_rules(session)
    }
    known_sources = {
        row.log_source_id: _Known((row.name, row.type_name), row.missing_since)
        for row in await list_catalog_log_sources(session)
    }
    rules = {rule.rule_id: rule for rule in inventory.rules}
    sources = {source.log_source_id: source for source in inventory.log_sources}
    listed_sources = inventory.log_source_ids()

    new_rules = [rule for rule_id, rule in rules.items() if rule_id not in known_rules]
    changed_rules = [
        rule
        for rule_id, rule in rules.items()
        if rule_id in known_rules and known_rules[rule_id].qradar != _rule_fields(rule)
    ]
    new_sources = [
        source for source_id, source in sources.items() if source_id not in known_sources
    ]
    changed_sources = [
        source
        for source_id, source in sources.items()
        if source_id in known_sources and known_sources[source_id].qradar != _source_fields(source)
    ]
    rule_marks = _marks(known_rules, rules.keys())
    source_marks = _marks(known_sources, listed_sources)

    if new_rules or changed_rules:
        await sync_catalog_rules(
            session, [*new_rules, *changed_rules], synced_by=actor, synced_at=synced_at
        )
    if new_sources or changed_sources:
        await sync_catalog_log_sources(
            session, [*new_sources, *changed_sources], synced_by=actor, synced_at=synced_at
        )
    for ids, missing in ((rule_marks.marked, True), (rule_marks.returned, False)):
        if ids:
            await set_catalog_rules_missing(
                session, ids, missing=missing, synced_by=actor, synced_at=synced_at
            )
    for ids, missing in ((source_marks.marked, True), (source_marks.returned, False)):
        if ids:
            await set_catalog_log_sources_missing(
                session, ids, missing=missing, synced_by=actor, synced_at=synced_at
            )

    audit = _Audit(session, actor)
    for rule in new_rules:
        await audit.rule(
            rule.rule_id,
            {"change": "added", "rule_name": rule.rule_name, "qradar_enabled": rule.qradar_enabled},
        )
    for rule in changed_rules:
        previous_name, previous_enabled = known_rules[rule.rule_id].qradar
        await audit.rule(
            rule.rule_id,
            {
                "change": "changed",
                "rule_name": rule.rule_name,
                "qradar_enabled": rule.qradar_enabled,
                "previous_rule_name": previous_name,
                "previous_qradar_enabled": previous_enabled,
            },
        )
    await audit.marks(RULE_SYNC_ACTION, RULE_OBJECT, rule_marks, known_rules, synced_at)
    for source in new_sources:
        await audit.log_source(
            source.log_source_id,
            {"change": "added", "name": source.name, "type_name": source.type_name},
        )
    for source in changed_sources:
        previous_name, previous_type_name = known_sources[source.log_source_id].qradar
        await audit.log_source(
            source.log_source_id,
            {
                "change": "changed",
                "name": source.name,
                "type_name": source.type_name,
                "previous_name": previous_name,
                "previous_type_name": previous_type_name,
            },
        )
    await audit.marks(
        LOG_SOURCE_SYNC_ACTION, LOG_SOURCE_OBJECT, source_marks, known_sources, synced_at
    )

    return CatalogSyncReport(
        rules=len(rules),
        log_sources=len(listed_sources),
        rules_added=_ids(rule.rule_id for rule in new_rules),
        rules_changed=_ids(rule.rule_id for rule in changed_rules),
        rules_missing=rule_marks.missing,
        rules_marked_missing=rule_marks.marked,
        rules_returned=rule_marks.returned,
        log_sources_added=_ids(source.log_source_id for source in new_sources),
        log_sources_changed=_ids(source.log_source_id for source in changed_sources),
        log_sources_missing=source_marks.missing,
        log_sources_marked_missing=source_marks.marked,
        log_sources_returned=source_marks.returned,
        log_sources_untyped=_ids(set(inventory.untyped_log_sources) - sources.keys()),
    )


def _rule_fields(rule: SyncedRule) -> tuple[str | bool, ...]:
    return (rule.rule_name, rule.qradar_enabled)


def _source_fields(source: SyncedLogSource) -> tuple[str | bool, ...]:
    return (source.name, source.type_name)


def _marks(known: Mapping[int, _Known], listed: Collection[int]) -> _Marks:
    missing = _ids(known.keys() - set(listed))
    return _Marks(
        missing=missing,
        marked=tuple(entry_id for entry_id in missing if known[entry_id].missing_since is None),
        returned=_ids(
            entry_id
            for entry_id in listed
            if entry_id in known and known[entry_id].missing_since is not None
        ),
    )


@dataclass(frozen=True)
class _Audit:
    """Appends the sync's audit entries in the caller's transaction."""

    session: AsyncSession
    actor: str

    async def rule(self, rule_id: int, details: Mapping[str, JsonValue]) -> None:
        await self._append(RULE_SYNC_ACTION, RULE_OBJECT, rule_id, details)

    async def log_source(self, log_source_id: int, details: Mapping[str, JsonValue]) -> None:
        await self._append(LOG_SOURCE_SYNC_ACTION, LOG_SOURCE_OBJECT, log_source_id, details)

    async def marks(
        self,
        action: str,
        object_type: str,
        marks: _Marks,
        known: Mapping[int, _Known],
        synced_at: datetime,
    ) -> None:
        """`missing` for the entries the sync marked, `returned` for those it unmarked."""
        for entry_id in marks.marked:
            await self._append(
                action,
                object_type,
                entry_id,
                {"change": "missing", "missing_since": synced_at.isoformat()},
            )
        for entry_id in marks.returned:
            since = known[entry_id].missing_since
            await self._append(
                action,
                object_type,
                entry_id,
                {
                    "change": "returned",
                    "previous_missing_since": None if since is None else since.isoformat(),
                },
            )

    async def _append(
        self, action: str, object_type: str, object_id: int, details: Mapping[str, JsonValue]
    ) -> None:
        await append_audit(
            self.session,
            actor_kind=ActorKind.SYSTEM,
            actor_id=self.actor,
            action=action,
            object_type=object_type,
            object_id=str(object_id),
            details=details,
        )


def _ids(ids: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted(ids))
