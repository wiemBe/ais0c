"""Scenarios from recorded runs (T-053 criterion 5): the anonymizer, the draft file and the
repository's scenarios, which hold no address outside the documentation ranges."""

import io
import ipaddress
import re
from pathlib import Path

import yaml

from ais0c_harness.eval.cli import Dependencies, main
from ais0c_harness.eval.from_records import (
    TODO_PREFIX,
    anonymize,
    anonymize_text,
    render,
)

from .eval_helpers import REPO_ROOT, SUITES

DOCUMENTATION_V4 = [
    ipaddress.ip_network(net) for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
]
DOCUMENTATION_V6 = ipaddress.ip_network("2001:db8::/32")
V4 = re.compile(r"(?<![\w.])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\w.])")
V6 = re.compile(r"(?<![\w:.])(?=[0-9A-Fa-f]*:[0-9A-Fa-f]*:)[0-9A-Fa-f:]{3,}(?![\w:.])")


def addresses_outside_documentation(text: str) -> list[str]:
    found: list[str] = []
    for token in V4.findall(text):
        try:
            address = ipaddress.ip_address(token)
        except ValueError:
            continue
        if not any(address in net for net in DOCUMENTATION_V4):
            found.append(token)
    for token in V6.findall(text):
        try:
            address = ipaddress.ip_address(token)
        except ValueError:
            continue
        if address not in DOCUMENTATION_V6:
            found.append(token)
    return found


def test_the_repository_scenarios_hold_no_address_outside_the_documentation_ranges() -> None:
    files = sorted(SUITES.glob("*/*.yaml"))

    assert len(files) > 10
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert addresses_outside_documentation(text) == [], path.name


def test_the_scan_finds_a_real_looking_address() -> None:
    assert addresses_outside_documentation("from 10.1.2.3 and 8.8.8.8") == ["10.1.2.3", "8.8.8.8"]
    assert addresses_outside_documentation("from 2001:4860::1 not 2001:db8::7") == ["2001:4860::1"]
    assert addresses_outside_documentation("from 198.51.100.7, 20:14:18 and 1.2.3") == []


def test_an_address_outside_the_documentation_ranges_is_mapped_into_them() -> None:
    mapping: dict[str, str] = {}

    text = anonymize_text("10.20.30.40 talked to 192.168.1.5 and 198.51.100.9", mapping)

    assert addresses_outside_documentation(text) == []
    assert text.endswith("198.51.100.9")
    assert "10.20.30.40" not in text
    assert "192.168.1.5" not in text


def test_the_same_address_always_maps_to_the_same_one() -> None:
    mapping: dict[str, str] = {}

    first = anonymize_text("10.20.30.40", mapping)
    second = anonymize_text("seen 10.20.30.40 again, then 10.20.30.41", mapping)
    third = anonymize_text("10.20.30.40", {})

    assert second.split()[1] == first
    assert third == first
    assert len(set(mapping.values())) == 2


def test_an_ipv6_address_maps_into_the_documentation_range() -> None:
    text = anonymize_text("peer fd00:1234:5678::9 and fe80::1", {})

    assert addresses_outside_documentation(text) == []
    assert text.count("2001:db8::") == 2


def test_a_lab_domain_maps_to_example_com_and_a_clock_time_stays() -> None:
    text = anonymize_text("dc01.bank.example at 20:14:18 on host.lab.example", {})

    assert text == "dc01.example.com at 20:14:18 on host.example.com"


def test_a_timestamp_and_a_year_stay_as_they_are() -> None:
    text = "retrieved 2026-10-07T10:54:20.497913+00:00 and 2026-10-06 20:14:18, offense 35"

    assert anonymize_text(text, {}) == text


def test_anonymize_reaches_every_string_at_any_depth() -> None:
    value = {"a": ["x 10.0.0.1"], "b": {"c": "10.0.0.2", "n": 7}, "t": True}

    result = anonymize(value, {})

    assert addresses_outside_documentation(str(result)) == []
    assert isinstance(result, dict)
    assert result["t"] is True
    assert result["b"] == {"c": result["b"]["c"], "n": 7}  # type: ignore[index]


def test_a_draft_names_its_provenance_and_its_todo_notes() -> None:
    mapping = {"10.0.0.1": "198.51.100.1"}

    text = render(
        {"id": "x", "input": {"source": "10.0.0.1"}},
        run_id="case-35-reporting-1",
        mapping=mapping,
        notes=["offense start: guessed"],
    )

    assert "case-35-reporting-1" in text
    assert "#   10.0.0.1 -> 198.51.100.1" in text
    assert f"# {TODO_PREFIX}offense start: guessed" in text
    body = yaml.safe_load(text)
    assert body["input"]["source"] == "198.51.100.1"
    assert addresses_outside_documentation(text.split("\n\n", 1)[1]) == []


def test_the_scenario_command_needs_a_database(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()

    code = main(
        [
            "--root",
            str(REPO_ROOT),
            "scenario",
            "--run",
            "case-35-reporting-1",
            "--suite",
            "reporting-gold",
            "--id",
            "rep-09-x",
            "--out",
            str(tmp_path / "rep-09-x.yaml"),
        ],
        environ={},
        deps=Dependencies(retry_delay_seconds=0),
        stdout=out,
        stderr=err,
    )

    assert code == 2
    assert out.getvalue() == ""
    assert not (tmp_path / "rep-09-x.yaml").exists()
    assert err.getvalue().startswith("error:")
