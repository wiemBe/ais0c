"""Repository functions of `catalog_rules` and `catalog_log_sources` (criterion 6), with
`qradar_enabled` and `missing_since` of contracts v0.4 (T-041 criterion 6)."""

from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import T0, T1

from ais0c_contracts import CatalogLogSource, CatalogMode, CatalogRule, Level
from ais0c_storage.enums import TelemetryClass
from ais0c_storage.errors import NotFoundError
from ais0c_storage.models import CatalogRuleRow
from ais0c_storage.repositories import (
    SyncedLogSource,
    SyncedRule,
    accept_catalog_rule_draft,
    get_catalog_log_source,
    get_catalog_log_sources,
    get_catalog_rule,
    get_catalog_rules,
    list_catalog_log_sources,
    list_catalog_rules,
    set_catalog_log_sources_missing,
    set_catalog_rule_draft,
    set_catalog_rules_missing,
    sync_catalog_log_sources,
    sync_catalog_rules,
    to_catalog_log_source,
    to_catalog_rule,
    update_catalog_log_source,
    update_catalog_rule,
)

pytestmark = pytest.mark.anyio

SYNC = "knowledge-sync"
NOTE = "Fires often from the vulnerability scanners on Tuesday nights."


async def define_rule(session: AsyncSession, rule_id: int, mode: CatalogMode) -> CatalogRuleRow:
    return await update_catalog_rule(
        session,
        rule_id,
        mode=mode,
        min_level=Level.HIGH,
        has_automated_action=mode is CatalogMode.SKIP,
        context_note=NOTE,
        updated_by="admin01",
        updated_at=T1,
    )


async def test_sync_adds_new_rules_as_undefined(session: AsyncSession) -> None:
    rules = [SyncedRule(100201, "Excessive Firewall Accepts"), SyncedRule(100305, "DCSync")]

    new_ids = await sync_catalog_rules(session, rules, synced_by=SYNC, synced_at=T0)

    assert new_ids == [100201, 100305]
    rule = await get_catalog_rule(session, 100201)
    assert rule is not None
    assert (rule.defined, rule.mode, rule.min_level, rule.has_automated_action) == (
        False,
        CatalogMode.ANALYZE,
        None,
        False,
    )
    assert (rule.context_note, rule.updated_by, rule.updated_at) == (None, SYNC, T0)


async def test_sync_renames_and_keeps_operator_fields(session: AsyncSession) -> None:
    await sync_catalog_rules(
        session,
        [SyncedRule(1, "Old name"), SyncedRule(2, "Unchanged")],
        synced_by=SYNC,
        synced_at=T0,
    )
    await define_rule(session, 1, CatalogMode.SKIP)
    later = T1 + timedelta(days=1)

    new_ids = await sync_catalog_rules(
        session,
        [SyncedRule(1, "New name"), SyncedRule(2, "Unchanged"), SyncedRule(3, "Brand new")],
        synced_by=SYNC,
        synced_at=later,
    )

    assert new_ids == [3]
    renamed = await get_catalog_rule(session, 1)
    unchanged = await get_catalog_rule(session, 2)
    assert renamed is not None
    assert unchanged is not None
    assert (renamed.rule_name, renamed.updated_by, renamed.updated_at) == ("New name", SYNC, later)
    assert (renamed.defined, renamed.mode, renamed.context_note) == (True, CatalogMode.SKIP, NOTE)
    assert unchanged.updated_at == T0


async def test_sync_handles_large_lists_and_repeated_ids(session: AsyncSession) -> None:
    rules = [SyncedRule(rule_id, f"Rule {rule_id}") for rule_id in range(1, 2501)]
    rules.append(SyncedRule(7, "Rule 7, renamed in the same read"))

    new_ids = await sync_catalog_rules(session, rules, synced_by=SYNC, synced_at=T0)

    assert new_ids == list(range(1, 2501))
    rule = await get_catalog_rule(session, 7)
    assert rule is not None
    assert rule.rule_name == "Rule 7, renamed in the same read"
    assert await sync_catalog_rules(session, [], synced_by=SYNC, synced_at=T0) == []


async def test_admin_edit_defines_a_rule(session: AsyncSession) -> None:
    await sync_catalog_rules(session, [SyncedRule(1, "Rule")], synced_by=SYNC, synced_at=T0)

    rule = await define_rule(session, 1, CatalogMode.SKIP)

    assert (rule.defined, rule.mode, rule.min_level, rule.has_automated_action) == (
        True,
        CatalogMode.SKIP,
        Level.HIGH,
        True,
    )
    assert (rule.context_note, rule.updated_by, rule.updated_at) == (NOTE, "admin01", T1)
    assert to_catalog_rule(rule) == CatalogRule(
        rule_id=1, mode=CatalogMode.SKIP, min_level=Level.HIGH, context_note=NOTE
    )
    with pytest.raises(ValidationError):
        await update_catalog_rule(
            session,
            1,
            mode=CatalogMode.ANALYZE,
            min_level=None,
            has_automated_action=False,
            context_note="x" * 601,
            updated_by="admin01",
            updated_at=T1,
        )
    with pytest.raises(NotFoundError):
        await define_rule(session, 404, CatalogMode.ANALYZE)


async def test_admin_edit_maps_attack_techniques(session: AsyncSession) -> None:
    await sync_catalog_rules(session, [SyncedRule(1, "DCSync")], synced_by=SYNC, synced_at=T0)
    new = await get_catalog_rule(session, 1)
    assert new is not None
    assert new.attack_techniques == []
    assert to_catalog_rule(new).attack_techniques is None

    rule = await update_catalog_rule(
        session,
        1,
        mode=CatalogMode.ANALYZE,
        min_level=None,
        has_automated_action=False,
        context_note=None,
        attack_techniques=["T1003.006", "T1003", "T1003.006"],
        updated_by="admin01",
        updated_at=T1,
    )
    # Sorted, each once.
    assert rule.attack_techniques == ["T1003", "T1003.006"]
    assert to_catalog_rule(rule).attack_techniques == ["T1003", "T1003.006"]

    # The sync renames the rule and leaves the techniques alone.
    later = T1 + timedelta(days=1)
    await sync_catalog_rules(
        session, [SyncedRule(1, "DCSync, renamed")], synced_by=SYNC, synced_at=later
    )
    renamed = await get_catalog_rule(session, 1)
    assert renamed is not None
    assert (renamed.rule_name, renamed.attack_techniques) == (
        "DCSync, renamed",
        ["T1003", "T1003.006"],
    )

    # An edit replaces every operator field: without techniques, the rule has none.
    cleared = await define_rule(session, 1, CatalogMode.ANALYZE)
    assert cleared.attack_techniques == []


@pytest.mark.parametrize(
    "techniques",
    [["t1003"], ["T1003.6"], ["TA0006"], ["T1003 "], [""], [f"T{1000 + n}" for n in range(21)]],
    ids=["lower case", "short sub-technique", "tactic", "space", "empty", "21 techniques"],
)
async def test_admin_edit_refuses_malformed_techniques(
    session: AsyncSession, techniques: list[str]
) -> None:
    await sync_catalog_rules(session, [SyncedRule(1, "DCSync")], synced_by=SYNC, synced_at=T0)

    with pytest.raises(ValidationError):
        await update_catalog_rule(
            session,
            1,
            mode=CatalogMode.ANALYZE,
            min_level=None,
            has_automated_action=False,
            context_note=None,
            attack_techniques=techniques,
            updated_by="admin01",
            updated_at=T1,
        )
    rule = await get_catalog_rule(session, 1)
    assert rule is not None
    assert (rule.defined, rule.attack_techniques) == (False, [])


async def test_ai_draft_is_used_only_after_acceptance(session: AsyncSession) -> None:
    await sync_catalog_rules(session, [SyncedRule(1, "Rule")], synced_by=SYNC, synced_at=T0)

    drafted = await set_catalog_rule_draft(session, 1, NOTE)
    assert (drafted.ai_draft_note, drafted.context_note) == (NOTE, None)

    accepted = await accept_catalog_rule_draft(session, 1, updated_by="admin01", updated_at=T1)
    assert (accepted.ai_draft_note, accepted.context_note, accepted.updated_by) == (
        None,
        NOTE,
        "admin01",
    )
    with pytest.raises(NotFoundError, match="draft"):
        await accept_catalog_rule_draft(session, 1, updated_by="admin01", updated_at=T1)
    with pytest.raises(ValidationError):
        await set_catalog_rule_draft(session, 1, "x" * 601)


async def test_rule_lookup_and_listing(session: AsyncSession) -> None:
    await sync_catalog_rules(
        session,
        [
            SyncedRule(1, "Excessive Firewall Accepts"),
            SyncedRule(2, "DCSync from a 100% unusual host"),
            SyncedRule(3, "Login_failure burst"),
        ],
        synced_by=SYNC,
        synced_at=T0,
    )
    await define_rule(session, 2, CatalogMode.SKIP)

    def ids(rows: list[CatalogRuleRow]) -> list[int]:
        return [row.rule_id for row in rows]

    assert ids(await get_catalog_rules(session, [3, 1, 404])) == [1, 3]
    assert ids(await list_catalog_rules(session)) == [1, 2, 3]
    assert ids(await list_catalog_rules(session, defined=False)) == [1, 3]
    assert ids(await list_catalog_rules(session, mode=CatalogMode.SKIP)) == [2]
    assert ids(await list_catalog_rules(session, search="firewall")) == [1]
    # LIKE wildcards in the search text are taken literally.
    assert ids(await list_catalog_rules(session, search="100%")) == [2]
    assert ids(await list_catalog_rules(session, search="DCSync%host")) == []
    assert ids(await list_catalog_rules(session, search="Firewall_Accepts")) == []
    assert ids(await list_catalog_rules(session, search="LOGIN_")) == [3]


async def test_log_source_sync_edit_and_listing(session: AsyncSession) -> None:
    sources = [
        SyncedLogSource(112, "FW-DMZ-01", "Cisco ASA"),
        SyncedLogSource(113, "DC-01", "Microsoft Windows Security Event Log"),
    ]
    assert await sync_catalog_log_sources(session, sources, synced_by=SYNC, synced_at=T0) == [
        112,
        113,
    ]
    new = await get_catalog_log_source(session, 112)
    assert new is not None
    assert (new.defined, new.in_scope, new.criticality, new.updated_by) == (
        False,
        True,
        None,
        SYNC,
    )

    edited = await update_catalog_log_source(
        session,
        113,
        description="Domain controller",
        owner="identity team",
        criticality=Level.CRITICAL,
        in_scope=False,
        context_note="Replication traffic from DC-02 is expected.",
        telemetry_classes=None,
        updated_by="admin01",
        updated_at=T1,
    )
    assert (edited.defined, edited.in_scope, edited.owner) == (True, False, "identity team")
    assert to_catalog_log_source(edited) == CatalogLogSource(
        log_source_id=113,
        type_name="Microsoft Windows Security Event Log",
        description="Domain controller",
        criticality=Level.CRITICAL,
        context_note="Replication traffic from DC-02 is expected.",
    )

    later = T1 + timedelta(days=1)
    changed = [
        SyncedLogSource(112, "FW-DMZ-01", "Cisco Firepower"),
        SyncedLogSource(113, "DC-01", "Microsoft Windows Security Event Log"),
    ]
    assert await sync_catalog_log_sources(session, changed, synced_by=SYNC, synced_at=later) == []
    retyped = await get_catalog_log_source(session, 112)
    kept = await get_catalog_log_source(session, 113)
    assert retyped is not None
    assert kept is not None
    assert (retyped.type_name, retyped.updated_at) == ("Cisco Firepower", later)
    assert (kept.updated_by, kept.in_scope, kept.description) == (
        "admin01",
        False,
        "Domain controller",
    )

    assert [row.log_source_id for row in await get_catalog_log_sources(session, [113, 9])] == [113]
    assert [row.log_source_id for row in await list_catalog_log_sources(session)] == [112, 113]
    in_scope = await list_catalog_log_sources(session, in_scope=True)
    assert [row.log_source_id for row in in_scope] == [112]
    defined = await list_catalog_log_sources(session, defined=True)
    assert [row.log_source_id for row in defined] == [113]
    found = await list_catalog_log_sources(session, search="windows security")
    assert [row.log_source_id for row in found] == [113]

    with pytest.raises(ValidationError):
        await update_catalog_log_source(
            session,
            112,
            description="x" * 301,
            owner=None,
            criticality=None,
            in_scope=True,
            context_note=None,
            telemetry_classes=None,
            updated_by="admin01",
            updated_at=T1,
        )
    with pytest.raises(NotFoundError):
        await update_catalog_log_source(
            session,
            404,
            description=None,
            owner=None,
            criticality=None,
            in_scope=True,
            context_note=None,
            telemetry_classes=None,
            updated_by="admin01",
            updated_at=T1,
        )


async def test_an_edit_writes_the_telemetry_classes_as_given(session: AsyncSession) -> None:
    await sync_catalog_log_sources(
        session,
        [SyncedLogSource(112, "MAIL-01", "Brightmail")],
        synced_by=SYNC,
        synced_at=T0,
    )

    async def edit(classes: list[TelemetryClass] | None) -> list[str] | None:
        row = await update_catalog_log_source(
            session,
            112,
            description=None,
            owner=None,
            criticality=None,
            in_scope=True,
            context_note=None,
            telemetry_classes=classes,
            updated_by="admin01",
            updated_at=T1,
        )
        return row.telemetry_classes

    assert await edit([TelemetryClass.VPN, TelemetryClass.EMAIL_SECURITY, TelemetryClass.VPN]) == [
        "email-security",
        "vpn",
    ]
    assert await edit([]) == []
    assert await edit(None) is None


async def test_sync_stores_and_follows_qradar_enabled_state(session: AsyncSession) -> None:
    await sync_catalog_rules(
        session,
        [SyncedRule(1, "Enabled"), SyncedRule(2, "Disabled", qradar_enabled=False)],
        synced_by=SYNC,
        synced_at=T0,
    )
    await define_rule(session, 2, CatalogMode.SKIP)
    added = {row.rule_id: row.qradar_enabled for row in await list_catalog_rules(session)}
    assert added == {1: True, 2: False}

    later = T1 + timedelta(days=1)
    new_ids = await sync_catalog_rules(
        session,
        [SyncedRule(1, "Enabled"), SyncedRule(2, "Disabled", qradar_enabled=True)],
        synced_by=SYNC,
        synced_at=later,
    )

    assert new_ids == []
    enabled = await get_catalog_rule(session, 2)
    unchanged = await get_catalog_rule(session, 1)
    assert enabled is not None
    assert unchanged is not None
    assert (enabled.qradar_enabled, enabled.updated_by, enabled.updated_at) == (True, SYNC, later)
    # The operator's fields stay; the analysis does not look at the QRadar state.
    assert (enabled.defined, enabled.mode, enabled.context_note) == (True, CatalogMode.SKIP, NOTE)
    assert to_catalog_rule(enabled) == CatalogRule(
        rule_id=2, mode=CatalogMode.SKIP, min_level=Level.HIGH, context_note=NOTE
    )
    assert (unchanged.qradar_enabled, unchanged.updated_at) == (True, T0)


async def test_entries_qradar_no_longer_lists_are_marked_and_unmarked(
    session: AsyncSession,
) -> None:
    await sync_catalog_rules(
        session,
        [
            SyncedRule(1, "Rule 1"),
            SyncedRule(2, "Rule 2", qradar_enabled=False),
            SyncedRule(3, "3"),
        ],
        synced_by=SYNC,
        synced_at=T0,
    )
    await define_rule(session, 1, CatalogMode.SKIP)
    later = T1 + timedelta(days=1)

    marked = await set_catalog_rules_missing(
        session, [2, 1, 404, 1], missing=True, synced_by=SYNC, synced_at=later
    )

    assert marked == [1, 2]
    rule = await get_catalog_rule(session, 1)
    listed = await get_catalog_rule(session, 3)
    assert rule is not None
    assert listed is not None
    assert (rule.missing_since, rule.updated_by, rule.updated_at) == (later, SYNC, later)
    assert (rule.defined, rule.mode, rule.min_level, rule.context_note) == (
        True,
        CatalogMode.SKIP,
        Level.HIGH,
        NOTE,
    )
    assert (listed.missing_since, listed.updated_at) == (None, T0)
    # The enrichment still finds a marked rule, and its context carries no new field.
    assert [row.rule_id for row in await get_catalog_rules(session, [1, 2, 3])] == [1, 2, 3]
    assert to_catalog_rule(rule) == CatalogRule(
        rule_id=1, mode=CatalogMode.SKIP, min_level=Level.HIGH, context_note=NOTE
    )

    # A later sync that still misses them keeps the first mark and changes nothing.
    much_later = later + timedelta(days=1)
    again = await set_catalog_rules_missing(
        session, [1, 2], missing=True, synced_by=SYNC, synced_at=much_later
    )
    assert again == []
    rule = await get_catalog_rule(session, 1)
    assert rule is not None
    assert (rule.missing_since, rule.updated_at) == (later, later)

    # Rule 2 is listed again; rule 3 never went missing, so nothing changes for it.
    back = await set_catalog_rules_missing(
        session, [2, 3], missing=False, synced_by=SYNC, synced_at=much_later
    )
    assert back == [2]
    returned = await get_catalog_rule(session, 2)
    listed = await get_catalog_rule(session, 3)
    assert returned is not None
    assert listed is not None
    assert (returned.missing_since, returned.updated_at, returned.qradar_enabled) == (
        None,
        much_later,
        False,
    )
    assert listed.updated_at == T0


async def test_log_sources_qradar_no_longer_lists_are_marked_and_unmarked(
    session: AsyncSession,
) -> None:
    sources = [
        SyncedLogSource(112, "FW-DMZ-01", "Cisco ASA"),
        SyncedLogSource(113, "DC-01", "Microsoft Windows Security Event Log"),
    ]
    await sync_catalog_log_sources(session, sources, synced_by=SYNC, synced_at=T0)

    marked = await set_catalog_log_sources_missing(
        session, [113], missing=True, synced_by=SYNC, synced_at=T1
    )

    assert marked == [113]
    source = await get_catalog_log_source(session, 113)
    assert source is not None
    assert (source.missing_since, source.updated_at) == (T1, T1)
    assert [row.log_source_id for row in await get_catalog_log_sources(session, [112, 113])] == [
        112,
        113,
    ]
    assert to_catalog_log_source(source) == CatalogLogSource(
        log_source_id=113, type_name="Microsoft Windows Security Event Log"
    )
    assert (
        await set_catalog_log_sources_missing(
            session, [113], missing=True, synced_by=SYNC, synced_at=T1 + timedelta(days=1)
        )
        == []
    )

    later = T1 + timedelta(days=2)
    back = await set_catalog_log_sources_missing(
        session, [112, 113], missing=False, synced_by=SYNC, synced_at=later
    )

    assert back == [113]
    source = await get_catalog_log_source(session, 113)
    assert source is not None
    assert (source.missing_since, source.updated_at) == (None, later)


async def test_marks_handle_large_lists(session: AsyncSession) -> None:
    rules = [SyncedRule(rule_id, f"Rule {rule_id}") for rule_id in range(1, 2501)]
    await sync_catalog_rules(session, rules, synced_by=SYNC, synced_at=T0)

    marked = await set_catalog_rules_missing(
        session, range(1, 2501), missing=True, synced_by=SYNC, synced_at=T1
    )
    back = await set_catalog_rules_missing(
        session, range(1001, 2501), missing=False, synced_by=SYNC, synced_at=T1
    )

    assert (marked, back) == (list(range(1, 2501)), list(range(1001, 2501)))
    assert (
        await set_catalog_rules_missing(session, [], missing=True, synced_by=SYNC, synced_at=T1)
        == []
    )
