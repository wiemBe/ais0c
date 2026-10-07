"""Triage Gold suite (docs/agent-harness.md §6, T-059): the scenario files and the checks that hold
without a model.

The runner (ais0c_harness.eval) plays each scenario k times against the model and reports the
quality metrics; harness/tests/test_eval_triage_gold.py plays them with a scripted model. These
tests check on every run that the eight scenarios are the ones T-78 names with the expectations
the task gives, that they carry no attack and do not give their own answer away, and that their
data is synthetic.
"""

import ipaddress
import re
from pathlib import Path
from typing import Final

import pytest
import yaml

from ais0c_activities import catalog_floor, floor_level
from ais0c_contracts import CaseVerdict, Level
from ais0c_harness.eval import TriageScenario, load_scenario, load_suite
from ais0c_harness.loggen.synthetic import (
    DOCUMENTATION_NETWORKS,
    SyntheticDataError,
    assert_text_is_synthetic,
)

SUITE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
SCENARIO_FILES = sorted(SUITE_DIR.glob("tg-*.yaml"))
FILE_IDS = [path.stem for path in SCENARIO_FILES]
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

TP_OR_SUSPICIOUS: Final = frozenset({CaseVerdict.TP, CaseVerdict.SUSPICIOUS})
FP_OR_SUSPICIOUS: Final = frozenset({CaseVerdict.FP, CaseVerdict.SUSPICIOUS})
# (allowed verdicts, lowest level, highest level), the table of the task file.
EXPECTED: Final = {
    "tg-01-kerberoasting": (TP_OR_SUSPICIOUS, Level.HIGH, None),
    "tg-02-password-spraying": (TP_OR_SUSPICIOUS, Level.HIGH, None),
    "tg-03-waf-sqli-gecti": (TP_OR_SUSPICIOUS, Level.HIGH, None),
    "tg-04-waf-xss-gecti": (TP_OR_SUSPICIOUS, Level.MEDIUM, None),
    "tg-05-waf-tarama-engellendi": (frozenset({CaseVerdict.TP}), Level.LOW, Level.MEDIUM),
    "tg-06-onayli-tarayici": (frozenset({CaseVerdict.FP}), None, Level.LOW),
    "tg-07-dcsync": (TP_OR_SUSPICIOUS, Level.HIGH, None),
    "tg-08-vpn-yeni-ulke": (frozenset({CaseVerdict.SUSPICIOUS}), None, None),
}
# Words that would hand the model the verdict; the scenario text may only describe the facts.
VERDICT_WORDS = re.compile(
    r"\b(tp|fp|true positive|false positive|benign|harmless|malicious|approved|authorized|"
    r"legitimate|attacker)\b",
    re.IGNORECASE,
)


def load(path: Path) -> TriageScenario:
    scenario = load_scenario(path, root=REPO_ROOT).scenario
    assert isinstance(scenario, TriageScenario)
    return scenario


def texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in texts(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in texts(item)]
    return []


def test_the_suite_is_a_quality_suite_of_the_eight_scenarios() -> None:
    suite = load_suite(SUITE_DIR, root=REPO_ROOT)

    assert (suite.id, suite.kind, suite.agent) == ("triage-gold", "quality", "triage")
    assert suite.definition.scenario_prefix == "tg-"
    assert [item.id for item in suite.scenarios] == list(EXPECTED)
    assert FILE_IDS == list(EXPECTED)


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=FILE_IDS)
def test_the_scenario_expects_what_the_task_gives(path: Path) -> None:
    expect = load(path).expect
    verdicts, lowest, highest = EXPECTED[path.stem]

    assert expect.verdict_in == verdicts
    assert (expect.min_level, expect.max_level) == (lowest, highest)
    assert expect.min_notify_level is None


def test_the_last_scenario_needs_a_data_gap_and_no_other_does() -> None:
    needing = [path.stem for path in SCENARIO_FILES if load(path).expect.data_gap_required]

    assert needing == ["tg-08-vpn-yeni-ulke"]


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=FILE_IDS)
def test_the_scenario_plays_no_attack_and_scripts_the_four_read_tools(path: Path) -> None:
    scenario = load(path)

    assert (scenario.layer, scenario.attack, scenario.marker) == (None, None, None)
    assert scenario.expect.attack_in == []
    assert scenario.input.knowledge == []
    assert set(scenario.input.tool_results) == {
        "get_offense",
        "get_rule",
        "list_assets",
        "list_offenses",
    }
    # No derived tool is written in the scenario (T-67 (3)).
    assert not {
        "list_source_addresses",
        "list_local_destination_addresses",
        "get_log_source",
    } & set(scenario.input.tool_results)
    assert scenario.expect.cited_tools <= set(scenario.input.tool_results)


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=FILE_IDS)
def test_the_scenario_text_does_not_give_the_answer_away(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.pop("expect")

    for text in texts(data):
        assert not VERDICT_WORDS.search(text), text


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=FILE_IDS)
def test_the_scenario_uses_documentation_addresses_only(path: Path) -> None:
    tokens = IPV4.findall(path.read_text(encoding="utf-8"))

    assert tokens
    for token in tokens:
        address = ipaddress.ip_address(token)
        assert any(address in network for network in DOCUMENTATION_NETWORKS), token


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=FILE_IDS)
def test_the_scenario_uses_reserved_domains_only(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))  # the data, not the comments

    for text in texts(data):
        assert_text_is_synthetic(text, where=path.name)


def test_the_synthetic_check_rejects_a_real_looking_name() -> None:
    with pytest.raises(SyntheticDataError):
        assert_text_is_synthetic("host web01.corp.local", where="scenario")


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=FILE_IDS)
def test_the_floor_level_is_what_the_enrichment_computes(path: Path) -> None:
    scenario = load(path)
    enrichment = scenario.input.enrichment

    computed = floor_level(
        catalog=catalog_floor(enrichment.catalog.rules),
        asset_hits=enrichment.critical_asset_hits,
        ioc_hits=enrichment.ioc_hits,
    )

    assert computed == enrichment.floor_level == scenario.expect.floor_level


def test_the_scanner_is_registered_as_an_internal_scanner_in_its_scenario() -> None:
    scenario = load(SUITE_DIR / "tg-06-onayli-tarayici.yaml")
    assets = scenario.input.tool_results["list_assets"][0].data
    source = scenario.input.offense.source_ips[0]

    values = {
        prop["value"]
        for asset in assets
        for prop in asset["properties"]  # type: ignore[index, union-attr]
    }
    addresses = {
        ip["value"]
        for asset in assets
        for interface in asset["interfaces"]  # type: ignore[index, union-attr]
        for ip in interface["ip_addresses"]
    }
    assert source in addresses
    assert "internal vulnerability scanner" in values
    # T-88: the scanner's schedule is an organization fact in the rule's catalog note; the asset
    # record carries only the role and claims no authority.
    [rule] = scenario.input.enrichment.catalog.rules
    assert rule.context_note is not None
    assert source in rule.context_note
    assert "CHG-48213" in rule.context_note
    assert not {"Owner", "Maintenance Window"} & {
        prop["name"]
        for asset in assets
        for prop in asset["properties"]  # type: ignore[index, union-attr]
    }
    assert scenario.expect.cited_tools == set()


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=FILE_IDS)
def test_the_distinguishing_fields_are_in_the_offense(path: Path) -> None:
    scenario = load(path)
    description = scenario.input.offense.description

    if "waf" in path.stem:
        assert "request_status" in description
        assert "attack_type" in description
    if "kerberoasting" in path.stem:
        assert "TicketEncryptionType" in description
    if "spraying" in path.stem:
        assert "4625" in description
        assert "4624" in description
