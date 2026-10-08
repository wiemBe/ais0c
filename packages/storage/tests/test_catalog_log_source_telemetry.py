"""Migration 0012 and the telemetry classes of log sources (T-068 criterion 1, decision T-95)."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import T0, T1
from storage_postgres import Server
from test_migrations import alembic

from ais0c_storage import TelemetryClass
from ais0c_storage.db import create_sync_engine
from ais0c_storage.models import CatalogLogSourceRow
from ais0c_storage.repositories import (
    SyncedLogSource,
    effective_telemetry_classes,
    get_catalog_log_source,
    list_catalog_log_sources,
    set_catalog_log_sources_missing,
    sync_catalog_log_sources,
)

SYNC = "knowledge-sync"
COLUMNS = ("qradar_enabled", "default_telemetry_classes", "telemetry_classes")


def test_migration_0012_adds_the_three_columns(server: Server, empty_database: str) -> None:
    url = server.app_url(empty_database)
    assert alembic("upgrade", "0011", url=url).returncode == 0
    engine = create_sync_engine(url)

    def columns() -> dict[str, tuple[str, str, str | None]]:
        """Column -> (type, nullable, default) of the three columns that exist."""
        query = text(
            "SELECT column_name, data_type || coalesce('/' || udt_name, ''), is_nullable,"
            " column_default FROM information_schema.columns"
            " WHERE table_name = 'catalog_log_sources' AND column_name = ANY(:names)"
        )
        with engine.connect() as connection:
            found = connection.execute(query, {"names": list(COLUMNS)})
            return {name: (kind, nullable, default) for name, kind, nullable, default in found}

    try:
        assert columns() == {}
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO catalog_log_sources (log_source_id, name, type_name, defined,"
                    " in_scope, updated_by, updated_at)"
                    " VALUES (7, 'LS', 'Linux OS', false, true, 'knowledge-sync', now())"
                )
            )
        upgraded = alembic("upgrade", "head", url=url)
        assert upgraded.returncode == 0, upgraded.stderr
        assert columns() == {
            "qradar_enabled": ("boolean/bool", "NO", "true"),
            "default_telemetry_classes": ("ARRAY/_text", "NO", "'{}'::text[]"),
            "telemetry_classes": ("ARRAY/_text", "YES", None),
        }
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT qradar_enabled, default_telemetry_classes, telemetry_classes FROM catalog_log_sources"
                )
            )
            assert row.one() == (True, [], None)
        downgraded = alembic("downgrade", "0011", url=url)
        assert downgraded.returncode == 0, downgraded.stderr
        assert columns() == {}
        again = alembic("upgrade", "head", url=url)
        assert again.returncode == 0, again.stderr
    finally:
        engine.dispose()


def row(
    *,
    enabled: bool = True,
    default: tuple[str, ...] = (),
    assigned: tuple[str, ...] | None = None,
    missing: bool = False,
) -> CatalogLogSourceRow:
    """An unsaved row; the function reads only these fields."""
    return CatalogLogSourceRow(
        log_source_id=1,
        name="LS",
        type_name="T",
        qradar_enabled=enabled,
        default_telemetry_classes=list(default),
        telemetry_classes=None if assigned is None else list(assigned),
        missing_since=T0 if missing else None,
    )


def test_effective_classes() -> None:
    windows = frozenset({TelemetryClass.WINDOWS})
    assert effective_telemetry_classes(row(default=("windows",))) == windows
    # The admin's classes replace the defaults; an empty assignment is an assignment.
    assert effective_telemetry_classes(row(default=("linux",), assigned=("windows",))) == windows
    assert effective_telemetry_classes(row(default=("linux",), assigned=())) == frozenset()
    assert effective_telemetry_classes(row(default=("firewall", "vpn"))) == frozenset(
        {TelemetryClass.FIREWALL, TelemetryClass.VPN}
    )
    # QRadar disabled it, or no longer lists it: no classes, whoever assigned them.
    assert effective_telemetry_classes(row(enabled=False, default=("windows",))) == frozenset()
    assert effective_telemetry_classes(row(default=("windows",), missing=True)) == frozenset()
    assert effective_telemetry_classes(row(enabled=False, assigned=("windows",))) == frozenset()
    assert effective_telemetry_classes(row(assigned=("windows",), missing=True)) == frozenset()
    assert effective_telemetry_classes(row()) == frozenset()


@pytest.mark.anyio
async def test_list_filters(session: AsyncSession) -> None:
    windows = (TelemetryClass.WINDOWS,)
    await sync_catalog_log_sources(
        session,
        [
            SyncedLogSource(1, "A", "Microsoft Windows Security Event Log", True, windows),
            SyncedLogSource(2, "B", "Microsoft Windows Security Event Log", False, windows),
            SyncedLogSource(3, "C", "Universal LEEF"),
            SyncedLogSource(
                4, "D", "Fortinet FortiGate Security Gateway", True, (TelemetryClass.FIREWALL,)
            ),
            SyncedLogSource(5, "E", "Linux OS", True, (TelemetryClass.LINUX,)),
        ],
        synced_by=SYNC,
        synced_at=T0,
    )
    # An admin's classes beat the type's default; a missing log source counts for nothing.
    five = await get_catalog_log_source(session, 5)
    assert five is not None
    five.telemetry_classes = ["firewall", "vpn"]
    await set_catalog_log_sources_missing(session, [4], missing=True, synced_by=SYNC, synced_at=T1)
    await session.flush()

    async def ids(**filters: object) -> list[int]:
        rows = await list_catalog_log_sources(session, **filters)  # type: ignore[arg-type]
        return [found.log_source_id for found in rows]

    assert await ids(qradar_enabled=False) == [2]
    assert await ids(qradar_enabled=True) == [1, 3, 4, 5]
    assert await ids(telemetry_class=TelemetryClass.WINDOWS) == [1]
    assert await ids(telemetry_class=TelemetryClass.FIREWALL) == [5]
    assert await ids(telemetry_class=TelemetryClass.VPN) == [5]
    assert await ids(telemetry_class=TelemetryClass.LINUX) == []
    assert await ids(unclassified=True) == [3]
    assert await ids(unclassified=False) == [1, 2, 4, 5]
    assert await ids(unclassified=True, telemetry_class=TelemetryClass.WINDOWS) == []
