"""Build the lab rules' QRadar extension zip from their sources (T-058).

    uv run python harness/lab/qradar/build_extension.py --out ais0c-lab-rules.zip

The sources are the YAML files under ``rules/``: one CRE event rule each, named
``AIS0C LAB - ...``, tied to the scenario it opens an offense for and indexing the
offense by that scenario's natural key (a user name or a source address). The zip holds

* ``info.json``: the extension's name, version and description;
* ``content.xml``: the rules, in the shape of QRadar's content export.

The build is deterministic: the same sources give the same bytes (sorted rules, ids
derived from names, fixed zip timestamps), so a change to a rule shows up as a change
of the zip and nothing else does.

This script installs nothing. The README of this directory says how the zip gets into
the lab. The XML is written from QRadar's content-export structure and has not been
installed on a console yet: the first install runs the console's own validation
("Preview" in Extensions Management), which is the check of record.
"""

from __future__ import annotations

import argparse
import json
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import yaml

HERE = Path(__file__).resolve().parent
RULES_DIR = HERE / "rules"
SCENARIOS_DIR = HERE.parents[1] / "scenarios"

NAME_PREFIX = "AIS0C LAB - "
EXTENSION_NAME = "AIS0C LAB rules"
EXTENSION_VERSION = "1.0.0"
#: Fixed so that the zip's bytes depend only on the sources.
ZIP_TIMESTAMP = (2026, 10, 8, 0, 0, 0)
#: Namespace of the ids derived from rule names.
_ID_NAMESPACE = uuid.UUID("5d0c1ab0-0000-4000-8000-00000000a150")

#: The offense index of each ``index_by`` value: the QRadar property and its label.
INDEX_BY: Mapping[str, tuple[str, str]] = {
    "username": ("username", "Username"),
    "source_ip": ("sourceip", "Source IP"),
}

#: The test types a rule may use, each with the parameters it needs.
TEST_PARAMETERS: Mapping[str, tuple[str, ...]] = {
    "log_source_type": ("value",),
    "qid": ("value",),
    "payload_contains": ("value",),
    "payload_contains_any": ("value",),
    "username_not_ends_with": ("value",),
    "username_not_starts_with": ("value",),
    "source_ip_in": ("value",),
    "source_ip_not_in": ("value",),
    "threshold": ("events", "window_seconds", "same"),
}
_OPTIONAL_TEST_KEYS = {"meaning", "distinct", "distinct_count"}
_RULE_KEYS = {"name", "scenario", "description", "index_by", "conditions"}


class RuleError(ValueError):
    """Raised when a rule source is malformed."""


@dataclass(frozen=True)
class Rule:
    name: str
    scenario: str
    description: str
    index_by: str
    conditions: tuple[Mapping[str, Any], ...]
    source: str

    @property
    def rule_id(self) -> str:
        return str(uuid.uuid5(_ID_NAMESPACE, self.name))

    @property
    def offense_key(self) -> str:
        """The property the offense is indexed by, as QRadar names it."""
        return INDEX_BY[self.index_by][0]


# --- loading ------------------------------------------------------------------------------


def _check_condition(raw: object, where: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise RuleError(f"{where}: a condition must be a mapping")
    test = str(raw.get("test"))
    if test not in TEST_PARAMETERS:
        raise RuleError(f"{where}: unknown test {test!r}; one of {sorted(TEST_PARAMETERS)}")
    allowed = {"test", *TEST_PARAMETERS[test], *_OPTIONAL_TEST_KEYS}
    unknown = set(raw) - allowed
    if unknown:
        raise RuleError(f"{where}: unknown keys {sorted(unknown)} for test {test!r}")
    missing = [key for key in TEST_PARAMETERS[test] if raw.get(key) in (None, "", [])]
    if missing:
        raise RuleError(f"{where}: test {test!r} needs {missing}")
    return {str(key): value for key, value in raw.items()}


def parse_rule(raw: object, source: str, scenarios_dir: Path = SCENARIOS_DIR) -> Rule:
    if not isinstance(raw, dict):
        raise RuleError(f"{source}: a rule must be a mapping")
    unknown = set(raw) - _RULE_KEYS
    if unknown:
        raise RuleError(f"{source}: unknown keys {sorted(unknown)}")
    name, scenario, description = raw.get("name"), raw.get("scenario"), raw.get("description")
    if not isinstance(name, str) or not name.startswith(NAME_PREFIX):
        raise RuleError(f"{source}: the name must start with {NAME_PREFIX!r}")
    if not isinstance(scenario, str) or not (scenarios_dir / f"{scenario}.yaml").is_file():
        raise RuleError(f"{source}: scenario {scenario!r} is not in {scenarios_dir}")
    if not isinstance(description, str) or not description.strip():
        raise RuleError(f"{source}: a description is required")
    index_by = raw.get("index_by")
    if index_by not in INDEX_BY:
        raise RuleError(f"{source}: index_by must be one of {sorted(INDEX_BY)}")
    conditions = raw.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise RuleError(f"{source}: conditions must be a non-empty list")
    checked = tuple(
        _check_condition(c, f"{source}.conditions[{i}]") for i, c in enumerate(conditions)
    )
    return Rule(name, scenario, description.strip(), str(index_by), checked, source)


def load_rules(rules_dir: Path = RULES_DIR, scenarios_dir: Path = SCENARIOS_DIR) -> list[Rule]:
    """Every rule source, in file-name order. Names and scenarios must be unique."""
    paths = sorted(rules_dir.glob("*.yaml"))
    if not paths:
        raise RuleError(f"no rule sources in {rules_dir}")
    rules = [
        parse_rule(yaml.safe_load(path.read_text(encoding="utf-8")), path.name, scenarios_dir)
        for path in paths
    ]
    for label, values in (
        ("name", [r.name for r in rules]),
        ("scenario", [r.scenario for r in rules]),
    ):
        if len(set(values)) != len(values):
            raise RuleError(f"rule {label}s must be unique")
    return rules


# --- rendering ----------------------------------------------------------------------------


def describe_condition(condition: Mapping[str, Any]) -> str:
    """The condition in QRadar's rule wording, as shown in the rule editor."""
    test = condition["test"]
    value: Any = condition.get("value", [])
    if test == "log_source_type":
        return f"and when the log source type is {value}"
    if test == "qid":
        return f"and when the event QID is one of {value}"
    if test == "payload_contains":
        return f"and when the event payload contains {value}"
    if test == "payload_contains_any":
        return "and when the event payload contains any of " + " | ".join(map(str, value))
    if test == "username_not_ends_with":
        return f"and when the username does not end with {value}"
    if test == "username_not_starts_with":
        return f"and when the username does not start with {value}"
    if test == "source_ip_in":
        return "and when the source IP is in any of " + ", ".join(map(str, value))
    if test == "source_ip_not_in":
        return "and when the source IP is not in any of " + ", ".join(map(str, value))
    text = (
        f"and when at least {condition['events']} events are seen with the same "
        f"{condition['same']} in {condition['window_seconds']} seconds"
    )
    if condition.get("distinct"):
        text += f", with at least {condition['distinct_count']} different {condition['distinct']}"
    return text


def _rule_element(rule: Rule) -> ET.Element:
    element = ET.Element(
        "rule",
        {
            "id": rule.rule_id,
            "name": rule.name,
            "type": "EVENT",
            "enabled": "true",
            "owner": "admin",
            "scope": "LOCAL",
        },
    )
    ET.SubElement(element, "notes").text = rule.description
    tests = ET.SubElement(element, "testDefinitions")
    for position, condition in enumerate(rule.conditions, start=1):
        test = ET.SubElement(
            tests, "test", {"position": str(position), "type": str(condition["test"])}
        )
        ET.SubElement(test, "text").text = describe_condition(condition)
        for key in sorted(set(condition) - {"test", "meaning"}):
            value = condition[key]
            rendered = ",".join(map(str, value)) if isinstance(value, list) else str(value)
            ET.SubElement(test, "parameter", {"name": key}).text = rendered
    key, label = INDEX_BY[rule.index_by]
    responses = ET.SubElement(element, "responses")
    ET.SubElement(responses, "offenseIndex", {"property": key, "label": label})
    return element


def render_content_xml(rules: Sequence[Rule]) -> str:
    root = ET.Element("content")
    for rule in rules:
        ET.SubElement(root, "custom_rule").append(_rule_element(rule))
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def render_info(rules: Sequence[Rule]) -> str:
    info = {
        "name": EXTENSION_NAME,
        "version": EXTENSION_VERSION,
        "description": "Event rules that open offenses for the AIS0C lab scenarios: "
        + ", ".join(rule.scenario for rule in rules),
        "rules": [rule.name for rule in rules],
    }
    return json.dumps(info, indent=2, sort_keys=True) + "\n"


def build_zip(out: Path, rules: Sequence[Rule]) -> Path:
    """Write the extension zip. Deterministic: sorted entries, fixed timestamps."""
    files = {"content.xml": render_content_xml(rules), "info.json": render_info(rules)}
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            entry = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, files[name].encode("utf-8"))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the lab rules' extension zip.")
    parser.add_argument("--out", type=Path, default=Path("ais0c-lab-rules.zip"))
    args = parser.parse_args(argv)
    try:
        rules = load_rules()
    except RuleError as error:
        print(f"error: {error}")
        return 2
    path = build_zip(args.out, rules)
    print(f"{path}: {len(rules)} rules")
    for rule in rules:
        print(f"  {rule.name}  [{rule.scenario}, indexed by {rule.index_by}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
