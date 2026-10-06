"""The examples in the QRadar tool registry (config/connectors/qradar.yaml, T-039, T-050).

A filter example is a span in backticks in the description of a tool's `filter` argument, such
as `source_ip = '192.0.2.1'`; an AQL example sits in the description of a tool's
`query_expression` argument. No other description uses backticks, so the lab test
(test_filter_examples_lab.py) runs every filter example and test_aql_examples.py runs every
AQL example through the Guard.

IP addresses in the descriptions follow one rule: each sits in single quotes and is a
documentation address (AGENTS.md hard rule 6). QRadar's filter parser refuses an unquoted
address with 422 "Error Parsing filter" and takes single and double quotes alike. The examples
use single quotes because a double quote must be escaped in the JSON arguments of a tool call:
on the lab (case-28-triage-1, 2026-10-05) the model broke its arguments doing that and then left
the quotes out.
"""

import ipaddress
import re
from collections.abc import Iterator, Mapping
from typing import Any, Final

EXAMPLE: Final = re.compile(r"`([^`]*)`")
# An IPv4 address or network, not part of a longer dotted number.
_IPV4: Final = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?(?!\w|\.\d)")
# A run that may be an IPv6 address or network; only what Python parses as one counts.
_IPV6: Final = re.compile(
    r"(?<![\w:.])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?:/\d{1,3})?(?![\w:])"
)
_DOCUMENTATION_V4: Final = tuple(
    ipaddress.IPv4Network(network)
    for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")  # RFC 5737
)
_DOCUMENTATION_V6: Final = (ipaddress.IPv6Network("2001:db8::/32"),)  # RFC 3849
_VALID_FIELDS: Final = re.compile(r"Valid fields: ([a-z0-9_, ]+)\.")
_QUOTED: Final = re.compile(r"'[^']*'|\"[^\"]*\"")
_JOIN: Final = re.compile(r"\b(?:and|or)\b")

# The QRadar REST endpoint each tool with filter examples reads: the fork's tools/endpoints.py at
# the manifest's server_version. The fork passes `filter` to QRadar as it is.
REST_PATHS: Final = {
    "list_offenses": "/siem/offenses",
    "list_offense_types": "/siem/offense_types",
    "list_source_addresses": "/siem/source_addresses",
    "list_local_destination_addresses": "/siem/local_destination_addresses",
    "list_rules": "/analytics/rules",
    "list_assets": "/asset_model/assets",
    "list_log_sources": "/config/event_sources/log_source_management/log_sources",
    "list_log_source_types": "/config/event_sources/log_source_management/log_source_types",
    "list_reference_sets": "/reference_data_collections/sets",
    "list_reference_maps": "/reference_data/maps",
    "list_reference_tables": "/reference_data/tables",
}


def descriptions(node: object, where: str = "") -> Iterator[tuple[str, str]]:
    """Every description in the manifest with its place, e.g.
    tools.list_assets.input_schema.properties.filter.description."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            path = f"{where}.{key}" if where else str(key)
            if key == "description" and isinstance(value, str):
                yield path, value
            else:
                yield from descriptions(value, path)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from descriptions(item, f"{where}[{index}]")


def filter_description(entry: Mapping[str, Any]) -> str | None:
    """The description of a tool's `filter` argument, if it has one."""
    spec = entry["input_schema"]["properties"].get("filter")
    if isinstance(spec, Mapping) and isinstance(spec.get("description"), str):
        return str(spec["description"])
    return None


def filter_examples(manifest: Mapping[str, Any]) -> dict[str, list[str]]:
    """The filter examples of each tool that shows any, in the order of the description."""
    found: dict[str, list[str]] = {}
    for tool_id, entry in manifest["tools"].items():
        text = filter_description(entry)
        shown = EXAMPLE.findall(text) if text else []
        if shown:
            found[tool_id] = shown
    return found


def query_description(entry: Mapping[str, Any]) -> str | None:
    """The description of a tool's `query_expression` argument, if it has one."""
    spec = entry["input_schema"]["properties"].get("query_expression")
    if isinstance(spec, Mapping) and isinstance(spec.get("description"), str):
        return str(spec["description"])
    return None


def query_examples(manifest: Mapping[str, Any]) -> dict[str, list[str]]:
    """The AQL examples of each tool that shows any, in the order of the description."""
    found: dict[str, list[str]] = {}
    for tool_id, entry in manifest["tools"].items():
        text = query_description(entry)
        shown = EXAMPLE.findall(text) if text else []
        if shown:
            found[tool_id] = shown
    return found


def listed_fields(entry: Mapping[str, Any]) -> set[str]:
    """The field names the description of a tool's `fields` argument lists."""
    spec = entry["input_schema"]["properties"].get("fields")
    match = _VALID_FIELDS.search(spec["description"]) if isinstance(spec, Mapping) else None
    return set(match.group(1).replace(" ", "").split(",")) if match else set()


def condition_fields(example: str) -> list[str]:
    """The field each condition of a filter starts with: `a = 1 and b = 'x'` gives a and b."""
    bare = _QUOTED.sub("''", example)
    parts = (part.strip(" ()") for part in _JOIN.split(bare))
    return [part.split()[0] for part in parts if part]


def ip_addresses(text: str) -> list[str]:
    """The IP addresses and networks in a text, in order."""
    return [match.group() for match in _ip_matches(text)]


def ip_problems(text: str) -> list[str]:
    """What breaks the rule for IP addresses in a text; empty if nothing does."""
    problems: list[str] = []
    for match in _ip_matches(text):
        address = match.group()
        before, after = text[match.start() - 1 : match.start()], text[match.end() : match.end() + 1]
        if (before, after) != ("'", "'"):
            problems.append(f"{address} is not in single quotes")
        if not _documentation(address):
            problems.append(f"{address} is not a documentation address (RFC 5737, RFC 3849)")
    return problems


def manifest_ip_problems(manifest: Mapping[str, Any]) -> list[str]:
    """ip_problems of every description in the manifest, each with its place."""
    return [
        f"{where}: {problem}"
        for where, text in descriptions(manifest)
        for problem in ip_problems(text)
    ]


def _ip_matches(text: str) -> list[re.Match[str]]:
    matches = list(_IPV4.finditer(text))
    matches += [
        match
        for match in _IPV6.finditer(text)
        if match.group().count(":") >= 2 and _network(match.group()) is not None
    ]
    return sorted(matches, key=lambda match: match.start())


def _network(address: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    try:
        return ipaddress.ip_network(address, strict=False)
    except ValueError:
        return None


def _documentation(address: str) -> bool:
    network = _network(address)
    if isinstance(network, ipaddress.IPv4Network):
        return any(network.subnet_of(documentation) for documentation in _DOCUMENTATION_V4)
    if isinstance(network, ipaddress.IPv6Network):
        return any(network.subnet_of(documentation) for documentation in _DOCUMENTATION_V6)
    return False
