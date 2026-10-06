"""`critical_assets`: the hand-kept critical asset list (S-09). A hit raises the floor to at
least `high` (architecture §9)."""

import ipaddress
import uuid

from pydantic import TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import Level, ShortText
from ais0c_storage.enums import CriticalAssetKind
from ais0c_storage.models import CriticalAssetRow
from ais0c_storage.repositories._common import fetch_all, get_row, insert_row

CRITICAL_ASSET_LEVELS = frozenset({Level.HIGH, Level.CRITICAL})

# A hit carries the label into the prompt as `CriticalAssetHit.label` (contracts v0.4).
_LABEL: TypeAdapter[str] = TypeAdapter(ShortText)


def _normalized(kind: CriticalAssetKind, value: str) -> str:
    """An `ip` must be an address and a `cidr` a network without host bits; both are stored
    in canonical form. Raises ValueError otherwise."""
    if kind is CriticalAssetKind.IP:
        return str(ipaddress.ip_address(value))
    if kind is CriticalAssetKind.CIDR:
        return str(ipaddress.ip_network(value))
    if not value.strip():
        raise ValueError(f"empty {kind.value}")
    return value


async def add_critical_asset(
    session: AsyncSession, *, kind: CriticalAssetKind, value: str, label: str, level: Level
) -> CriticalAssetRow:
    """`level` is `high` or `critical`. `label` is a short label such as "SWIFT", at most 300
    characters; a longer one raises ValidationError."""
    if level not in CRITICAL_ASSET_LEVELS:
        raise ValueError("a critical asset is high or critical")
    _LABEL.validate_python(label)
    values = dict(kind=kind, value=_normalized(kind, value), label=label, level=level)
    return await insert_row(session, CriticalAssetRow, values)


async def get_critical_asset(session: AsyncSession, asset_id: uuid.UUID) -> CriticalAssetRow | None:
    """The asset as it is in the database now; None when there is no such asset."""
    return await get_row(session, CriticalAssetRow, asset_id)


async def delete_critical_asset(session: AsyncSession, asset_id: uuid.UUID) -> bool:
    """False if there was no such asset."""
    statement = (
        delete(CriticalAssetRow)
        .where(CriticalAssetRow.id == asset_id)
        .returning(CriticalAssetRow.id)
    )
    return await session.scalar(statement) is not None


async def list_critical_assets(
    session: AsyncSession, *, kind: CriticalAssetKind | None = None
) -> list[CriticalAssetRow]:
    statement = select(CriticalAssetRow)
    if kind is not None:
        statement = statement.where(CriticalAssetRow.kind == kind)
    statement = statement.order_by(CriticalAssetRow.kind, CriticalAssetRow.value)
    return await fetch_all(session, statement)
