"""The "Offense ve bağlam" models of docs/impl/contracts.md."""

from typing import Annotated

from pydantic import Field, StringConstraints

from ais0c_contracts.common import (
    AttackTechnique,
    ContractModel,
    ShortText,
    Summary,
    UtcDatetime,
)
from ais0c_contracts.enums import CatalogMode, Confidence, Level


class OffenseSnapshot(ContractModel):
    """Offense summary read from QRadar.

    `description`, `rule_names` and the user names are untrusted: they reach a prompt only
    inside the `untrusted_*` wrapper.
    """

    offense_id: int
    description: Annotated[str, StringConstraints(max_length=500)]
    offense_type: str
    offense_source: str
    rule_ids: list[int]
    rule_names: list[str]
    categories: list[str]
    # Informational only (D-24).
    magnitude: int
    start_time: UtcDatetime
    last_updated_time: UtcDatetime
    event_count: int
    log_source_ids: list[int]
    source_ips: Annotated[list[str], Field(max_length=50)]
    destination_ips: Annotated[list[str], Field(max_length=50)]
    usernames: Annotated[list[str], Field(max_length=50)]


class CatalogRule(ContractModel):
    rule_id: int
    mode: CatalogMode
    min_level: Level | None = None
    context_note: Summary | None = None
    # Mapped to the rule by an operator; None when there are none. The skill router reads
    # them (v0.3, T-26).
    attack_techniques: Annotated[list[AttackTechnique], Field(max_length=20)] | None = None


class CatalogLogSource(ContractModel):
    log_source_id: int
    # QRadar's name for the log source type, synced from QRadar, e.g. "Microsoft Windows
    # Security Event Log"; the skill router reads it (v0.3, T-26).
    type_name: Annotated[str, StringConstraints(max_length=255)] | None = None
    description: ShortText | None = None
    criticality: Level | None = None
    context_note: Summary | None = None


class CatalogContext(ContractModel):
    """Trusted context from the Analysis Catalog (D-25); goes to the `org_context` section."""

    rules: list[CatalogRule]
    log_sources: list[CatalogLogSource]


class CriticalAssetHit(ContractModel):
    value: str
    label: str
    level: Level


class IocHit(ContractModel):
    value: str
    type: str
    source: str
    confidence: Confidence


class EntityResolution(ContractModel):
    ip: str
    time: UtcDatetime
    host: str | None = None
    user: str | None = None


class EnrichmentContext(ContractModel):
    """Output of the deterministic enrichment."""

    catalog: CatalogContext
    critical_asset_hits: list[CriticalAssetHit]
    ioc_hits: list[IocHit]
    entity_resolutions: list[EntityResolution]
    group_id: str | None = None
    # max(catalog floor, asset floor, IOC floor), computed deterministically.
    floor_level: Level | None = None
