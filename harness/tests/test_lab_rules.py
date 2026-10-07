"""T-058 criterion 3 and T-061: the lab rules' sources and the extension zip built from them."""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import sys
import uuid
import zipfile
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree as ET

import pytest
import yaml

LAB_DIR = Path(__file__).resolve().parents[1] / "lab" / "qradar"
REFERENCE = Path(__file__).resolve().parent / "fixtures" / "lab_rules_reference.xml"


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "lab_build_extension", LAB_DIR / "build_extension.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_builder()

# scenario -> (rule name, what the offense is indexed by)
EXPECTED = {
    "s2-dcsync": ("AIS0C LAB - DCSync by a non-machine account", "username"),
    "s4-kerberoasting": ("AIS0C LAB - Kerberoasting RC4 service tickets", "username"),
    "s5-password-spraying": ("AIS0C LAB - Password spraying from one source", "source_ip"),
    "s6-waf-sqli-gecti": ("AIS0C LAB - WAF SQL injection not blocked", "source_ip"),
    "s7-waf-xss-gecti": ("AIS0C LAB - WAF cross-site scripting not blocked", "source_ip"),
    "s8-waf-tarama-engellendi": (
        "AIS0C LAB - WAF signature volume from an external source",
        "source_ip",
    ),
    "s9-onayli-tarayici": (
        "AIS0C LAB - WAF signature volume from an internal source",
        "source_ip",
    ),
}
DCSYNC_UUID = "6b5d5ad0-dd8c-4ff9-b1df-68df3dde66bb"
TEST_CLASSES = {
    "DeviceTypeID_Test",
    "QID_Test",
    "EventPayload_Test",
    "Regex_Test",
    "SrcHost_Test",
    "functions.MatchCount",
}


def rules_by_scenario() -> dict[str, object]:
    return {rule.scenario: rule for rule in builder.load_rules()}


def build(tmp_path: Path) -> Path:
    return builder.build_zip(tmp_path / "rules.zip", builder.load_rules())


def exported_rules(zip_path: Path) -> dict[str, tuple[ET.Element, ET.Element]]:
    """uuid -> (``<custom_rule>``, the decoded ``<rule>``)."""
    with zipfile.ZipFile(zip_path) as archive:
        root = ET.fromstring(archive.read("ais0c-lab-rules.xml"))  # noqa: S314 - built above
    result = {}
    for custom in root.findall("custom_rule"):
        data = base64.b64decode(custom.findtext("rule_data", ""))
        result[custom.findtext("uuid", "")] = (custom, ET.fromstring(data))  # noqa: S314
    return result


def reference_rules() -> dict[str, ET.Element]:
    """The installed lab rules, by uuid (the reference is QRadar's own export, decoded)."""
    text = REFERENCE.read_text(encoding="utf-8")
    found = re.findall(r"<!-- id \d+ uuid (\S+) -->\n(<rule .*?</rule>)", text, re.S)
    return {rule_uuid: ET.fromstring(xml) for rule_uuid, xml in found}  # noqa: S314


def shape(rule: ET.Element) -> dict[str, object]:
    """What the rule engine reads: tests in order, negation, every parameter's selection."""
    tests = [
        (
            test.get("name", "").removeprefix("com.q1labs.semsources.cre.tests."),
            test.get("negate") == "true",
            [p.findtext("userSelection") for p in test.findall("parameter")],
        )
        for test in rule.iter("test")
    ]
    actions = rule.find("actions")
    assert actions is not None
    return {
        "tests": tests,
        "offenseMapping": actions.get("offenseMapping"),
        "forceOffenseCreation": actions.get("forceOffenseCreation"),
    }


# --- sources ------------------------------------------------------------------------------


def test_every_scenario_with_an_offense_has_one_rule_named_and_indexed_as_expected() -> None:
    rules = rules_by_scenario()
    assert set(rules) == set(EXPECTED)
    for scenario, (name, index_by) in EXPECTED.items():
        rule = rules[scenario]
        assert (rule.name, rule.index_by) == (name, index_by)  # type: ignore[attr-defined]
        assert rule.name.startswith("AIS0C LAB - ")  # type: ignore[attr-defined]


def test_the_dcsync_rule_keeps_the_conditions_of_pr_t_012() -> None:
    rule = rules_by_scenario()["s2-dcsync"]
    conditions = [(c["test"], c["value"]) for c in rule.conditions]  # type: ignore[attr-defined]
    assert conditions == [
        ("log_source_type", "Microsoft Windows Security Event Log"),
        ("qid", 5000849),
        ("payload_contains", "DS-Replication-Get-Changes"),
        ("username_not_matches", ["\\$$", "^MSOL_"]),
    ]


def test_rules_match_the_log_source_and_text_the_scenarios_emit() -> None:
    rules = rules_by_scenario()
    waf = ("log_source_type", "F5 Networks BIG-IP ASM")
    for scenario in ("s6-waf-sqli-gecti", "s7-waf-xss-gecti", "s8-waf-tarama-engellendi"):
        first = rules[scenario].conditions[0]  # type: ignore[attr-defined]
        assert (first["test"], first["value"]) == waf
    texts = {
        scenario: [c.get("value") for c in rule.conditions]  # type: ignore[attr-defined]
        for scenario, rule in rules.items()
    }
    assert 'attack_type="SQL-Injection"' in texts["s6-waf-sqli-gecti"]
    assert 'request_status="alerted"' in texts["s6-waf-sqli-gecti"]
    assert 'attack_type="Cross Site Scripting (XSS)"' in texts["s7-waf-xss-gecti"]
    assert 5000938 in texts["s4-kerberoasting"]  # 4769 as the lab's Windows DSM maps it
    assert "Ticket Encryption Type: 0x17" in texts["s4-kerberoasting"]


def threshold(scenario: str) -> dict[str, object]:
    rule = rules_by_scenario()[scenario]
    (found,) = [c for c in rule.conditions if c["test"] == "threshold"]  # type: ignore[attr-defined]
    return found


def test_kerberoasting_counts_five_rc4_tickets_of_one_user_in_two_minutes() -> None:
    counter = threshold("s4-kerberoasting")
    assert counter["events"] == 5
    assert counter["window_minutes"] == 2
    assert counter["same"] == "username"
    assert "distinct" not in counter
    assert "distinct_count" not in counter


def test_spraying_counts_five_different_usernames_from_one_source_in_five_minutes() -> None:
    counter = threshold("s5-password-spraying")
    assert counter["distinct"] == "username"
    assert counter["distinct_count"] == 5
    assert counter["same"] == "source_ip"
    assert counter["window_minutes"] == 5
    assert "events" not in counter


# --- the zip's shape ----------------------------------------------------------------------


def test_the_zip_holds_the_export_xml_and_the_manifest(tmp_path: Path) -> None:
    out = build(tmp_path)
    with zipfile.ZipFile(out) as archive:
        assert archive.namelist() == ["ais0c-lab-rules.xml", "manifest.txt"]
        manifest = json.loads(archive.read("manifest.txt"))
        root = ET.fromstring(archive.read("ais0c-lab-rules.xml"))  # noqa: S314 - built above
    extension = manifest["doc"]["extension_manifest"]
    assert manifest["_id"] == "ais0c-lab-rules"
    assert extension["version"]
    assert extension["locale"]["en-US"]["extension.name"] == "AIS0C LAB rules"
    assert root.tag == "content"
    customs = root.findall("custom_rule")
    assert len(customs) == len(EXPECTED)
    for custom in customs:
        assert custom.findtext("origin") == "USER"
        assert custom.findtext("rule_type") == "0"
        for tag in ("rule_data", "uuid", "id", "mod_date", "create_date"):
            assert custom.findtext(tag)


def test_rule_data_is_the_base64_of_a_rule_xml(tmp_path: Path) -> None:
    rules = exported_rules(build(tmp_path))
    names = sorted(str(rule.findtext("name")) for _, rule in rules.values())
    assert names == sorted(name for name, _ in EXPECTED.values())
    for custom, rule in rules.values():
        assert rule.tag == "rule"
        assert rule.get("type") == "EVENT"
        assert rule.get("id") == custom.findtext("id")
        assert rule.get("overrideid") == rule.get("id")
        assert rule.get("enabled") == "true"


def test_the_build_is_deterministic(tmp_path: Path) -> None:
    first = builder.build_zip(tmp_path / "a.zip", builder.load_rules())
    second = builder.build_zip(tmp_path / "sub" / "b.zip", builder.load_rules())
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert {info.date_time for info in archive.infolist()} == {builder.ZIP_TIMESTAMP}


def test_the_cli_writes_the_zip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "rules.zip"
    assert builder.main(["--out", str(out)]) == 0
    assert out.is_file()
    assert "7 rules" in capsys.readouterr().out


# --- the rule XML against the installed rules (criterion 2) ---------------------------------


def test_the_reference_holds_the_seven_installed_rules() -> None:
    assert len(reference_rules()) == 7


def test_each_rule_xml_matches_the_installed_rule(tmp_path: Path) -> None:
    built = exported_rules(build(tmp_path))
    reference = reference_rules()
    assert set(built) == set(reference)
    for rule_uuid, installed in reference.items():
        _, ours = built[rule_uuid]
        assert ours.findtext("name") == installed.findtext("name")
        assert shape(ours) == shape(installed), installed.findtext("name")


def test_only_the_expected_test_classes_are_used(tmp_path: Path) -> None:
    classes = {
        test.get("name", "").removeprefix("com.q1labs.semsources.cre.tests.")
        for _, rule in exported_rules(build(tmp_path)).values()
        for test in rule.iter("test")
    }
    assert classes == TEST_CLASSES


def test_offense_mapping_follows_the_scenarios_natural_key(tmp_path: Path) -> None:
    mapping = {"username": "3", "source_ip": "0"}
    by_name = {
        str(rule.findtext("name")): rule.find("actions")
        for _, rule in exported_rules(build(tmp_path)).values()
    }
    for name, index_by in EXPECTED.values():
        actions = by_name[name]
        assert actions is not None
        assert actions.get("offenseMapping") == mapping[index_by]
        assert actions.get("forceOffenseCreation") == "true"


# --- identities (criterion 4) -------------------------------------------------------------


def test_new_rules_get_a_uuid5_of_their_name_and_dcsync_keeps_the_installed_uuid() -> None:
    for scenario, (name, _) in EXPECTED.items():
        rule = rules_by_scenario()[scenario]
        if scenario == "s2-dcsync":
            assert rule.rule_uuid == DCSYNC_UUID  # type: ignore[attr-defined]
        else:
            expected = uuid.uuid5(uuid.UUID("5d0c1ab0-0000-4000-8000-00000000a150"), name)
            assert rule.rule_uuid == str(expected)  # type: ignore[attr-defined]
    assert len({r.rule_uuid for r in builder.load_rules()}) == len(EXPECTED)


def test_the_exported_uuids_are_the_installed_ones(tmp_path: Path) -> None:
    assert set(exported_rules(build(tmp_path))) == set(reference_rules())


# --- negative tests: a malformed source is rejected ---------------------------------------


def good_rule() -> dict[str, object]:
    return yaml.safe_load((LAB_DIR / "rules" / "kerberoasting.yaml").read_text(encoding="utf-8"))


def with_threshold(**counter: object) -> dict[str, object]:
    base = good_rule()
    conditions = [c for c in base["conditions"] if c["test"] != "threshold"]  # type: ignore[attr-defined,union-attr]
    return {**base, "conditions": [*conditions, {"test": "threshold", **counter}]}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": "Kerberoasting"}, "must start with"),
        ({"scenario": "s99-nope"}, "is not in"),
        ({"index_by": "hostname"}, "index_by"),
        ({"conditions": []}, "non-empty"),
        ({"conditions": [{"test": "payload_regex", "value": "x"}]}, "unknown test"),
        ({"conditions": [{"test": "payload_contains"}]}, "needs"),
        ({"conditions": [{"test": "qid", "value": 1, "extra": 2}]}, "unknown keys"),
        ({"conditions": [{"test": "log_source_type", "value": "Other DSM"}]}, "log source type"),
        ({"conditions": [{"test": "username_not_matches", "value": ["("]}]}, "regular expression"),
        ({"conditions": [{"test": "payload_contains_any", "value": ["a|b"]}]}, "plain words"),
        ({"surprise": True}, "unknown keys"),
        ({"uuid": "not-a-uuid"}, "uuid"),
        ({"description": " "}, "description"),
    ],
)
def test_a_malformed_rule_is_rejected(change: dict[str, object], message: str) -> None:
    with pytest.raises(builder.RuleError, match=message):
        builder.parse_rule({**good_rule(), **change}, "test.yaml")


@pytest.mark.parametrize(
    ("counter", "message"),
    [
        ({"events": 5, "window_seconds": 120, "same": "username"}, "minutes"),
        ({"events": 5, "window_minutes": 1.5, "same": "username"}, "whole number of minutes"),
        ({"events": 5, "window_minutes": 0, "same": "username"}, "whole number of minutes"),
        ({"events": 5, "window_minutes": 2, "same": "hostname"}, "same must be"),
        ({"window_minutes": 2, "same": "username"}, "needs events"),
        (
            {"events": 5, "distinct": "username", "window_minutes": 2, "same": "source_ip"},
            "together",
        ),
        (
            {
                "events": 10,
                "distinct": "username",
                "distinct_count": 5,
                "window_minutes": 5,
                "same": "source_ip",
            },
            "together",
        ),
        ({"distinct": "username", "window_minutes": 5, "same": "source_ip"}, "distinct_count"),
        (
            {"events": 5, "distinct_count": 5, "window_minutes": 2, "same": "username"},
            "needs events",
        ),
        (
            {
                "distinct": "Service Name",
                "distinct_count": 5,
                "window_minutes": 2,
                "same": "username",
            },
            "distinct must be",
        ),
    ],
)
def test_a_counter_the_rule_engine_cannot_express_is_rejected(
    counter: dict[str, object], message: str
) -> None:
    with pytest.raises(builder.RuleError, match=message):
        builder.parse_rule(with_threshold(**counter), "test.yaml")


def test_duplicate_rule_names_are_rejected(tmp_path: Path) -> None:
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(
            (LAB_DIR / "rules" / "kerberoasting.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    with pytest.raises(builder.RuleError, match="unique"):
        builder.load_rules(tmp_path)
