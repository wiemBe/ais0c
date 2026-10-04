"""Database set-up and reads for the activity tests, through the storage repositories."""

from collections.abc import Sequence

from activity_payloads import T0

from ais0c_activities import SessionFactory
from ais0c_contracts import CaseSource, CatalogMode, Level
from ais0c_storage.enums import CaseStatus, CriticalAssetKind, OffenseStatus
from ais0c_storage.models import CaseRow, OffenseGroupRow, OffenseSeenRow
from ais0c_storage.repositories import (
    SyncedLogSource,
    SyncedRule,
    add_critical_asset,
    create_case,
    get_case,
    get_offense_group,
    get_offense_seen,
    set_case_status,
    sync_catalog_log_sources,
    sync_catalog_rules,
    update_catalog_rule,
    update_offense_seen,
)


async def catalog_rule(
    sessions: SessionFactory,
    rule_id: int,
    *,
    mode: CatalogMode = CatalogMode.ANALYZE,
    min_level: Level | None = None,
    attack_techniques: Sequence[str] = (),
) -> None:
    """An operator-defined catalog entry; `skip` rules are the ones with automated actions."""
    async with sessions.begin() as session:
        rule = SyncedRule(rule_id=rule_id, rule_name=f"Rule {rule_id}")
        await sync_catalog_rules(session, [rule], synced_by="sync", synced_at=T0)
        await update_catalog_rule(
            session,
            rule_id,
            mode=mode,
            min_level=min_level,
            has_automated_action=mode is CatalogMode.SKIP,
            context_note=None,
            attack_techniques=attack_techniques,
            updated_by="admin",
            updated_at=T0,
        )


async def catalog_log_source(sessions: SessionFactory, log_source_id: int, type_name: str) -> None:
    """A log source as the QRadar sync adds it."""
    async with sessions.begin() as session:
        source = SyncedLogSource(log_source_id, f"Log source {log_source_id}", type_name)
        await sync_catalog_log_sources(session, [source], synced_by="sync", synced_at=T0)


async def critical_asset(
    sessions: SessionFactory,
    kind: CriticalAssetKind,
    value: str,
    label: str,
    level: Level = Level.HIGH,
) -> None:
    async with sessions.begin() as session:
        await add_critical_asset(session, kind=kind, value=value, label=label, level=level)


async def seen(sessions: SessionFactory, offense_id: int) -> OffenseSeenRow | None:
    async with sessions() as session:
        return await get_offense_seen(session, offense_id)


async def group(sessions: SessionFactory, group_id: str) -> OffenseGroupRow | None:
    async with sessions() as session:
        return await get_offense_group(session, group_id)


async def case(sessions: SessionFactory, case_id: str) -> CaseRow | None:
    async with sessions() as session:
        return await get_case(session, case_id)


async def start_case_row(
    sessions: SessionFactory, offense_id: int, *, status: CaseStatus = CaseStatus.RUNNING
) -> None:
    """The offense's case as `start_case` and the case's first evaluation leave it."""
    case_id = f"case-{offense_id}"
    async with sessions.begin() as session:
        await update_offense_seen(
            session, offense_id, status=OffenseStatus.RUNNING, case_id=case_id
        )
        await create_case(
            session,
            case_id=case_id,
            source=CaseSource.OFFENSE,
            offense_id=offense_id,
            sla_due_at=T0,
            workflow_id=case_id,
            run_id="run-1",
        )
        if status is not CaseStatus.RUNNING:
            await set_case_status(session, case_id, status)
