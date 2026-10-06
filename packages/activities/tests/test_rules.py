"""Pure rules of the intake and case activities: catalog filter, floor, critical assets,
pre-priority, levels and the SLA deadline."""

from datetime import timedelta

import pytest
from activity_payloads import T0, offense

from ais0c_activities import (
    CaseSettings,
    catalog_floor,
    catalog_mode,
    floor_level,
    match_critical_assets,
    pre_priority,
    sla_deadline,
)
from ais0c_activities.levels import at_least, level_rank, max_level
from ais0c_contracts import (
    SHORT_TEXT_MAX_LENGTH,
    CatalogMode,
    CatalogRule,
    Confidence,
    CriticalAssetHit,
    IocHit,
    Level,
)
from ais0c_storage.enums import CriticalAssetKind
from ais0c_storage.models import CriticalAssetRow

BASE = "https://ais0c.example.com/cases"

SKIP = CatalogMode.SKIP
ANALYZE = CatalogMode.ANALYZE


def rule(rule_id: int, mode: CatalogMode = ANALYZE, min_level: Level | None = None) -> CatalogRule:
    return CatalogRule(rule_id=rule_id, mode=mode, min_level=min_level)


def asset(
    kind: CriticalAssetKind, value: str, label: str, level: Level = Level.HIGH
) -> CriticalAssetRow:
    return CriticalAssetRow(kind=kind, value=value, label=label, level=level)


IOC = IocHit(value="203.0.113.7", type="ip", source="usta", confidence=Confidence.HIGH)


# --- Analysis Catalog --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule_ids", "rules", "expected"),
    [
        ([1], [rule(1, SKIP)], SKIP),
        ([1, 2], [rule(1, SKIP), rule(2, SKIP)], SKIP),
        ([1, 2], [rule(1, SKIP), rule(2, ANALYZE)], ANALYZE),
        ([1, 2], [rule(1, SKIP)], ANALYZE),  # rule 2 is not in the catalog: analyzed
        ([1], [], ANALYZE),
        ([], [], ANALYZE),
    ],
    ids=["skip", "all skip", "one analyzed", "one undefined", "undefined", "no rules"],
)
def test_an_offense_is_skipped_only_when_every_rule_is_skip(
    rule_ids: list[int], rules: list[CatalogRule], expected: CatalogMode
) -> None:
    assert catalog_mode(rule_ids, rules) is expected


def test_the_catalog_floor_is_the_highest_min_level() -> None:
    rules = [rule(1, min_level=Level.MEDIUM), rule(2, min_level=Level.HIGH), rule(3)]
    assert catalog_floor(rules) is Level.HIGH
    assert catalog_floor([rule(3)]) is None


# --- Critical assets ---------------------------------------------------------------------------


def test_critical_assets_match_addresses_networks_users_and_hosts() -> None:
    snapshot = offense(
        1,
        offense_source="SWIFT-GW01",
        source_ips=["203.0.113.7", "not-an-ip"],
        destination_ips=["198.51.100.15", "2001:db8::15"],
        usernames=["Alice.Admin"],
    )
    assets = [
        asset(CriticalAssetKind.IP, "198.51.100.15", "SWIFT", Level.CRITICAL),
        asset(CriticalAssetKind.CIDR, "2001:db8::/64", "Core banking"),
        asset(CriticalAssetKind.USER, "alice.admin", "Domain admin"),
        asset(CriticalAssetKind.HOST, "swift-gw01", "SWIFT gateway", Level.CRITICAL),
        asset(CriticalAssetKind.IP, "192.0.2.1", "Unrelated"),
        asset(CriticalAssetKind.CIDR, "192.0.2.0/24", "Unrelated network"),
    ]

    hits = match_critical_assets(snapshot, assets)

    assert hits == [
        CriticalAssetHit(value="198.51.100.15", label="SWIFT", level=Level.CRITICAL),
        CriticalAssetHit(value="2001:db8::15", label="Core banking", level=Level.HIGH),
        CriticalAssetHit(value="Alice.Admin", label="Domain admin", level=Level.HIGH),
        CriticalAssetHit(value="SWIFT-GW01", label="SWIFT gateway", level=Level.CRITICAL),
    ]


def test_an_address_offense_source_is_matched_too() -> None:
    snapshot = offense(1, offense_source="192.0.2.10", source_ips=[], destination_ips=[])
    hits = match_critical_assets(snapshot, [asset(CriticalAssetKind.CIDR, "192.0.2.0/24", "DC")])
    assert [hit.value for hit in hits] == ["192.0.2.10"]


def test_a_host_entry_does_not_match_user_names() -> None:
    snapshot = offense(1, usernames=["dc01"])
    assert match_critical_assets(snapshot, [asset(CriticalAssetKind.HOST, "dc01", "DC")]) == []


def test_the_same_value_and_label_is_reported_once_at_its_highest_level() -> None:
    assets = [
        asset(CriticalAssetKind.IP, "198.51.100.15", "SWIFT", Level.HIGH),
        asset(CriticalAssetKind.CIDR, "198.51.100.0/24", "SWIFT", Level.CRITICAL),
    ]
    hits = match_critical_assets(offense(1), assets)
    assert hits == [CriticalAssetHit(value="198.51.100.15", label="SWIFT", level=Level.CRITICAL)]


def test_a_malformed_stored_asset_is_skipped() -> None:
    assets = [
        asset(CriticalAssetKind.CIDR, "198.51.100.0/33", "Broken"),
        asset(CriticalAssetKind.IP, "198.51.100.15", "SWIFT"),
    ]
    assert [hit.label for hit in match_critical_assets(offense(1), assets)] == ["SWIFT"]


def test_a_label_stored_before_the_limit_is_cut_and_the_hit_still_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`CriticalAssetHit.label` is `ShortText` (contracts v0.4). Storage refuses a longer label
    now; one stored before is cut, so the hit and the floor it raises are not lost."""
    assets = [asset(CriticalAssetKind.IP, "198.51.100.15", "Ş" * 400, Level.CRITICAL)]

    hits = match_critical_assets(offense(1), assets)

    assert hits == [
        CriticalAssetHit(
            value="198.51.100.15", label="Ş" * SHORT_TEXT_MAX_LENGTH, level=Level.CRITICAL
        )
    ]
    assert "has a label longer than 300" in caplog.text
    assert "Ş" not in caplog.text


# --- Floor -------------------------------------------------------------------------------------


def test_the_floor_is_the_highest_of_catalog_asset_and_ioc_floors() -> None:
    critical_hit = CriticalAssetHit(value="198.51.100.15", label="SWIFT", level=Level.CRITICAL)
    assert floor_level(catalog=None, asset_hits=[], ioc_hits=[]) is None
    assert floor_level(catalog=Level.LOW, asset_hits=[], ioc_hits=[]) is Level.LOW
    assert floor_level(catalog=Level.LOW, asset_hits=[], ioc_hits=[IOC]) is Level.HIGH
    assert floor_level(catalog=Level.HIGH, asset_hits=[critical_hit], ioc_hits=[]) is Level.CRITICAL
    assert floor_level(catalog=Level.CRITICAL, asset_hits=[], ioc_hits=[IOC]) is Level.CRITICAL


def test_levels_are_ordered_by_severity_not_by_name() -> None:
    assert [level_rank(level) for level in (None, *Level)] == [0, 1, 2, 3, 4]
    assert max_level(Level.MEDIUM, None, Level.CRITICAL, Level.HIGH) is Level.CRITICAL
    assert max_level(None, None) is None
    assert at_least(Level.LOW, Level.HIGH) is Level.HIGH
    assert at_least(Level.CRITICAL, Level.HIGH) is Level.CRITICAL
    assert at_least(Level.MEDIUM, None) is Level.MEDIUM


# --- Pre-priority ------------------------------------------------------------------------------


def test_pre_priority_puts_high_catalog_floors_first_then_asset_or_ioc_hits() -> None:
    def priority(floor: Level | None, asset_hit: bool = False, ioc_hit: bool = False) -> int:
        return pre_priority(catalog_floor=floor, critical_asset_hit=asset_hit, ioc_hit=ioc_hit)

    expected_order = [
        priority(Level.CRITICAL, asset_hit=True),
        priority(Level.CRITICAL),
        priority(Level.HIGH, ioc_hit=True),
        priority(Level.HIGH),
        priority(Level.MEDIUM, asset_hit=True),
        priority(None, ioc_hit=True),
        priority(Level.MEDIUM),
        priority(Level.LOW),
        priority(None),
    ]

    assert expected_order == sorted(expected_order, reverse=True)
    assert len(set(expected_order)) == len(expected_order)
    assert priority(None, asset_hit=True) == priority(None, ioc_hit=True)


# --- Settings and SLA --------------------------------------------------------------------------


def test_settings_default_to_the_architecture_values() -> None:
    settings = CaseSettings.from_env({"AIS0C_CASE_URL_BASE": BASE})

    assert settings == CaseSettings(case_url_base=BASE)
    assert settings.case_url("case-101") == f"{BASE}/case-101"
    assert settings.group_full_analyses_per_hour == 5
    assert (settings.sla_high, settings.sla_low) == (timedelta(minutes=10), timedelta(minutes=60))
    assert settings.max_concurrent_cases == 10
    # D-31 and T-014.
    assert settings.reevaluation_interval == timedelta(minutes=30)
    assert settings.agent_retry_delay == timedelta(minutes=5)


def test_settings_come_from_the_environment() -> None:
    settings = CaseSettings.from_env(
        {
            "AIS0C_CASE_URL_BASE": "https://soc.example.com/cases",
            "AIS0C_MAX_CONCURRENT_CASES": "3",
            "AIS0C_GROUP_FULL_ANALYSES_PER_HOUR": " 8 ",
            "AIS0C_SLA_HIGH_MINUTES": "5",
            "AIS0C_SLA_LOW_MINUTES": "30",
            "AIS0C_REEVALUATION_MINUTES": "45",
            "AIS0C_AGENT_RETRY_MINUTES": "2",
        }
    )

    assert settings == CaseSettings(
        case_url_base="https://soc.example.com/cases",
        max_concurrent_cases=3,
        group_full_analyses_per_hour=8,
        sla_high=timedelta(minutes=5),
        sla_low=timedelta(minutes=30),
        reevaluation_interval=timedelta(minutes=45),
        agent_retry_delay=timedelta(minutes=2),
    )


@pytest.mark.parametrize("value", ["0", "-2", "ten", "1.5"])
@pytest.mark.parametrize(
    "name",
    [
        "AIS0C_MAX_CONCURRENT_CASES",
        "AIS0C_GROUP_FULL_ANALYSES_PER_HOUR",
        "AIS0C_SLA_HIGH_MINUTES",
        "AIS0C_SLA_LOW_MINUTES",
        "AIS0C_REEVALUATION_MINUTES",
        "AIS0C_AGENT_RETRY_MINUTES",
    ],
)
def test_invalid_settings_are_rejected(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        CaseSettings.from_env({"AIS0C_CASE_URL_BASE": BASE, name: value})


def test_settings_reject_impossible_values() -> None:
    with pytest.raises(ValueError, match="max_concurrent_cases"):
        CaseSettings(case_url_base=BASE, max_concurrent_cases=0)
    with pytest.raises(ValueError, match="SLA"):
        CaseSettings(case_url_base=BASE, sla_high=timedelta(0))
    with pytest.raises(ValueError, match="re-evaluation interval"):
        CaseSettings(case_url_base=BASE, reevaluation_interval=timedelta(0))
    with pytest.raises(ValueError, match="retry delay"):
        CaseSettings(case_url_base=BASE, agent_retry_delay=timedelta(seconds=-1))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
        "ftp://ais0c.example.com/cases",
        "https://ais0c.example.com/cases/",
        "https://ais0c.example.com/cases?view=1",
        "https://ais0c.example.com/cases#top",
        "https://ais0c.example.com/" + "c" * 200,
        "x" * 200,
    ],
)
def test_an_unusable_case_url_base_is_rejected(value: str) -> None:
    """T-045 criterion 6: without a usable base the worker does not start."""
    with pytest.raises(ValueError, match=r"AIS0C_CASE_URL_BASE|case_url_base"):
        CaseSettings.from_env({"AIS0C_CASE_URL_BASE": value})


@pytest.mark.parametrize(
    ("level", "sla"),
    [
        (Level.CRITICAL, timedelta(minutes=10)),
        (Level.HIGH, timedelta(minutes=10)),
        (Level.MEDIUM, timedelta(minutes=60)),
        (Level.LOW, timedelta(minutes=60)),
        (None, timedelta(minutes=60)),
    ],
)
def test_the_sla_depends_on_the_level(level: Level | None, sla: timedelta) -> None:
    assert CaseSettings(case_url_base=BASE).sla_for(level) == sla


def test_the_sla_runs_from_creation_then_from_each_update() -> None:
    snapshot = offense(1, start=T0, updated=T0 + timedelta(hours=3))
    settings = CaseSettings(case_url_base=BASE)

    first = sla_deadline(snapshot, evaluation_no=1, level=Level.HIGH, settings=settings)
    second = sla_deadline(snapshot, evaluation_no=2, level=None, settings=settings)

    assert first == T0 + timedelta(minutes=10)
    assert second == T0 + timedelta(hours=3, minutes=60)
