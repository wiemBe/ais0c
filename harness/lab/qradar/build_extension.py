"""Build the lab rules' QRadar extension zip from their sources (T-058, T-061).

    uv run python harness/lab/qradar/build_extension.py --out ais0c-lab-rules.zip

The sources are the YAML files under ``rules/``: one CRE event rule each, named
``AIS0C LAB - ...``, tied to the scenario it opens an offense for and indexing the
offense by that scenario's natural key (a user name or a source address). The zip is in
the shape of QRadar's own content export (the one the console's Extensions Management
installs) and holds two files:

* ``ais0c-lab-rules.xml``: ``<content>`` with one ``<custom_rule>`` per rule; its
  ``rule_data`` is the base64 of the rule's ``<rule>`` XML (the CRE test classes
  ``DeviceTypeID_Test``, ``QID_Test``, ``EventPayload_Test``, ``Regex_Test``,
  ``SrcHost_Test`` and ``functions.MatchCount``);
* ``manifest.txt``: the extension manifest, as JSON.

The build is deterministic: the same sources give the same bytes (sorted rules, ids
derived from names, fixed dates and zip timestamps), so a change to a rule shows up as a
change of the zip and nothing else does. A rule's ``uuid`` is what QRadar matches on when
the zip is installed again, so it never changes for a rule that already exists in the lab.

This script installs nothing. The README of this directory says how the zip gets into
the lab.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
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
EXTENSION_ID = "ais0c-lab-rules"
EXTENSION_NAME = "AIS0C LAB rules"
EXTENSION_VERSION = "1.1.0"
#: Fixed so that the zip's bytes depend only on the sources.
ZIP_TIMESTAMP = (2026, 10, 8, 0, 0, 0)
EXPORT_DATE = "2026-10-08T00:00:00.000Z"
#: Namespace of the ids derived from rule names.
_ID_NAMESPACE = uuid.UUID("5d0c1ab0-0000-4000-8000-00000000a150")
#: The first rule id; QRadar assigns its own on install and matches by uuid.
FIRST_RULE_ID = 100353

#: How a rule indexes its offense: the QRadar ``offenseMapping``.
OFFENSE_MAPPING: Mapping[str, int] = {"username": 3, "source_ip": 0}

#: The event fields a counter may use: the database name and the label QRadar shows.
FIELDS: Mapping[str, tuple[str, str]] = {
    "username": ("userName", "Username"),
    "source_ip": ("sourceIP", "Source IP"),
}

#: The log source types the lab rules use, by the name of the DSM and its id in QRadar.
LOG_SOURCE_TYPES: Mapping[str, int] = {
    "Microsoft Windows Security Event Log": 12,
    "F5 Networks BIG-IP ASM": 213,
}

_REQUIRED = "EventViewer.RULECREATION|SURVEILLANCE.RULECREATION"
_CLASS_PREFIX = "com.q1labs.semsources.cre.tests."

#: The test types a rule may use, each with the keys it needs.
TEST_PARAMETERS: Mapping[str, tuple[str, ...]] = {
    "log_source_type": ("value",),
    "qid": ("value",),
    "payload_contains": ("value",),
    "payload_contains_any": ("value",),
    "username_not_matches": ("value",),
    "source_ip_in": ("value",),
    "source_ip_not_in": ("value",),
    "threshold": ("window_minutes", "same"),
}
_OPTIONAL_TEST_KEYS = {"meaning"}
_THRESHOLD_KEYS = {"distinct", "events", "distinct_count"}
_RULE_KEYS = {"name", "scenario", "description", "index_by", "conditions", "uuid"}
#: What ``payload_contains_any`` may hold: plain words, since they are joined into a regex.
_PLAIN_WORDS = re.compile(r"[\w .:-]+")


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
    fixed_uuid: str | None = None

    @property
    def rule_uuid(self) -> str:
        return self.fixed_uuid or str(uuid.uuid5(_ID_NAMESPACE, self.name))


# --- loading ------------------------------------------------------------------------------


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _check_threshold(raw: Mapping[str, Any], where: str) -> None:
    if not _whole(raw.get("window_minutes")):
        raise RuleError(f"{where}: window_minutes must be a whole number of minutes, at least 1")
    if raw.get("same") not in FIELDS:
        raise RuleError(f"{where}: same must be one of {sorted(FIELDS)}")
    distinct = raw.get("distinct")
    if distinct is None:
        if raw.get("distinct_count") is not None or not _whole(raw.get("events")):
            raise RuleError(f"{where}: without distinct, a threshold needs events (a count)")
        return
    if distinct not in FIELDS:
        raise RuleError(f"{where}: distinct must be one of {sorted(FIELDS)}")
    if raw.get("events") is not None:
        # QRadar's counter holds one number: events, or different values, not both.
        raise RuleError(
            f"{where}: a counter cannot count events and different values together; "
            "with distinct, give distinct_count and no events"
        )
    if not _whole(raw.get("distinct_count")):
        raise RuleError(f"{where}: with distinct, a threshold needs distinct_count")


def _check_condition(raw: object, where: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise RuleError(f"{where}: a condition must be a mapping")
    test = str(raw.get("test"))
    if test not in TEST_PARAMETERS:
        raise RuleError(f"{where}: unknown test {test!r}; one of {sorted(TEST_PARAMETERS)}")
    if "window_seconds" in raw:
        raise RuleError(f"{where}: QRadar's counter takes minutes; use window_minutes")
    allowed = {"test", *TEST_PARAMETERS[test], *_OPTIONAL_TEST_KEYS}
    if test == "threshold":
        allowed |= _THRESHOLD_KEYS
    unknown = set(raw) - allowed
    if unknown:
        raise RuleError(f"{where}: unknown keys {sorted(unknown)} for test {test!r}")
    missing = [key for key in TEST_PARAMETERS[test] if raw.get(key) in (None, "", [])]
    if missing:
        raise RuleError(f"{where}: test {test!r} needs {missing}")
    if test == "threshold":
        _check_threshold(raw, where)
    elif test == "log_source_type" and raw["value"] not in LOG_SOURCE_TYPES:
        raise RuleError(f"{where}: unknown log source type {raw['value']!r}")
    elif test == "payload_contains_any":
        words = raw["value"]
        if not isinstance(words, list) or not all(
            isinstance(w, str) and _PLAIN_WORDS.fullmatch(w) for w in words
        ):
            raise RuleError(f"{where}: payload_contains_any needs a list of plain words")
    elif test == "username_not_matches":
        patterns = raw["value"]
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            raise RuleError(f"{where}: username_not_matches needs a list of regular expressions")
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as error:
                raise RuleError(
                    f"{where}: {pattern!r} is not a regular expression: {error}"
                ) from None
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
    if index_by not in OFFENSE_MAPPING:
        raise RuleError(f"{source}: index_by must be one of {sorted(OFFENSE_MAPPING)}")
    fixed_uuid = raw.get("uuid")
    if fixed_uuid is not None:
        try:
            fixed_uuid = str(uuid.UUID(str(fixed_uuid)))
        except ValueError:
            raise RuleError(f"{source}: uuid is not a UUID") from None
    conditions = raw.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise RuleError(f"{source}: conditions must be a non-empty list")
    checked = tuple(
        _check_condition(c, f"{source}.conditions[{i}]") for i, c in enumerate(conditions)
    )
    return Rule(name, scenario, description.strip(), str(index_by), checked, source, fixed_uuid)


def load_rules(rules_dir: Path = RULES_DIR, scenarios_dir: Path = SCENARIOS_DIR) -> list[Rule]:
    """Every rule source, in file-name order. Names, scenarios and uuids must be unique."""
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
        ("uuid", [r.rule_uuid for r in rules]),
    ):
        if len(set(values)) != len(values):
            raise RuleError(f"rule {label}s must be unique")
    return rules


# --- rendering ----------------------------------------------------------------------------

_REGEX_FIELDS = {
    "MAC": "MAC",
    "SourceMAC": "source MAC",
    "DestinationMAC": "destination MAC",
    "UserName": "username",
    "SourceUserName": "source username",
    "DestinationUserName": "destination username",
    "EventUserName": "event username",
    "HostName": "hostname",
    "SourceHostName": "source hostname",
    "DestinationHostName": "destination hostname",
    "OS": "OS",
    "SourceOS": "source OS",
    "DestinationOS": "destination OS",
    "Payload": "event payload",
}
_EVENT_FIELDS_METHOD = "com.q1labs.sem.ui.semservices.UISemServices.getEventDatabaseFields"
_POSITIVE_NUMBER = {
    "format": "user",
    "validation": "com.q1labs.core.ui.util.ValidatorUtils.validatePositiveNumber",
    "errorkey": "30001",
    "multiselect": "false",
}


def _link(position: int, parameter: int, text: object) -> str:
    return (
        f'<a href=\'javascript:editParameter("{position}", "{parameter}")\' '
        f"class='dynamic'>{text}</a>"
    )


def _options(attributes: Mapping[str, str], choices: Mapping[str, str] | None = None) -> ET.Element:
    element = ET.Element("userOptions", dict(attributes))
    for key, label in (choices or {}).items():
        ET.SubElement(element, "option", {"id": key}).text = label
    return element


def _parameter(
    test: ET.Element,
    number: int,
    initial: str,
    label: str,
    selection: object,
    options: ET.Element,
    *,
    name: str | None = None,
    types: bool = False,
) -> None:
    parameter = ET.SubElement(test, "parameter", {"id": str(number)})
    if name:
        ET.SubElement(parameter, "name").text = name
    ET.SubElement(parameter, "initialText").text = initial
    ET.SubElement(parameter, "selectionLabel").text = label
    parameter.append(options)
    ET.SubElement(parameter, "userSelection").text = str(selection)
    if types:
        ET.SubElement(parameter, "userSelectionTypes")
    ET.SubElement(parameter, "userSelectionId").text = "0"


def _test(
    tests: ET.Element,
    position: int,
    test_id: int,
    class_name: str,
    group: str,
    text: str,
    *,
    negate: bool = False,
    group_id: int | None = None,
    capabilities: bool = True,
) -> ET.Element:
    attributes = {
        "id": str(test_id),
        "name": _CLASS_PREFIX + class_name,
        "uid": str(position),
        "group": group,
    }
    if group_id is not None:
        attributes["groupId"] = str(group_id)
    if negate:
        attributes["negate"] = "true"
    if capabilities:
        attributes["requiredCapabilities"] = _REQUIRED
    test = ET.SubElement(tests, "test", attributes)
    ET.SubElement(test, "text").text = text
    return test


def _regex_test(tests: ET.Element, position: int, field: str, regex: str, *, negate: bool) -> None:
    lead = "and NOT when the " if negate else "when the "
    text = (
        f"{lead}{_link(position, 1, _REGEX_FIELDS[field])} matches the following "
        f"{_link(position, 2, regex)}"
    )
    test = _test(tests, position, 100, "Regex_Test", "Event Property Tests", text, negate=negate)
    field_options = _options(
        {"format": "list", "source": "xml", "multiselect": "false"}, _REGEX_FIELDS
    )
    regex_options = _options(
        {
            "format": "user",
            "source": "user",
            "validation": "com.q1labs.core.ui.util.ValidatorUtils.validateRegularExpression",
            "errorkey": "12000",
            "multiselect": "true",
        }
    )
    _parameter(test, 1, "username", "Select a field", field, field_options)
    _parameter(test, 2, "regex", "Enter a regex string", regex, regex_options)


def _match_count(tests: ET.Element, position: int, condition: Mapping[str, Any]) -> None:
    same, same_label = FIELDS[condition["same"]]
    distinct = condition.get("distinct")
    count = condition["distinct_count"] if distinct else condition["events"]
    minutes = condition["window_minutes"]
    text = (
        f"when at least {_link(position, 2, count)} events are seen with the same "
        f"{_link(position, 3, same_label)}"
    )
    if distinct:
        text += f" and different {_link(position, 4, FIELDS[distinct][1])}"
    text += f" in {_link(position, 5, minutes)} {_link(position, 6, 'minutes')}"
    test = _test(tests, position, 301, "functions.MatchCount", "Functions - Counters", text)
    property_label = "Select a event property and click 'Add'"
    rules_options = _options(
        {
            "format": "list",
            "source": "class",
            "method": "com.q1labs.sem.ui.semservices.UISemServices.getEventRules",
            "multiselect": "true",
        }
    )
    unit_options = _options(
        {"format": "list", "source": "xml", "multiselect": "false"},
        {"m": "minutes", "h": "hour(s)", "d": "day(s)"},
    )

    def fields() -> ET.Element:
        return _options(
            {
                "format": "list",
                "source": "class",
                "method": _EVENT_FIELDS_METHOD,
                "multiselect": "true",
            }
        )

    _parameter(
        test, 1, "these rules", "Select the rule(s)", " ", rules_options, name="getEventRules"
    )
    _parameter(test, 2, "this many", "Enter a value", count, _options(_POSITIVE_NUMBER))
    _parameter(test, 3, "event properties", property_label, same, fields())
    _parameter(
        test, 4, "event properties", property_label,
        FIELDS[distinct][0] if distinct else " ", fields(),
    )  # fmt: skip
    _parameter(test, 5, "this many", "Enter a value", minutes, _options(_POSITIVE_NUMBER))
    _parameter(test, 6, "minutes", "Select a time unit", "m", unit_options)


def _add_test(tests: ET.Element, position: int, condition: Mapping[str, Any]) -> None:
    kind = condition["test"]
    value: Any = condition.get("value", [])
    if kind == "log_source_type":
        text = f"when the event(s) were detected by one or more of {_link(position, 1, value)}"
        test = _test(
            tests, position, 14, "DeviceTypeID_Test",
            "jsp.qradar.rulewizard.condition.page.group.log", text, group_id=10,
        )  # fmt: skip
        options = _options(
            {
                "format": "list",
                "source": "class",
                "method": "com.q1labs.sem.ui.semservices.UISemServices.getDeviceTypeDescs",
                "multiselect": "true",
            }
        )
        _parameter(
            test, 1, "these log source types", "Select a Log Source type and click 'Add'",
            LOG_SOURCE_TYPES[str(value)], options, types=True,
        )  # fmt: skip
    elif kind == "qid":
        shown = f"({value}) {condition['meaning']}" if condition.get("meaning") else f"({value})"
        text = f"when the event QID is one of the following {_link(position, 1, shown)}"
        test = _test(tests, position, 19, "QID_Test", "Event Property Tests", text)
        options = _options(
            {
                "format": "CustomizeParameter-QID.jsp",
                "source": "class",
                "method": "com.q1labs.sem.ui.semservices.UISemServices.getQidsByLowLevelCategory",
                "multiselect": "true",
            }
        )
        _parameter(
            test, 1, "QIDs",
            "Browse or Search for QIDs below. Select the desired QIDs and click 'Add'",
            value, options,
        )  # fmt: skip
    elif kind == "payload_contains":
        text = f"when the Event Payload contains {_link(position, 1, value)}"
        test = _test(
            tests, position, 3, "EventPayload_Test", "Event Property Tests", text,
            capabilities=False,
        )  # fmt: skip
        options = _options({"format": "user", "source": "user", "multiselect": "false"})
        _parameter(test, 1, "this string", "Enter a payload string", value, options)
    elif kind == "payload_contains_any":
        _regex_test(tests, position, "Payload", "|".join(value), negate=False)
    elif kind == "username_not_matches":
        _regex_test(tests, position, "EventUserName", ", ".join(value), negate=True)
    elif kind in ("source_ip_in", "source_ip_not_in"):
        negate = kind == "source_ip_not_in"
        ranges = ", ".join(map(str, value))
        lead = "and NOT when" if negate else "when"
        text = f"{lead} the source IP is one of the following {_link(position, 1, ranges)}"
        test = _test(tests, position, 8, "SrcHost_Test", "IP / Port Tests", text, negate=negate)
        options = _options(
            {
                "format": "user",
                "validation": "com.q1labs.core.ui.util.ValidatorUtils.validateCidr",
                "errorkey": "1026",
                "multiselect": "true",
            }
        )
        _parameter(
            test, 1, "IP addresses", "Enter an IP address or CIDR and click 'Add'", ranges, options
        )
    else:
        _match_count(tests, position, condition)


def rule_element(rule: Rule, rule_id: int = FIRST_RULE_ID) -> ET.Element:
    """The rule as QRadar's ``<rule>`` XML (the content of ``rule_data``)."""
    element = ET.Element(
        "rule",
        {
            "id": str(rule_id),
            "enabled": "true",
            "buildingBlock": "false",
            "roleDefinition": "false",
            "type": "EVENT",
            "scope": "LOCAL",
            "owner": "admin",
            "overrideid": str(rule_id),
        },
    )
    ET.SubElement(element, "name").text = rule.name
    ET.SubElement(element, "notes").text = rule.description
    tests = ET.SubElement(element, "testDefinitions")
    for position, condition in enumerate(rule.conditions):
        _add_test(tests, position, condition)
    ET.SubElement(
        element,
        "actions",
        {
            "offenseMapping": str(OFFENSE_MAPPING[rule.index_by]),
            "forceOffenseCreation": "true",
            "includeAttackerEventsInterval": "0",
            "flowAnalysisInterval": "0",
        },
    )
    ET.SubElement(element, "responses")
    return element


def render_content_xml(rules: Sequence[Rule]) -> str:
    root = ET.Element("content")
    # Ids follow the scenario order, so a rule keeps its id while the set stays as it is.
    for offset, rule in enumerate(sorted(rules, key=lambda r: r.scenario)):
        rule_id = FIRST_RULE_ID + offset
        data = ET.tostring(rule_element(rule, rule_id), encoding="unicode")
        custom = ET.SubElement(root, "custom_rule")
        ET.SubElement(custom, "origin").text = "USER"
        ET.SubElement(custom, "mod_date").text = EXPORT_DATE
        ET.SubElement(custom, "rule_data").text = base64.b64encode(data.encode("utf-8")).decode()
        ET.SubElement(custom, "uuid").text = rule.rule_uuid
        ET.SubElement(custom, "rule_type").text = "0"
        ET.SubElement(custom, "id").text = str(rule_id)
        ET.SubElement(custom, "create_date").text = EXPORT_DATE
    ET.indent(root, space="\t")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def render_manifest(rules: Sequence[Rule]) -> str:
    description = "Event rules that open offenses for the AIS0C lab scenarios: " + ", ".join(
        sorted(rule.scenario for rule in rules)
    )
    manifest = {
        "doc": {
            "extension_manifest": {
                "min_qradar_version": "7.5.0",
                "authored_by": "ais0c",
                "authored_by_email": "ais0c",
                "package_size": "0",
                "supported_language_set": ["en-US"],
                "extension_name": "extension.name",
                "extension_long_description": "extension.long.description",
                "locale": {
                    "en-US": {
                        "extension.long.description": description,
                        "extension.name": EXTENSION_NAME,
                    }
                },
                "version": EXTENSION_VERSION,
            }
        },
        "_id": EXTENSION_ID,
    }
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def build_zip(out: Path, rules: Sequence[Rule]) -> Path:
    """Write the extension zip. Deterministic: sorted entries, fixed timestamps."""
    files = {
        f"{EXTENSION_ID}.xml": render_content_xml(rules),
        "manifest.txt": render_manifest(rules),
    }
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
