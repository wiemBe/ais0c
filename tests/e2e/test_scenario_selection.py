"""T-058 criterion 5: the chain test selects its lab scenario from `AIS0C_E2E_SCENARIO` and the
seed. No lab and no stack is needed; only the settings logic runs."""

import pytest
from e2e_support import (
    DEFAULT_SCENARIO,
    SCENARIO_SPECS,
    E2ESetupError,
    LabSettings,
)

LAB = {
    "QRADAR_LAB_URL": "qradar.example.com",
    "QRADAR_LAB_TOKEN": "token",
    "AIS0C_E2E_QRADAR_MCP": "/bin/qradar-mcp-fork",
    "AIS0C_DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1/db",
    "LITELLM_API_KEY": "key",
}

# scenario -> (rule name, offense key with the default seed 9)
KEYS = {
    "s2-dcsync": ("AIS0C LAB - DCSync by a non-machine account", "bkupadmin"),
    "s4-kerberoasting": ("AIS0C LAB - Kerberoasting RC4 service tickets", "branch.user05"),
    "s5-password-spraying": ("AIS0C LAB - Password spraying from one source", "10.50.7.23"),
    "s6-waf-sqli-gecti": ("AIS0C LAB - WAF SQL injection not blocked", "198.51.100.23"),
    "s7-waf-xss-gecti": ("AIS0C LAB - WAF cross-site scripting not blocked", "203.0.113.61"),
    "s8-waf-tarama-engellendi": (
        "AIS0C LAB - WAF signature volume from an external source",
        "192.0.2.88",
    ),
    "s9-onayli-tarayici": (
        "AIS0C LAB - WAF signature volume from an internal source",
        "10.30.5.10",
    ),
}


def test_the_default_scenario_is_dcsync_with_the_old_seed_and_account() -> None:
    settings = LabSettings.from_env(LAB)
    assert (settings.scenario, settings.seed) == (DEFAULT_SCENARIO, "9")
    assert DEFAULT_SCENARIO == "s2-dcsync"
    assert settings.rule_name == KEYS["s2-dcsync"][0]
    assert settings.offense_key == "bkupadmin"


@pytest.mark.parametrize(
    ("seed", "account"), [("9", "bkupadmin"), ("12", "svc_backup"), ("75", "svc_sql")]
)
def test_dcsync_seeds_still_give_the_known_accounts(seed: str, account: str) -> None:
    settings = LabSettings.from_env({**LAB, "AIS0C_E2E_SEED": seed})
    assert settings.offense_key == account


@pytest.mark.parametrize("scenario", sorted(KEYS))
def test_each_scenario_selects_its_rule_and_offense_key(scenario: str) -> None:
    settings = LabSettings.from_env({**LAB, "AIS0C_E2E_SCENARIO": scenario})
    rule_name, key = KEYS[scenario]
    assert settings.spec is SCENARIO_SPECS[scenario]
    assert (settings.rule_name, settings.offense_key) == (rule_name, key)


def test_the_selectable_scenarios_are_the_ones_with_a_lab_rule() -> None:
    assert set(SCENARIO_SPECS) == set(KEYS)


def test_the_report_puts_expected_and_chain_decisions_side_by_side() -> None:
    comparison = SCENARIO_SPECS["s9-onayli-tarayici"].comparison("fp", "low")
    assert comparison["expected_verdict"] == "fp"
    assert comparison["chain_verdict"] == "fp"
    assert {"scenario", "expected_level", "chain_level", "rationale"} <= set(comparison)
    # A disagreement is a measurement, not an error.
    miss = SCENARIO_SPECS["s6-waf-sqli-gecti"].comparison("fp", "low")
    assert (miss["expected_verdict"], miss["chain_verdict"]) == ("tp", "fp")


@pytest.mark.parametrize("scenario", ["s3-vpn-yeni-ulke", "s1-arka-plan", "nope", "../s2-dcsync"])
def test_a_scenario_without_a_lab_rule_is_refused(scenario: str) -> None:
    with pytest.raises(E2ESetupError, match="AIS0C_E2E_SCENARIO"):
        LabSettings.from_env({**LAB, "AIS0C_E2E_SCENARIO": scenario})


def test_a_dcsync_seed_that_would_open_two_offenses_is_refused() -> None:
    # Seed 1 gives the three DCSync events different accounts.
    with pytest.raises(E2ESetupError, match="offense keys"):
        LabSettings.from_env({**LAB, "AIS0C_E2E_SEED": "1"})


def test_a_seed_that_is_not_a_number_is_refused() -> None:
    with pytest.raises(E2ESetupError, match="number"):
        LabSettings.from_env({**LAB, "AIS0C_E2E_SEED": "nine"})
