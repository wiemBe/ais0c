"""Deterministic enrichment of an offense (architecture §9, §16).

Builds the `EnrichmentContext`: the Analysis Catalog entries of the offense's rules and log
sources, critical asset hits, IOC hits and the floor level. Entity resolution (§16) comes later;
`entity_resolutions` stays empty for now.

Offense fields are untrusted: an address that does not parse is ignored, never an error.
"""

import ipaddress
import logging
from collections.abc import Collection, Iterable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_activities.levels import level_rank, max_level
from ais0c_contracts import (
    CatalogContext,
    CatalogMode,
    CatalogRule,
    CriticalAssetHit,
    EnrichmentContext,
    IocHit,
    Level,
    OffenseSnapshot,
)
from ais0c_storage.enums import CriticalAssetKind
from ais0c_storage.models import CriticalAssetRow
from ais0c_storage.repositories import (
    get_catalog_log_sources,
    get_catalog_rules,
    list_critical_assets,
    to_catalog_log_source,
    to_catalog_rule,
)

type IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_log = logging.getLogger(__name__)


class IocMatcher(Protocol):
    """Finds the offense's indicators in the knowledge store's IOCs."""

    async def match(self, session: AsyncSession, offense: OffenseSnapshot) -> list[IocHit]: ...


class NoIocMatcher:
    """Matches nothing. The knowledge store has no IOC import yet (USTA, Soteryan; KnowledgeSync),
    so there is nothing to match against."""

    async def match(self, session: AsyncSession, offense: OffenseSnapshot) -> list[IocHit]:
        return []


def catalog_mode(rule_ids: Collection[int], rules: Iterable[CatalogRule]) -> CatalogMode:
    """`skip` only when the offense has rules and every one of them is marked `skip`.

    A rule missing from the catalog is analyzed (architecture §9), so one analyzed rule among
    skipped ones is enough to analyze the offense.
    """
    modes = {rule.rule_id: rule.mode for rule in rules}
    if rule_ids and all(modes.get(rule_id) is CatalogMode.SKIP for rule_id in rule_ids):
        return CatalogMode.SKIP
    return CatalogMode.ANALYZE


def catalog_floor(rules: Iterable[CatalogRule]) -> Level | None:
    """The highest `min_level` among the offense's rules (architecture §9: katalog tabanı)."""
    return max_level(*(rule.min_level for rule in rules))


def _addresses(values: Iterable[str]) -> dict[IpAddress, str]:
    found: dict[IpAddress, str] = {}
    for value in values:
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError:
            continue
        found.setdefault(address, str(address))
    return found


def _asset_matches(
    asset: CriticalAssetRow, addresses: dict[IpAddress, str], names: dict[str, str]
) -> list[str]:
    """The offense's values that `asset` covers.

    Stored addresses are validated on write; one that still does not parse is skipped and
    logged, so a bad row cannot stop the intake.
    """
    try:
        match asset.kind:
            case CriticalAssetKind.IP:
                text = addresses.get(ipaddress.ip_address(asset.value))
                return [] if text is None else [text]
            case CriticalAssetKind.CIDR:
                network = ipaddress.ip_network(asset.value)
                return [text for address, text in addresses.items() if address in network]
            case CriticalAssetKind.HOST | CriticalAssetKind.USER:
                name = names.get(asset.value.casefold())
                return [] if name is None else [name]
    except ValueError:
        _log.warning("critical asset %s has an invalid %s value", asset.id, asset.kind.value)
        return []


def match_critical_assets(
    offense: OffenseSnapshot, assets: Iterable[CriticalAssetRow]
) -> list[CriticalAssetHit]:
    """Critical assets the offense touches (S-09).

    `ip` and `cidr` entries are matched against the source and destination IPs and an offense
    source that is an address; `user` entries against the user names and the offense source;
    `host` entries against the offense source. Names are compared without case. `value` of a
    hit is the offense's value.
    """
    addresses = _addresses([*offense.source_ips, *offense.destination_ips, offense.offense_source])
    users = {name.casefold(): name for name in [*offense.usernames, offense.offense_source]}
    hosts = {offense.offense_source.casefold(): offense.offense_source}
    hits: dict[tuple[str, str], CriticalAssetHit] = {}
    for asset in assets:
        names = hosts if asset.kind is CriticalAssetKind.HOST else users
        for value in _asset_matches(asset, addresses, names):
            key = (value, asset.label)
            if key not in hits or level_rank(asset.level) > level_rank(hits[key].level):
                hits[key] = CriticalAssetHit(value=value, label=asset.label, level=asset.level)
    return sorted(hits.values(), key=lambda hit: (hit.value, hit.label))


def floor_level(
    *,
    catalog: Level | None,
    asset_hits: Collection[CriticalAssetHit],
    ioc_hits: Collection[IocHit],
) -> Level | None:
    """max(catalog floor, asset floor, IOC floor) (architecture §9).

    A critical asset hit raises the floor to the asset's level (high or critical); an IOC hit
    raises it to at least high.
    """
    asset_floor = max_level(*(hit.level for hit in asset_hits))
    ioc_floor = Level.HIGH if ioc_hits else None
    return max_level(catalog, asset_floor, ioc_floor)


async def build_enrichment(
    session: AsyncSession,
    offense: OffenseSnapshot,
    *,
    ioc_matcher: IocMatcher,
    group_id: str | None = None,
) -> EnrichmentContext:
    rules = [to_catalog_rule(row) for row in await get_catalog_rules(session, offense.rule_ids)]
    log_sources = [
        to_catalog_log_source(row)
        for row in await get_catalog_log_sources(session, offense.log_source_ids)
    ]
    asset_hits = match_critical_assets(offense, await list_critical_assets(session))
    ioc_hits = await ioc_matcher.match(session, offense)
    return EnrichmentContext(
        catalog=CatalogContext(rules=rules, log_sources=log_sources),
        critical_asset_hits=asset_hits,
        ioc_hits=ioc_hits,
        entity_resolutions=[],
        group_id=group_id,
        floor_level=floor_level(
            catalog=catalog_floor(rules), asset_hits=asset_hits, ioc_hits=ioc_hits
        ),
    )
