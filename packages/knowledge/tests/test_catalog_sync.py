"""sync_catalog: QRadar's lists into the Analysis Catalog (criteria 3, 4 and 6), with the
enabled state and the missing marks of contracts v0.4 (T-041 criterion 6).

The database is real. Inventories are built by hand, as read_inventory returns them; names are
made up, type names are QRadar product names.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
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
    ClassDefaults,
    QRadarInventory,
    sync_catalog,
)
from ais0c_storage import ActorKind, TelemetryClass
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
T2 = T1 + timedelta(days=1)
T3 = T2 + timedelta(days=1)

# The columns a sync writes. Every other column is the operator's (or the AI draft's) and a
# sync never changes it (criterion 4).
RULE_QRADAR_COLUMNS = {
    "rule_id",
    "rule_name",
    "qradar_enabled",
    "missing_since",
    "updated_by",
    "updated_at",
}
RULE_OPERATOR_COLUMNS = {
    "defined",
    "mode",
    "min_level",
    "has_automated_action",
    "context_note",
    "ai_draft_note",
    "attack_techniques",
}
LOG_SOURCE_QRADAR_COLUMNS = {
    "log_source_id",
    "name",
    "type_name",
    "qradar_enabled",
    "default_telemetry_classes",
    "missing_since",
    "updated_by",
    "updated_at",
}
LOG_SOURCE_OPERATOR_COLUMNS = {
    "telemetry_classes",
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
    disabled: tuple[int, ...] = (),
    disabled_sources: tuple[int, ...] = (),
) -> QRadarInventory:
    return QRadarInventory(
        rules=tuple(
            SyncedRule(rule_id, name, qradar_enabled=rule_id not in disabled)
            for rule_id, name in sorted(rules.items())
        ),
        log_sources=tuple(
            SyncedLogSource(source_id, name, type_name, source_id not in disabled_sources)
            for source_id, (name, type_name) in sorted(sources.items())
        ),
        untyped_log_sources=untyped,
    )


LAB_RULES = {
    100001: "Excessive Firewall Denies",
    100353: "AIS0C LAB - DCSync by a non-machine account",
}
LAB_SOURCES = {2001: ("DC-LAB-01", WINDOWS_SECURITY), 2002: ("WIN-FW-01", FORTIGATE)}
# Rule 100001 is disabled in QRadar, as 38 of the lab's 134 rules are.
LAB = inventory(LAB_RULES, LAB_SOURCES, disabled=(100001,))


async def sync(
    sessions: Sessions,
    items: QRadarInventory,
    at: datetime = T0,
    class_defaults: ClassDefaults = MappingProxyType({}),
) -> CatalogSyncReport:
    async with sessions.begin() as session:
        return await sync_catalog(session, items, synced_at=at, class_defaults=class_defaults)


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
            "qradar_enabled": enabled,
            "missing_since": None,
            "updated_by": SYNC_ACTOR,
            "updated_at": T0,
        }
        for rule_id, name, enabled in (
            (100001, "Excessive Firewall Denies", False),
            (100353, "AIS0C LAB - DCSync by a non-machine account", True),
        )
    ]
    assert sources == [
        {
            "log_source_id": source_id,
            "name": name,
            "type_name": type_name,
            "qradar_enabled": True,
            "default_telemetry_classes": [],
            "telemetry_classes": None,
            "defined": False,
            "description": None,
            "owner": None,
            "criticality": None,
            "in_scope": True,
            "context_note": None,
            "missing_since": None,
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
            disabled=(100001,),
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
    assert report.rules_changed == (100001,)
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
        "rules_changed": 0,
        "rules_missing": 0,
        "rules_marked_missing": 0,
        "rules_returned": 0,
        "log_sources": 2,
        "log_sources_added": 0,
        "log_sources_changed": 0,
        "log_sources_missing": 0,
        "log_sources_marked_missing": 0,
        "log_sources_returned": 0,
        "log_sources_untyped": 0,
    }


async def test_a_sync_after_operator_edits_changes_nothing_either(sessions: Sessions) -> None:
    await sync(sessions, LAB)
    await define_everything(sessions)
    rows = await catalog(sessions)

    report = await sync(sessions, LAB, at=T1)

    assert await catalog(sessions) == rows
    assert not report.changed


async def test_nothing_is_deleted_and_missing_entries_are_marked(sessions: Sessions) -> None:
    """T-37: an entry QRadar no longer lists stays, with every operator field; only its mark,
    `updated_by` and `updated_at` change."""
    await sync(sessions, LAB)
    await define_everything(sessions)
    rows = await catalog(sessions)

    report = await sync(sessions, inventory({100353: LAB_RULES[100353]}, {}), at=T1)

    rules, sources = await catalog(sessions)
    marked = {"missing_since": T1, "updated_by": SYNC_ACTOR, "updated_at": T1}
    assert rules == [rows[0][0] | marked, rows[0][1]]
    assert sources == [row | marked for row in rows[1]]
    assert report.rules_missing == report.rules_marked_missing == (100001,)
    assert report.log_sources_missing == report.log_sources_marked_missing == (2001, 2002)
    assert (report.rules_returned, report.log_sources_returned) == ((), ())
    assert report.changed


async def test_a_mark_keeps_the_first_missed_sync_until_the_entry_returns(
    sessions: Sessions,
) -> None:
    await sync(sessions, LAB)
    await define_everything(sessions)
    without_them = inventory({100353: LAB_RULES[100353]}, {2001: LAB_SOURCES[2001]})
    await sync(sessions, without_them, at=T1)
    marked = await catalog(sessions)
    entries = [entry.id for entry in await audit_entries(sessions)]

    # Still missing a day later: nothing changes, not even the audit log.
    still = await sync(sessions, without_them, at=T2)

    assert await catalog(sessions) == marked
    assert [entry.id for entry in await audit_entries(sessions)] == entries
    assert (still.rules_missing, still.log_sources_missing) == ((100001,), (2002,))
    assert (still.rules_marked_missing, still.log_sources_marked_missing) == ((), ())
    assert not still.changed

    # QRadar lists both again: the marks go, the operator fields are as they were.
    back = await sync(sessions, LAB, at=T3)

    rules, sources = await catalog(sessions)
    returned = {"missing_since": None, "updated_by": SYNC_ACTOR, "updated_at": T3}
    assert rules == [marked[0][0] | returned, marked[0][1]]
    assert sources == [marked[1][0], marked[1][1] | returned]
    assert (back.rules_returned, back.log_sources_returned) == ((100001,), (2002,))
    assert (back.rules_missing, back.log_sources_missing) == ((), ())
    assert back.changed
    marks = [
        (entry.action, entry.object_id, entry.details)
        for entry in await audit_entries(sessions)
        if entry.details.get("change") in {"missing", "returned"}
    ]
    assert marks == [
        (RULE_SYNC_ACTION, "100001", {"change": "missing", "missing_since": T1.isoformat()}),
        (LOG_SOURCE_SYNC_ACTION, "2002", {"change": "missing", "missing_since": T1.isoformat()}),
        (
            RULE_SYNC_ACTION,
            "100001",
            {"change": "returned", "previous_missing_since": T1.isoformat()},
        ),
        (
            LOG_SOURCE_SYNC_ACTION,
            "2002",
            {"change": "returned", "previous_missing_since": T1.isoformat()},
        ),
    ]

    # Nothing more to do.
    assert not (await sync(sessions, LAB, at=T3 + timedelta(days=1))).changed


async def test_an_untyped_log_source_counts_as_listed(sessions: Sessions) -> None:
    """QRadar lists a missing log source again, with a type its type list lacks: the mark goes;
    the name and type stay what the catalog had."""
    await sync(sessions, LAB)
    await sync(sessions, inventory(LAB_RULES, {2001: LAB_SOURCES[2001]}, disabled=(100001,)), at=T1)

    report = await sync(
        sessions,
        inventory(LAB_RULES, {2001: LAB_SOURCES[2001]}, untyped=(2002,), disabled=(100001,)),
        at=T2,
    )

    _, sources = await catalog(sessions)
    assert (sources[1]["name"], sources[1]["type_name"], sources[1]["missing_since"]) == (
        "WIN-FW-01",
        FORTIGATE,
        None,
    )
    assert report.log_sources_returned == (2002,)
    assert (report.log_sources_untyped, report.log_sources_missing) == ((2002,), ())


async def test_the_enabled_state_follows_qradar_and_is_audited(sessions: Sessions) -> None:
    """T-37: `qradar_enabled` is QRadar's `enabled`; a change is a sync change, audited, and
    leaves the operator's fields alone."""
    await sync(sessions, LAB)
    await define_everything(sessions)
    before, _ = await catalog(sessions)

    # QRadar enables 100001 and disables 100353.
    report = await sync(sessions, inventory(LAB_RULES, LAB_SOURCES, disabled=(100353,)), at=T1)

    rules, _ = await catalog(sessions)
    assert [rule["qradar_enabled"] for rule in rules] == [True, False]
    for rule, previous in zip(rules, before, strict=True):
        assert without(rule, RULE_QRADAR_COLUMNS) == without(previous, RULE_QRADAR_COLUMNS)
        assert (rule["updated_by"], rule["updated_at"]) == (SYNC_ACTOR, T1)
    assert report.rules_changed == (100001, 100353)
    changes = [
        (entry.object_id, entry.details)
        for entry in await audit_entries(sessions)
        if entry.action == RULE_SYNC_ACTION and entry.details["change"] == "changed"
    ]
    assert changes == [
        (
            "100001",
            {
                "change": "changed",
                "rule_name": LAB_RULES[100001],
                "qradar_enabled": True,
                "previous_rule_name": LAB_RULES[100001],
                "previous_qradar_enabled": False,
            },
        ),
        (
            "100353",
            {
                "change": "changed",
                "rule_name": LAB_RULES[100353],
                "qradar_enabled": False,
                "previous_rule_name": LAB_RULES[100353],
                "previous_qradar_enabled": True,
            },
        ),
    ]
    again = await sync(sessions, inventory(LAB_RULES, LAB_SOURCES, disabled=(100353,)), at=T2)
    assert not again.changed


async def test_each_change_is_audited_as_the_sync(sessions: Sessions) -> None:
    await sync(sessions, LAB)
    await define_everything(sessions)
    await sync(
        sessions,
        inventory(
            {100001: "Firewall denies", 100353: LAB_RULES[100353]},
            {2001: ("DC-LAB-01", WINDOWS_SECURITY), 2002: ("WIN-FW-01", LINUX)},
            disabled=(100001,),
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
            {"change": "added", "rule_name": "Excessive Firewall Denies", "qradar_enabled": False},
        ),
        (
            *system,
            RULE_SYNC_ACTION,
            RULE_OBJECT,
            "100353",
            {
                "change": "added",
                "rule_name": "AIS0C LAB - DCSync by a non-machine account",
                "qradar_enabled": True,
            },
        ),
        (
            *system,
            LOG_SOURCE_SYNC_ACTION,
            LOG_SOURCE_OBJECT,
            "2001",
            {
                "change": "added",
                "name": "DC-LAB-01",
                "type_name": WINDOWS_SECURITY,
                "qradar_enabled": True,
                "default_telemetry_classes": [],
            },
        ),
        (
            *system,
            LOG_SOURCE_SYNC_ACTION,
            LOG_SOURCE_OBJECT,
            "2002",
            {
                "change": "added",
                "name": "WIN-FW-01",
                "type_name": FORTIGATE,
                "qradar_enabled": True,
                "default_telemetry_classes": [],
            },
        ),
        (
            *system,
            RULE_SYNC_ACTION,
            RULE_OBJECT,
            "100001",
            {
                "change": "changed",
                "rule_name": "Firewall denies",
                "qradar_enabled": False,
                "previous_rule_name": "Excessive Firewall Denies",
                "previous_qradar_enabled": False,
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
                "qradar_enabled": True,
                "default_telemetry_classes": [],
                "previous_name": "WIN-FW-01",
                "previous_type_name": FORTIGATE,
                "previous_qradar_enabled": True,
                "previous_default_telemetry_classes": [],
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
            LAB_RULES,
            {2001: ("DC-LAB-01", WINDOWS_SECURITY)},
            untyped=(2002, 2003),
            disabled=(100001,),
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


DEFAULTS: ClassDefaults = MappingProxyType(
    {
        WINDOWS_SECURITY: frozenset({TelemetryClass.WINDOWS}),
        FORTIGATE: frozenset({TelemetryClass.VPN, TelemetryClass.FIREWALL}),
    }
)


async def log_source_rows_of(sessions: Sessions) -> dict[int, Columns]:
    _, sources = await catalog(sessions)
    return {source["log_source_id"]: source for source in sources}


async def test_sync_writes_defaults_and_enabled(sessions: Sessions) -> None:
    items = inventory(
        {},
        {
            2001: ("DC-LAB-01", WINDOWS_SECURITY),
            2002: ("WIN-FW-01", FORTIGATE),
            2003: ("SRV-LAB-01", "Universal LEEF"),
        },
        disabled_sources=(2002,),
    )

    report = await sync(sessions, items, class_defaults=DEFAULTS)

    found = await log_source_rows_of(sessions)
    assert {
        source_id: (
            row["qradar_enabled"],
            row["default_telemetry_classes"],
            row["telemetry_classes"],
        )
        for source_id, row in found.items()
    } == {
        2001: (True, ["windows"], None),
        # The enum's order, whatever the file's.
        2002: (False, ["firewall", "vpn"], None),
        2003: (True, [], None),
    }
    assert report.log_sources_added == (2001, 2002, 2003)
    audited = {
        entry.object_id: entry.details
        for entry in await audit_entries(sessions)
        if entry.action == LOG_SOURCE_SYNC_ACTION
    }
    assert audited["2002"] == {
        "change": "added",
        "name": "WIN-FW-01",
        "type_name": FORTIGATE,
        "qradar_enabled": False,
        "default_telemetry_classes": ["firewall", "vpn"],
    }


async def test_disabling_in_qradar_is_a_change(sessions: Sessions) -> None:
    sources = {2001: ("DC-LAB-01", WINDOWS_SECURITY), 2002: ("WIN-FW-01", FORTIGATE)}
    await sync(sessions, inventory({}, sources), class_defaults=DEFAULTS)

    report = await sync(
        sessions, inventory({}, sources, disabled_sources=(2001,)), at=T1, class_defaults=DEFAULTS
    )

    assert report.log_sources_changed == (2001,)
    assert report.changed
    found = await log_source_rows_of(sessions)
    assert (found[2001]["qradar_enabled"], found[2001]["updated_at"]) == (False, T1)
    assert found[2002]["updated_at"] == T0
    changes = [
        entry.details
        for entry in await audit_entries(sessions)
        if entry.action == LOG_SOURCE_SYNC_ACTION and entry.object_id == "2001"
    ]
    assert changes[-1] == {
        "change": "changed",
        "name": "DC-LAB-01",
        "type_name": WINDOWS_SECURITY,
        "qradar_enabled": False,
        "default_telemetry_classes": ["windows"],
        "previous_name": "DC-LAB-01",
        "previous_type_name": WINDOWS_SECURITY,
        "previous_qradar_enabled": True,
        "previous_default_telemetry_classes": ["windows"],
    }


async def test_a_changed_mapping_updates_the_defaults_at_the_next_sync(
    sessions: Sessions,
) -> None:
    items = inventory({}, {2002: ("WIN-FW-01", FORTIGATE)})
    await sync(sessions, items, class_defaults=DEFAULTS)

    report = await sync(
        sessions,
        items,
        at=T1,
        class_defaults={FORTIGATE: frozenset({TelemetryClass.FIREWALL})},
    )

    assert report.log_sources_changed == (2002,)
    assert (await log_source_rows_of(sessions))[2002]["default_telemetry_classes"] == ["firewall"]


async def test_admin_classes_survive_the_sync(sessions: Sessions) -> None:
    items = inventory({}, {2001: ("DC-LAB-01", "Universal LEEF")})
    await sync(sessions, items)
    async with sessions.begin() as session:
        row = await session.get(CatalogLogSourceRow, 2001)
        assert row is not None
        row.telemetry_classes = ["windows", "dns"]

    # The type gets a default, QRadar disables the log source and renames it.
    await sync(
        sessions,
        inventory(
            {},
            {2001: ("DC-LAB-02", "Universal LEEF")},
            disabled_sources=(2001,),
        ),
        at=T1,
        class_defaults={"Universal LEEF": frozenset({TelemetryClass.OTHER})},
    )

    row_after = (await log_source_rows_of(sessions))[2001]
    assert row_after["telemetry_classes"] == ["windows", "dns"]
    assert row_after["default_telemetry_classes"] == ["other"]
    assert (row_after["name"], row_after["qradar_enabled"]) == ("DC-LAB-02", False)


async def test_second_sync_changes_nothing(sessions: Sessions) -> None:
    items = inventory(
        LAB_RULES,
        {2001: ("DC-LAB-01", WINDOWS_SECURITY), 2002: ("WIN-FW-01", FORTIGATE)},
        disabled_sources=(2002,),
    )
    await sync(sessions, items, class_defaults=DEFAULTS)
    before = await catalog(sessions)
    audit_before = len(await audit_entries(sessions))

    report = await sync(sessions, items, at=T1, class_defaults=DEFAULTS)

    assert not report.changed
    assert await catalog(sessions) == before
    assert len(await audit_entries(sessions)) == audit_before
