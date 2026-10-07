"""T-058 criterion 3: the lab rules' sources and the extension zip built from them."""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree as ET

import pytest
import yaml

LAB_DIR = Path(__file__).resolve().parents[1] / "lab" / "qradar"


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


def rules_by_scenario() -> dict[str, object]:
    return {rule.scenario: rule for rule in builder.load_rules()}


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
        ("username_not_ends_with", "$"),
        ("username_not_starts_with", "MSOL_"),
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


def test_the_zip_holds_a_valid_manifest_and_every_rule(tmp_path: Path) -> None:
    out = builder.build_zip(tmp_path / "rules.zip", builder.load_rules())
    with zipfile.ZipFile(out) as archive:
        assert archive.namelist() == ["content.xml", "info.json"]
        info = json.loads(archive.read("info.json"))
        root = ET.fromstring(archive.read("content.xml"))  # noqa: S314 - built above
    assert set(info) == {"name", "version", "description", "rules"}
    assert info["name"] == "AIS0C LAB rules"
    names = [rule.get("name", "") for rule in root.iter("rule")]
    assert names == info["rules"]
    assert sorted(names) == sorted(name for name, _ in EXPECTED.values())
    for rule in root.iter("rule"):
        assert rule.get("type") == "EVENT"
        assert rule.get("id")
        assert rule.find("testDefinitions") is not None
        assert len(list(rule.iter("test"))) >= 3
        index = rule.find("responses/offenseIndex")
        assert index is not None
        assert index.get("property") in {"username", "sourceip"}


def test_offense_index_follows_the_scenarios_natural_key(tmp_path: Path) -> None:
    out = builder.build_zip(tmp_path / "rules.zip", builder.load_rules())
    with zipfile.ZipFile(out) as archive:
        root = ET.fromstring(archive.read("content.xml"))  # noqa: S314 - built above
    indexed = {
        rule.get("name"): rule.find("responses/offenseIndex").get("property")  # type: ignore[union-attr]
        for rule in root.iter("rule")
    }
    for name, index_by in EXPECTED.values():
        assert indexed[name] == ("username" if index_by == "username" else "sourceip")


def test_the_build_is_deterministic(tmp_path: Path) -> None:
    first = builder.build_zip(tmp_path / "a.zip", builder.load_rules())
    second = builder.build_zip(tmp_path / "sub" / "b.zip", builder.load_rules())
    assert first.read_bytes() == second.read_bytes()
    # Rule ids come from the names, so they do not change between builds.
    assert len({rule.rule_id for rule in builder.load_rules()}) == len(EXPECTED)
    assert [r.rule_id for r in builder.load_rules()] == [r.rule_id for r in builder.load_rules()]


def test_the_cli_writes_the_zip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "rules.zip"
    assert builder.main(["--out", str(out)]) == 0
    assert out.is_file()
    assert "7 rules" in capsys.readouterr().out


# --- negative tests: a malformed source is rejected ---------------------------------------


def good_rule() -> dict[str, object]:
    return yaml.safe_load((LAB_DIR / "rules" / "kerberoasting.yaml").read_text(encoding="utf-8"))


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
        ({"surprise": True}, "unknown keys"),
        ({"description": " "}, "description"),
    ],
)
def test_a_malformed_rule_is_rejected(change: dict[str, object], message: str) -> None:
    with pytest.raises(builder.RuleError, match=message):
        builder.parse_rule({**good_rule(), **change}, "test.yaml")


def test_duplicate_rule_names_are_rejected(tmp_path: Path) -> None:
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(
            (LAB_DIR / "rules" / "kerberoasting.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    with pytest.raises(builder.RuleError, match="unique"):
        builder.load_rules(tmp_path)
