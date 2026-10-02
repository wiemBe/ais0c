"""The synthetic-data discipline (AGENTS.md hard rule 6): nothing the generator
emits may contain a real address, domain, account or host.

These include the negative tests the rule requires: the scanner must *reject*
forbidden input, and the generator must fail closed when a scenario tries to
smuggle a non-synthetic value onto the wire.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ais0c_harness.loggen import synthetic
from ais0c_harness.loggen.__main__ import build
from ais0c_harness.loggen.scenario import (
    SCENARIOS_DIR,
    EventBlock,
    Scenario,
    ScenarioStep,
    available_scenarios,
    generate,
)
from ais0c_harness.loggen.synthetic import SyntheticDataError
from ais0c_harness.loggen.templates import LogKind

BASE = datetime(2026, 1, 1, tzinfo=UTC)


# --- the scanner accepts synthetic values ------------------------------------


@pytest.mark.parametrize(
    "ip",
    ["192.0.2.5", "198.51.100.200", "203.0.113.1", "10.20.3.4", "192.168.10.9", "127.0.0.1"],
)
def test_documentation_and_internal_ips_pass(ip: str) -> None:
    assert synthetic.ip_is_synthetic(ip)
    synthetic.assert_text_is_synthetic(f"src={ip}", where="test")


@pytest.mark.parametrize("domain", ["bank.example", "host.bank.example", "foo.test", "x.invalid"])
def test_reserved_domains_pass(domain: str) -> None:
    assert synthetic.domain_is_synthetic(domain)
    synthetic.assert_text_is_synthetic(f"Computer={domain}", where="test")


# --- the scanner rejects real values (negative tests) ------------------------


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "203.0.114.1", "172.32.0.1", "11.0.0.1"])
def test_real_ips_are_rejected(ip: str) -> None:
    assert not synthetic.ip_is_synthetic(ip)
    with pytest.raises(SyntheticDataError):
        synthetic.assert_text_is_synthetic(f"remip={ip}", where="test")


@pytest.mark.parametrize(
    "domain", ["google.com", "corp.local", "mail.bank.com.tr", "notexample.com"]
)
def test_real_domains_are_rejected(domain: str) -> None:
    assert not synthetic.domain_is_synthetic(domain)
    with pytest.raises(SyntheticDataError):
        synthetic.assert_text_is_synthetic(f"host={domain}", where="test")


def test_notexample_does_not_pass_as_example() -> None:
    # The reserved-suffix match is anchored on a dot, so "notexample.com" is not
    # accepted just because it ends in "example.com".
    assert not synthetic.domain_is_synthetic("notexample.com")


@pytest.mark.parametrize("account", ["Administrator", "root", "jdoe", "MSOL"])
def test_non_synthetic_accounts_are_rejected(account: str) -> None:
    with pytest.raises(SyntheticDataError):
        synthetic.assert_account_is_synthetic(account, where="test")


@pytest.mark.parametrize("host", ["PROD-DC-01", "mail01", "VPN-GW-99"])
def test_non_synthetic_hosts_are_rejected(host: str) -> None:
    with pytest.raises(SyntheticDataError):
        synthetic.assert_host_is_synthetic(host, where="test")


# --- the generator fails closed ----------------------------------------------


def test_every_rendered_line_of_every_scenario_is_synthetic() -> None:
    # build() scans each line as it renders; scan again here over the whole wire
    # output so the guarantee is asserted end-to-end, not merely trusted.
    for name in available_scenarios():
        _events, lines = build(
            scenario_name=name, seed=3, base_time=BASE, scenarios_dir=SCENARIOS_DIR
        )
        assert lines
        for line in lines:
            synthetic.assert_text_is_synthetic(line, where=name)


def test_generator_rejects_a_scenario_with_a_real_account() -> None:
    # A scenario is data; it must not be able to put a real account on the wire.
    scenario = Scenario(
        name="malicious-input",
        description="",
        steps=(
            ScenarioStep(
                id="x",
                malicious=False,
                technique=None,
                events=(
                    EventBlock(
                        kind=LogKind.WINDOWS_LOGON,
                        count=1,
                        start=0.0,
                        interval=None,
                        spread=None,
                        params={
                            "event_id": "4624",
                            "users": ["Administrator"],
                            "hosts": ["DC-LAB-01"],
                        },
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(SyntheticDataError):
        generate(scenario, seed=1, base_time=BASE)
