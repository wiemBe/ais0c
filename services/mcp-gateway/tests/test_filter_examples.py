"""T-039 criteria 1, 2 and 4: the filter examples in the QRadar tool descriptions.

The checks run on the real config/connectors/qradar.yaml; test_filter_examples_lab.py runs the
examples on the lab QRadar (criterion 3). The bad filters below are ones the Triage agent sent
in the lab runs of 2026-10-05, with documentation addresses in place of the lab's.
"""

import copy
import re
from typing import Any

import pytest
from filter_examples import (
    REST_PATHS,
    condition_fields,
    descriptions,
    filter_description,
    filter_examples,
    ip_addresses,
    ip_problems,
    listed_fields,
    manifest_ip_problems,
)
from gateway_support import load_config

QUOTED_IP = r"'[0-9A-Fa-f.:/]+'"
FILTER_DESCRIPTION = "input_schema.properties.filter.description"


@pytest.fixture
def manifest() -> dict[str, Any]:
    manifest, _ = load_config()
    return manifest


def test_each_ip_list_tool_shows_quoted_examples_with_its_own_field(
    manifest: dict[str, Any],
) -> None:
    examples = filter_examples(manifest)
    ip_fields = [
        (tool_id, field)
        for tool_id, entry in manifest["tools"].items()
        if filter_description(entry) is not None
        for field in sorted(listed_fields(entry))
        if field.endswith("_ip")
    ]

    assert {
        ("list_source_addresses", "source_ip"),
        ("list_local_destination_addresses", "local_destination_ip"),
    } <= set(ip_fields)
    for tool_id, field in ip_fields:
        shown = examples.get(tool_id, [])
        one = re.compile(rf"{field} = {QUOTED_IP}")
        several = re.compile(rf"{field} in \({QUOTED_IP}(?:,{QUOTED_IP})+\)")
        assert any(one.fullmatch(example) for example in shown), (tool_id, shown)
        assert any(several.fullmatch(example) for example in shown), (tool_id, shown)


def test_list_assets_shows_how_to_find_an_asset_by_its_ip(manifest: dict[str, Any]) -> None:
    # The form was found on the lab QRadar (T-039 PR): interfaces and ip_addresses are lists of
    # objects, so each level takes contains.
    shown = filter_examples(manifest)["list_assets"]
    path = "interfaces contains ip_addresses contains value"

    assert any(re.fullmatch(rf"{path} = {QUOTED_IP}", example) for example in shown)
    assert any(
        re.fullmatch(rf"{path} in \({QUOTED_IP}(?:,{QUOTED_IP})+\)", example) for example in shown
    )


def test_every_ip_in_the_descriptions_is_a_quoted_documentation_address(
    manifest: dict[str, Any],
) -> None:
    shown = [address for _, text in descriptions(manifest) for address in ip_addresses(text)]

    assert len(shown) >= 8  # the check is not vacuous
    assert manifest_ip_problems(manifest) == []


def test_examples_quote_with_single_quotes(manifest: dict[str, Any]) -> None:
    # A double quote must be escaped in a tool call's JSON arguments; on the lab the model broke
    # its arguments doing that and then left the quotes out (case-28-triage-1).
    for tool_id, shown in filter_examples(manifest).items():
        for example in shown:
            assert '"' not in example, (tool_id, example)


@pytest.mark.parametrize(
    "text",
    [
        "source_ip in (192.0.2.1, 192.0.2.2)",
        "interfaces in (192.0.2.1, 192.0.2.2)",
        "interfaces contains ip_addresses contains value = 192.0.2.1",
        "id in (192.0.2.26, 192.0.2.183)",
        "id = 192.0.2.26 or id = 192.0.2.183",
        "interfaces.source_ip = 192.0.2.101",
        'source_ip in ("192.0.2.1","192.0.2.2")',
        "source_ip = '192.0.2.1",
        "Examples: `source_ip = 192.0.2.1`.",
        "local_destination_ip = 2001:db8::1",
    ],
)
def test_an_unquoted_ip_is_rejected(text: str) -> None:
    assert any(problem.endswith("is not in single quotes") for problem in ip_problems(text))


@pytest.mark.parametrize("text", ["source_ip = '127.0.0.1'", "source_ip = '::1'"])
def test_an_address_outside_the_documentation_ranges_is_rejected(text: str) -> None:
    assert ip_problems(text) == [
        f"{ip_addresses(text)[0]} is not a documentation address (RFC 5737, RFC 3849)"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "source_ip in ('192.0.2.1','192.0.2.2')",
        "source_ip = '198.51.100.7' or source_ip = '203.0.113.9'",
        "source_ip = '192.0.2.0/24'",
        "source_ip = '2001:db8::1'",
        "QRadar 7.6.0 FP1, API version 29.0",
        "... LIMIT 100 START '2026-10-05 10:00:00' STOP '2026-10-05 11:00:00'",
    ],
)
def test_quoted_documentation_addresses_and_other_numbers_pass(text: str) -> None:
    assert ip_problems(text) == []


@pytest.mark.parametrize(
    "where", [("list_source_addresses", FILTER_DESCRIPTION), ("list_assets", "description")]
)
def test_a_manifest_with_an_unquoted_ip_fails_the_check(
    manifest: dict[str, Any], where: tuple[str, str]
) -> None:
    tool_id, place = where
    broken = copy.deepcopy(manifest)
    node = broken["tools"][tool_id]
    *parents, key = place.split(".")
    for parent in parents:
        node = node[parent]
    node[key] += " Example: `source_ip in (192.0.2.1, 192.0.2.2)`."

    assert manifest_ip_problems(broken) == [
        f"tools.{tool_id}.{place}: 192.0.2.1 is not in single quotes",
        f"tools.{tool_id}.{place}: 192.0.2.2 is not in single quotes",
    ]


def test_examples_sit_only_in_filter_descriptions(manifest: dict[str, Any]) -> None:
    # The lab test runs every example, so an example elsewhere would go untested.
    for where, text in descriptions(manifest):
        if where.endswith(f".{FILTER_DESCRIPTION}"):
            assert text.count("`") % 2 == 0, where
        else:
            assert "`" not in text, f"{where}: a filter example belongs in a filter description"


def test_the_lab_test_knows_the_endpoint_of_each_tool_with_examples(
    manifest: dict[str, Any],
) -> None:
    examples = filter_examples(manifest)

    assert {"list_source_addresses", "list_local_destination_addresses", "list_assets"} <= set(
        examples
    )
    assert set(examples) <= set(REST_PATHS)


def test_examples_filter_on_the_fields_their_tool_lists(manifest: dict[str, Any]) -> None:
    for tool_id, shown in filter_examples(manifest).items():
        fields = listed_fields(manifest["tools"][tool_id])
        assert fields, tool_id
        for example in shown:
            assert set(condition_fields(example)) <= fields, (tool_id, example)


@pytest.mark.parametrize(
    ("example", "fields"),
    [
        ("enabled = true and origin = 'USER'", ["enabled", "origin"]),
        (
            "(status = 'OPEN' or status = 'HIDDEN') and magnitude >= 5",
            ["status"] * 2 + ["magnitude"],
        ),
        ("name ilike '%brute force and more%'", ["name"]),
        ('name = "a or b" and id = 1', ["name", "id"]),
        (
            "interfaces contains ip_addresses contains value in ('192.0.2.1','192.0.2.2')",
            ["interfaces"],
        ),
    ],
)
def test_condition_fields(example: str, fields: list[str]) -> None:
    assert condition_fields(example) == fields
