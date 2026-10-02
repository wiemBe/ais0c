"""Repository functions of `critical_assets` and `audit_log` (criterion 6)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import Level
from ais0c_storage.enums import ActorKind, CriticalAssetKind
from ais0c_storage.repositories import (
    add_critical_asset,
    append_audit,
    delete_critical_asset,
    list_audit,
    list_critical_assets,
)

pytestmark = pytest.mark.anyio


async def test_critical_assets(session: AsyncSession) -> None:
    swift = await add_critical_asset(
        session,
        kind=CriticalAssetKind.CIDR,
        value="198.51.100.0/24",
        label="SWIFT",
        level=Level.CRITICAL,
    )
    db = await add_critical_asset(
        session,
        kind=CriticalAssetKind.IP,
        value="2001:DB8::0:1",
        label="Core banking DB",
        level=Level.HIGH,
    )
    await add_critical_asset(
        session, kind=CriticalAssetKind.HOST, value="dc01.example.com", label="DC", level=Level.HIGH
    )

    assert swift.id.version == 7
    # Addresses are stored in canonical form.
    assert db.value == "2001:db8::1"
    assets = await list_critical_assets(session)
    assert [(asset.kind, asset.value) for asset in assets] == [
        (CriticalAssetKind.CIDR, "198.51.100.0/24"),
        (CriticalAssetKind.HOST, "dc01.example.com"),
        (CriticalAssetKind.IP, "2001:db8::1"),
    ]
    ips = await list_critical_assets(session, kind=CriticalAssetKind.IP)
    assert [asset.label for asset in ips] == ["Core banking DB"]

    assert await delete_critical_asset(session, swift.id) is True
    assert await delete_critical_asset(session, swift.id) is False
    assert await delete_critical_asset(session, uuid.uuid4()) is False
    assert len(await list_critical_assets(session)) == 2


@pytest.mark.parametrize(
    ("kind", "value", "level", "error"),
    [
        pytest.param(
            CriticalAssetKind.IP, "203.0.113.999", Level.HIGH, "does not appear", id="bad-ip"
        ),
        pytest.param(
            CriticalAssetKind.CIDR, "198.51.100.7/24", Level.HIGH, "host bits", id="host-bits"
        ),
        pytest.param(CriticalAssetKind.USER, " ", Level.HIGH, "empty user", id="empty-user"),
        pytest.param(
            CriticalAssetKind.HOST,
            "db01.example.com",
            Level.MEDIUM,
            "high or critical",
            id="medium",
        ),
    ],
)
async def test_invalid_critical_assets_are_refused(
    session: AsyncSession, kind: CriticalAssetKind, value: str, level: Level, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        await add_critical_asset(session, kind=kind, value=value, label="x", level=level)


async def test_audit_entries(session: AsyncSession) -> None:
    first = await append_audit(
        session,
        actor_kind=ActorKind.USER,
        actor_id="admin01",
        action="catalog.rule.update",
        object_type="catalog_rule",
        object_id="100201",
        details={"mode": {"old": "analyze", "new": "skip"}},
    )
    await session.commit()
    second = await append_audit(
        session,
        actor_kind=ActorKind.SYSTEM,
        actor_id="executor",
        action="note.write",
        object_type="offense",
        object_id="12345",
    )
    await session.commit()

    assert first.at is not None
    assert first.at.utcoffset() == timedelta(0)
    assert second.id > first.id
    assert second.details == {}
    entries = await list_audit(session)
    assert [entry.action for entry in entries] == ["note.write", "catalog.rule.update"]
    assert entries[1].details == {"mode": {"old": "analyze", "new": "skip"}}
    by_object = await list_audit(session, object_type="catalog_rule", object_id="100201")
    assert [entry.id for entry in by_object] == [first.id]
    assert [entry.id for entry in await list_audit(session, actor_id="executor")] == [second.id]
    assert [entry.id for entry in await list_audit(session, action="note.write")] == [second.id]
    assert len(await list_audit(session, limit=1)) == 1
    assert [entry.id for entry in await list_audit(session, since=second.at)] == [second.id]
    assert [entry.id for entry in await list_audit(session, until=second.at)] == [first.id]
