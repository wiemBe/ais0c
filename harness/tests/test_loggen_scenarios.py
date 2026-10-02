"""Scenario loading and validation, and the label file (acceptance 3 and 5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import yaml

from ais0c_harness.loggen.scenario import (
    ScenarioError,
    available_scenarios,
    generate,
    labels_jsonl,
    load_scenario,
    parse_scenario,
)
from ais0c_harness.loggen.templates import LogKind

BASE = datetime(2026, 1, 1, tzinfo=UTC)

# Acceptance criterion 5: these three scenarios exist and are defined in YAML.
REQUIRED_SCENARIOS = {"s1-arka-plan", "s2-dcsync", "s3-vpn-yeni-ulke"}


def test_the_three_required_scenarios_are_present() -> None:
    assert REQUIRED_SCENARIOS <= set(available_scenarios())


@pytest.mark.parametrize("name", sorted(REQUIRED_SCENARIOS))
def test_each_required_scenario_loads_and_expands(name: str) -> None:
    scenario = load_scenario(name)
    assert scenario.name == name
    events = generate(scenario, seed=1, base_time=BASE)
    assert events, "scenario expanded to no events"


def test_events_come_out_in_time_order() -> None:
    events = generate(load_scenario("s1-arka-plan"), seed=1, base_time=BASE)
    times = [g.label.time for g in events]
    assert times == sorted(times)


# --- labels (acceptance criterion 3) -----------------------------------------


def test_every_label_has_the_required_fields() -> None:
    events = generate(load_scenario("s2-dcsync"), seed=1, base_time=BASE)
    lines = labels_jsonl(events).splitlines()
    assert len(lines) == len(events)
    for line in lines:
        record = json.loads(line)
        assert set(record) == {
            "event_id",
            "time",
            "scenario",
            "step",
            "kind",
            "technique",
            "malicious",
        }
        assert record["kind"] in {k.value for k in LogKind}
        assert isinstance(record["malicious"], bool)
        datetime.fromisoformat(record["time"])  # parses, and carries a tz offset


def test_event_ids_are_unique() -> None:
    events = generate(load_scenario("s1-arka-plan"), seed=1, base_time=BASE)
    ids = [g.label.event_id for g in events]
    assert len(set(ids)) == len(ids)


def test_dcsync_attack_is_labelled_malicious_with_its_technique() -> None:
    events = generate(load_scenario("s2-dcsync"), seed=1, base_time=BASE)
    attack = [g for g in events if g.label.step == "dcsync-attack"]
    assert attack, "the attack step produced no events"
    assert all(g.label.malicious and g.label.technique == "T1003.006" for g in attack)
    assert all(g.event.kind is LogKind.WINDOWS_DCSYNC for g in attack)


def test_benign_replication_is_not_labelled_malicious() -> None:
    # The MSOL sync account and the DCs' own machine accounts are the benign
    # explanations H2 must not flag; they must never be labelled malicious.
    events = generate(load_scenario("s2-dcsync"), seed=1, base_time=BASE)
    benign = [g for g in events if g.label.step.startswith("benign-")]
    assert benign
    assert not any(g.label.malicious for g in benign)


def test_new_country_vpn_and_follow_on_access_are_malicious() -> None:
    events = generate(load_scenario("s3-vpn-yeni-ulke"), seed=1, base_time=BASE)
    steps = {g.label.step for g in events if g.label.malicious}
    assert steps == {"vpn-new-country", "internal-access-after"}


# --- validation (unknown fields are rejected) --------------------------------


def test_unknown_top_level_key_is_rejected() -> None:
    doc = yaml.safe_load(
        """
        name: x
        oops: true
        steps:
          - id: a
            events:
              - kind: fortigate_vpn
        """
    )
    with pytest.raises(ScenarioError, match="unknown keys"):
        parse_scenario(doc)


def test_unknown_kind_is_rejected() -> None:
    doc = {"name": "x", "steps": [{"id": "a", "events": [{"kind": "cisco_asa"}]}]}
    with pytest.raises(ScenarioError, match="unknown kind"):
        parse_scenario(doc)


def test_interval_and_spread_are_mutually_exclusive() -> None:
    doc = {
        "name": "x",
        "steps": [{"id": "a", "events": [{"kind": "fortigate_vpn", "interval": 1, "spread": 2}]}],
    }
    with pytest.raises(ScenarioError, match="at most one"):
        parse_scenario(doc)


def test_duplicate_step_ids_are_rejected() -> None:
    doc = {
        "name": "x",
        "steps": [
            {"id": "a", "events": [{"kind": "fortigate_vpn"}]},
            {"id": "a", "events": [{"kind": "fortigate_vpn"}]},
        ],
    }
    with pytest.raises(ScenarioError, match="unique"):
        parse_scenario(doc)


def test_missing_scenario_file_lists_whats_available() -> None:
    with pytest.raises(ScenarioError, match="no scenario 'nope'"):
        load_scenario("nope")
