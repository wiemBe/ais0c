"""Bringing the Analysis Catalog in line with QRadar (architecture §9, D-25; KnowledgeSync).

`sync_catalog` applies a complete read of QRadar (`read_inventory`) to `catalog_rules` and
`catalog_log_sources`:

- A new rule is added undefined (`defined=false`) in mode `analyze`, with no floor and no
  note; a new log source is added undefined and in scope. The UI lists undefined entries for
  an operator to fill in (T-029); until then they are analyzed (§9).
- An existing entry gets only what comes from QRadar: a rule its name, a log source its name
  and type name. The fields operators fill in never change: a rule's mode, floor, context
  note, automated action and ATT&CK techniques (T-26); a log source's description, owner,
  criticality, scope and context note; whether an entry is defined; the AI's draft note.
- Nothing is deleted. An entry QRadar no longer lists is only reported as missing; the data
  model has no field to mark it.
- A sync that finds nothing to change writes nothing: no row, no `updated_at`, no audit
  entry. Running it twice in a row changes nothing the second time.

The rows are written by the storage repository (`sync_catalog_rules`,
`sync_catalog_log_sources`), whose updates touch only the QRadar fields. Each change is
audited as the system actor `knowledge-sync`, under `catalog.rule.sync` or
`catalog.log_source.sync`, with the change in `details`. Syncs take turns on a
transaction-scoped advisory lock: two runs, such as a retry that starts while a slow attempt
still runs, never interleave, and each one's report is exact.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_knowledge.catalog.inventory import QRadarInventory
from ais0c_storage import ActorKind
from ais0c_storage.repositories import (
    append_audit,
    list_catalog_log_sources,
    list_catalog_rules,
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
    rules_renamed: tuple[int, ...] = ()
    rules_missing: tuple[int, ...] = ()
    """In the catalog but no longer listed by QRadar; kept as they are."""
    log_sources_added: tuple[int, ...] = ()
    log_sources_changed: tuple[int, ...] = ()
    """Renamed, or given another type, in QRadar."""
    log_sources_missing: tuple[int, ...] = ()
    """In the catalog but no longer listed by QRadar; kept as they are."""
    log_sources_untyped: tuple[int, ...] = ()
    """Listed with a type that QRadar's type list does not have; neither added nor changed."""

    @property
    def changed(self) -> bool:
        """Whether the sync changed the catalog."""
        return bool(
            self.rules_added
            or self.rules_renamed
            or self.log_sources_added
            or self.log_sources_changed
        )

    def counts(self) -> dict[str, int]:
        """The report in numbers, e.g. for a workflow's result."""
        return {
            "rules": self.rules,
            "rules_added": len(self.rules_added),
            "rules_renamed": len(self.rules_renamed),
            "rules_missing": len(self.rules_missing),
            "log_sources": self.log_sources,
            "log_sources_added": len(self.log_sources_added),
            "log_sources_changed": len(self.log_sources_changed),
            "log_sources_missing": len(self.log_sources_missing),
            "log_sources_untyped": len(self.log_sources_untyped),
        }


async def sync_catalog(
    session: AsyncSession,
    inventory: QRadarInventory,
    *,
    synced_at: datetime,
    actor: str = SYNC_ACTOR,
) -> CatalogSyncReport:
    """Apply `inventory`, a complete read of QRadar, to the catalog in the caller's
    transaction. `synced_at` becomes the `updated_at` of the entries it adds or changes."""
    await session.execute(select(func.pg_advisory_xact_lock(_LOCK_KEY)))
    rule_names = {row.rule_id: row.rule_name for row in await list_catalog_rules(session)}
    source_names = {
        row.log_source_id: (row.name, row.type_name)
        for row in await list_catalog_log_sources(session)
    }
    rules = {rule.rule_id: rule for rule in inventory.rules}
    sources = {source.log_source_id: source for source in inventory.log_sources}

    new_rules = [rule for rule_id, rule in rules.items() if rule_id not in rule_names]
    renamed_rules = [
        rule
        for rule_id, rule in rules.items()
        if rule_id in rule_names and rule_names[rule_id] != rule.rule_name
    ]
    new_sources = [source for source_id, source in sources.items() if source_id not in source_names]
    changed_sources = [
        source
        for source_id, source in sources.items()
        if source_id in source_names and source_names[source_id] != (source.name, source.type_name)
    ]

    if new_rules or renamed_rules:
        await sync_catalog_rules(
            session, [*new_rules, *renamed_rules], synced_by=actor, synced_at=synced_at
        )
    if new_sources or changed_sources:
        await sync_catalog_log_sources(
            session, [*new_sources, *changed_sources], synced_by=actor, synced_at=synced_at
        )

    for rule in new_rules:
        await _audit(
            session,
            actor,
            RULE_SYNC_ACTION,
            RULE_OBJECT,
            rule.rule_id,
            {"change": "added", "rule_name": rule.rule_name},
        )
    for rule in renamed_rules:
        await _audit(
            session,
            actor,
            RULE_SYNC_ACTION,
            RULE_OBJECT,
            rule.rule_id,
            {
                "change": "renamed",
                "rule_name": rule.rule_name,
                "previous_rule_name": rule_names[rule.rule_id],
            },
        )
    for source in new_sources:
        await _audit(
            session,
            actor,
            LOG_SOURCE_SYNC_ACTION,
            LOG_SOURCE_OBJECT,
            source.log_source_id,
            {"change": "added", "name": source.name, "type_name": source.type_name},
        )
    for source in changed_sources:
        previous_name, previous_type_name = source_names[source.log_source_id]
        await _audit(
            session,
            actor,
            LOG_SOURCE_SYNC_ACTION,
            LOG_SOURCE_OBJECT,
            source.log_source_id,
            {
                "change": "changed",
                "name": source.name,
                "type_name": source.type_name,
                "previous_name": previous_name,
                "previous_type_name": previous_type_name,
            },
        )

    listed_sources = inventory.log_source_ids()
    return CatalogSyncReport(
        rules=len(rules),
        log_sources=len(listed_sources),
        rules_added=_ids(rule.rule_id for rule in new_rules),
        rules_renamed=_ids(rule.rule_id for rule in renamed_rules),
        rules_missing=_ids(rule_names.keys() - rules.keys()),
        log_sources_added=_ids(source.log_source_id for source in new_sources),
        log_sources_changed=_ids(source.log_source_id for source in changed_sources),
        log_sources_missing=_ids(source_names.keys() - listed_sources),
        log_sources_untyped=_ids(set(inventory.untyped_log_sources) - sources.keys()),
    )


async def _audit(
    session: AsyncSession,
    actor: str,
    action: str,
    object_type: str,
    object_id: int,
    details: Mapping[str, JsonValue],
) -> None:
    await append_audit(
        session,
        actor_kind=ActorKind.SYSTEM,
        actor_id=actor,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        details=details,
    )


def _ids(ids: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted(ids))
