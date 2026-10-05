"""sync_catalog: QRadar's lists into the Analysis Catalog (criteria 3, 4 and 6).

The database is real. Inventories are built by hand, as read_inventory returns them; names are
made up, type names are QRadar product names.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_contracts import CatalogMode, Level
from ais0c_knowledge.catalog import (
    LOG_SOURCE_OBJECT,
    LOG_SOURCE_SYNC_ACTION,
    RULE_OBJECT,
    RULE_SYNC_ACTION,
    SYNC_ACTOR,
    CatalogSyncReport,
    QRadarInventory,
    sync_catalog,
)
from ais0c_storage import ActorKind
from ais0c_storage.models import AuditLogRow, CatalogLogSourceRow, CatalogRuleRow
from ais0c_storage.repositories import (
    SyncedLogSource,
    SyncedRule,
    list_catalog_log_sources,
    list_catalog_rules,
    set_catalog_rule_draft,
    update_catalog_log_source,
    update_catalog_rule,
)

from .catalog_helpers import FORTIGATE, LINUX, WINDOWS_SECURITY

pytestmark = pytest.mark.anyio

Sessions = async_sessionmaker[AsyncSession]
Columns = dict[str, Any]

T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)

# The columns a sync writes. Every other column is the operator's (or the AI draft's) and a
# sync never changes it (criterion 4).
RULE_QRADAR_COLUMNS = {"rule_id", "rule_name", "updated_by", "updated_at"}
RULE_OPERATOR_COLUMNS = {
    "defined",
    "mode",
    "min_level",
    "has_automated_action",
    "context_note",
    "ai_draft_note",
    "attack_techniques",
}
LOG_SOURCE_QRADAR_COLUMNS = {"log_source_id", "name", "type_name", "updated_by", "updated_at"}
LOG_SOURCE_OPERATOR_COLUMNS = {
    "defined",
    "description",
    "owner",
    "criticality",
    "in_scope",
    "context_note",
}


def inventory(
    rules: dict[int, str],
    sources: dict[int, tuple[str, str]],
    untyped: tuple[int, ...] = (),
) -> QRadarInventory:
    return QRadarInventory(
        rules=tuple(SyncedRule(rule_id, name) for rule_id, name in sorted(rules.items())),
        log_sources=tuple(
            SyncedLogSource(source_id, name, type_name)
            for source_id, (name, type_name) in sorted(sources.items())
        ),
        untyped_log_sources=untyped,
    )


LAB = inventory(
    {100001: "Excessive Firewall Denies", 100353: "AIS0C LAB - DCSync by a non-machine account"},
    {2001: ("DC-LAB-01", WINDOWS_SECURITY), 2002: ("WIN-FW-01", FORTIGATE)},
)


async def sync(sessions: Sessions, items: QRadarInventory, at: datetime = T0) -> CatalogSyncReport:
    async with sessions.begin() as session:
        return await sync_catalog(session, items, synced_at=at)


def columns(row: CatalogRuleRow | CatalogLogSourceRow) -> Columns:
    return {
        attribute.key: getattr(row, attribute.key) for attribute in inspect(row).mapper.column_attrs
    }


async def catalog(sessions: Sessions) -> tuple[list[Columns], list[Columns]]:
    """Every column of every catalog row, by ID."""
    async with sessions() as session:
        rules = await list_catalog_rules(session)
        sources = await list_catalog_log_sources(session)
    return [columns(row) for row in rules], [columns(row) for row in sources]


async def audit_entries(sessions: Sessions) -> list[AuditLogRow]:
    async with sessions() as session:
        return list(await session.scalars(select(AuditLogRow).order_by(AuditLogRow.id)))


async def define_everything(sessions: Sessions) -> None:
    """Admins fill in every operator field of LAB's entries."""
    async with sessions.begin() as session:
        await update_catalog_rule(
            session,
            100001,
            mode=CatalogMode.SKIP,
            min_level=Level.LOW,
            has_automated_action=True,
            context_note="QRadar blocks the source by itself; nothing to analyze.",
            attack_techniques=["T1046"],
            updated_by="admin-1",
            updated_at=T0 + timedelta(hours=1),
        )
        await set_catalog_rule_draft(session, 100001, "Firewall denies from one source.")
        await update_catalog_rule(
            session,
            100353,
            mode=CatalogMode.ANALYZE,
            min_level=Level.CRITICAL,
            has_automated_action=False,
            context_note="Only the AD Connect account replicates directories.",
            attack_techniques=["T1003.006"],
            updated_by="admin-2",
            updated_at=T0 + timedelta(hours=2),
        )
        await update_catalog_log_source(
            session,
            2001,
            description="Domain controller",
            owner="AD team",
            criticality=Level.CRITICAL,
            in_scope=True,
            context_note="Primary DC of the head office.",
            updated_by="admin-1",
            updated_at=T0 + timedelta(hours=3),
        )
        await update_catalog_log_source(
            session,
            2002,
            description="Branch firewall",
            owner="Network team",
            criticality=Level.MEDIUM,
            in_scope=False,
            context_note=None,
            updated_by="admin-2",
            updated_at=T0 + timedelta(hours=4),
        )


def without(row: Columns, names: set[str]) -> Columns:
    return {name: value for name, value in row.items() if name not in names}


def test_every_catalog_column_belongs_to_qradar_or_to_the_operator() -> None:
    """A new catalog column must be sorted into one of the two sets above."""
    assert {column.key for column in CatalogRuleRow.__table__.columns} == (
        RULE_QRADAR_COLUMNS | RULE_OPERATOR_COLUMNS
    )
    assert {column.key for column in CatalogLogSourceRow.__table__.columns} == (
        LOG_SOURCE_QRADAR_COLUMNS | LOG_SOURCE_OPERATOR_COLUMNS
    )


async def test_new_rules_and_log_sources_are_added_undefined_and_analyzed(
    sessions: Sessions,
) -> None:
    report = await sync(sessions, LAB)

    rules, sources = await catalog(sessions)
    assert rules == [
        {
            "rule_id": rule_id,
            "rule_name": name,
            "defined": False,
            "mode": CatalogMode.ANALYZE,
            "min_level": None,
            "has_automated_action": False,
            "context_note": None,
            "ai_draft_note": None,
            "attack_techniques": [],
            "updated_by": SYNC_ACTOR,
            "updated_at": T0,
        }
        for rule_id, name in (
            (100001, "Excessive Firewall Denies"),
            (100353, "AIS0C LAB - DCSync by a non-machine account"),
        )
    ]
    assert sources == [
        {
            "log_source_id": source_id,
            "name": name,
            "type_name": type_name,
            "defined": False,
            "description": None,
            "owner": None,
            "criticality": None,
            "in_scope": True,
            "context_note": None,
            "updated_by": SYNC_ACTOR,
            "updated_at": T0,
        }
        for source_id, name, type_name in (
            (2001, "DC-LAB-01", WINDOWS_SECURITY),
            (2002, "WIN-FW-01", FORTIGATE),
        )
    ]
    assert report == CatalogSyncReport(
        rules=2,
        log_sources=2,
        rules_added=(100001, 100353),
        log_sources_added=(2001, 2002),
    )
    assert report.changed


async def test_operator_fields_are_never_overwritten(sessions: Sessions) -> None:
    await sync(sessions, LAB)
    await define_everything(sessions)
    rules_before, sources_before = await catalog(sessions)

    # QRadar renames a rule and a log source and gives the other log source another type.
    report = await sync(
        sessions,
        inventory(
            {
                100001: "Excessive Firewall Denies (renamed)",
                100353: "AIS0C LAB - DCSync by a non-machine account",
            },
            {
                2001: ("DC-LAB-01", f"{WINDOWS_SECURITY} (custom)"),
                2002: ("WIN-FW-02", FORTIGATE),
            },
        ),
        at=T1,
    )

    rules, sources = await catalog(sessions)
    for before, after in zip(rules_before + sources_before, rules + sources, strict=True):
        operator = RULE_OPERATOR_COLUMNS if "rule_id" in before else LOG_SOURCE_OPERATOR_COLUMNS
        assert {name: after[name] for name in operator} == {name: before[name] for name in operator}
        assert after["defined"] is True
    assert (rules[0]["rule_name"], rules[0]["updated_by"], rules[0]["updated_at"]) == (
        "Excessive Firewall Denies (renamed)",
        SYNC_ACTOR,
        T1,
    )
    # An entry QRadar did not change is not touched at all.
    assert rules[1] == rules_before[1]
    assert (sources[0]["type_name"], sources[1]["name"]) == (
        f"{WINDOWS_SECURITY} (custom)",
        "WIN-FW-02",
    )
    assert report.rules_renamed == (100001,)
    assert report.log_sources_changed == (2001, 2002)
    assert (report.rules_added, report.log_sources_added) == ((), ())


async def test_a_second_sync_changes_nothing(sessions: Sessions) -> None:
    first = await sync(sessions, LAB)
    rows = await catalog(sessions)
    entries = [entry.id for entry in await audit_entries(sessions)]

    second = await sync(sessions, LAB, at=T1)

    assert await catalog(sessions) == rows
    assert [entry.id for entry in await audit_entries(sessions)] == entries
    assert first.changed
    assert not second.changed
    assert second.counts() == {
        "rules": 2,
        "rules_added": 0,
        "rules_renamed": 0,
        "rules_missing": 0,
        "log_sources": 2,
        "log_sources_added": 0,
        "log_sources_changed": 0,
        "log_sources_missing": 0,
        "log_sources_untyped": 0,
    }


async def test_a_sync_after_operator_edits_changes_nothing_either(sessions: Sessions) -> None:
    await sync(sessions, LAB)
    await define_everything(sessions)
    rows = await catalog(sessions)

    report = await sync(sessions, LAB, at=T1)

    assert await catalog(sessions) == rows
    assert not report.changed


async def test_nothing_is_deleted_and_missing_entries_are_reported(sessions: Sessions) -> None:
    await sync(sessions, LAB)
    await define_everything(sessions)
    rows = await catalog(sessions)

    report = await sync(sessions, inventory({100353: LAB.rules[1].rule_name}, {}), at=T1)

    assert await catalog(sessions) == rows
    assert report.rules_missing == (100001,)
    assert report.log_sources_missing == (2001, 2002)
    assert not report.changed


async def test_each_change_is_audited_as_the_sync(sessions: Sessions) -> None:
    await sync(sessions, LAB)
    await define_everything(sessions)
    await sync(
        sessions,
        inventory(
            {100001: "Firewall denies", 100353: LAB.rules[1].rule_name},
            {2001: ("DC-LAB-01", WINDOWS_SECURITY), 2002: ("WIN-FW-01", LINUX)},
        ),
        at=T1,
    )

    synced = [
        (
            entry.actor_kind,
            entry.actor_id,
            entry.action,
            entry.object_type,
            entry.object_id,
            entry.details,
        )
        for entry in await audit_entries(sessions)
        if entry.actor_id == SYNC_ACTOR
    ]
    system = (ActorKind.SYSTEM, SYNC_ACTOR)
    assert synced == [
        (
            *system,
            RULE_SYNC_ACTION,
            RULE_OBJECT,
            "100001",
            {"change": "added", "rule_name": "Excessive Firewall Denies"},
        ),
        (
            *system,
            RULE_SYNC_ACTION,
            RULE_OBJECT,
            "100353",
            {"change": "added", "rule_name": "AIS0C LAB - DCSync by a non-machine account"},
        ),
        (
            *system,
            LOG_SOURCE_SYNC_ACTION,
            LOG_SOURCE_OBJECT,
            "2001",
            {"change": "added", "name": "DC-LAB-01", "type_name": WINDOWS_SECURITY},
        ),
        (
            *system,
            LOG_SOURCE_SYNC_ACTION,
            LOG_SOURCE_OBJECT,
            "2002",
            {"change": "added", "name": "WIN-FW-01", "type_name": FORTIGATE},
        ),
        (
            *system,
            RULE_SYNC_ACTION,
            RULE_OBJECT,
            "100001",
            {
                "change": "renamed",
                "rule_name": "Firewall denies",
                "previous_rule_name": "Excessive Firewall Denies",
            },
        ),
        (
            *system,
            LOG_SOURCE_SYNC_ACTION,
            LOG_SOURCE_OBJECT,
            "2002",
            {
                "change": "changed",
                "name": "WIN-FW-01",
                "type_name": LINUX,
                "previous_name": "WIN-FW-01",
                "previous_type_name": FORTIGATE,
            },
        ),
    ]


async def test_log_sources_of_an_unknown_type_are_left_as_they_are(sessions: Sessions) -> None:
    await sync(sessions, LAB)
    rows = await catalog(sessions)

    # QRadar lists log source 2002 with a type it does not list, and a new one the same way.
    report = await sync(
        sessions,
        inventory(
            {rule.rule_id: rule.rule_name for rule in LAB.rules},
            {2001: ("DC-LAB-01", WINDOWS_SECURITY)},
            untyped=(2002, 2003),
        ),
        at=T1,
    )

    assert await catalog(sessions) == rows
    assert report.log_sources_untyped == (2002, 2003)
    assert report.log_sources_missing == ()
    assert report.log_sources == 3
    assert not report.changed


async def test_syncs_take_turns(sessions: Sessions) -> None:
    """A second sync waits for the first one's transaction, then finds nothing to do."""
    async with sessions.begin() as session:
        first = await sync_catalog(session, LAB, synced_at=T0)
        second = asyncio.create_task(sync(sessions, LAB, at=T1))
        await _until_a_sync_waits(sessions)
        assert not second.done()

    assert first.changed
    assert not (await second).changed
    assert len(await audit_entries(sessions)) == 4


async def _until_a_sync_waits(sessions: Sessions) -> None:
    waiting = (
        select(func.count())
        .select_from(text("pg_locks"))
        .where(text("locktype = 'advisory' AND NOT granted"))
    )
    async with asyncio.timeout(10):
        while True:
            async with sessions() as observer:
                if await observer.scalar(waiting):
                    return
            await asyncio.sleep(0.05)
