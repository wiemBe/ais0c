"""T-039 criterion 3: the filter examples in the QRadar tool descriptions work on the lab QRadar.

`@pytest.mark.lab`: skipped unless QRADAR_LAB_URL and QRADAR_LAB_TOKEN are set (see
ais0c_harness.pytest_plugin). No fork, gateway or database is needed. Each example goes to the
REST endpoint its tool reads (filter_examples.REST_PATHS) as a read-only GET for at most one row;
QRadar answers 200 for a filter it accepts and 422 for one it cannot use. Addresses read from
the lab go only into filters and are never printed.

    set -a; . ~/.config/ais0c/lab.env; set +a
    uv run pytest services/mcp-gateway/tests/test_filter_examples_lab.py -m lab -v
"""

import os
from collections.abc import Callable, Iterator
from typing import Any

import httpx2
import pytest
from filter_examples import REST_PATHS, filter_examples, ip_addresses
from gateway_support import load_config

pytestmark = pytest.mark.lab

API_VERSION = "29.0"
EXAMPLES = [
    (tool_id, example)
    for tool_id, shown in filter_examples(load_config()[0]).items()
    for example in shown
]
IP_EXAMPLES = [(tool_id, example) for tool_id, example in EXAMPLES if ip_addresses(example)]
# Filters the Triage agent sent in the lab runs of 2026-10-05, and two fields QRadar does not
# filter on (the list_offenses description says so). QRadar refuses each with 422.
REFUSED = [
    ("list_source_addresses", "source_ip in (192.0.2.1, 192.0.2.2)"),
    ("list_source_addresses", "id in (192.0.2.1, 192.0.2.2)"),
    ("list_assets", "interfaces in (192.0.2.1, 192.0.2.2)"),
    ("list_assets", "interfaces in ('192.0.2.1', '192.0.2.2')"),
    ("list_assets", "interfaces.source_ip = 192.0.2.1"),
    ("list_assets", "interfaces contains ip_addresses contains value = 192.0.2.1"),
    ("list_assets", "id = 192.0.2.1"),
    ("list_assets", "hostnames = 'host01.example.com'"),
    ("list_offenses", "offense_source = '192.0.2.1'"),
    ("list_offenses", "description = 'DCSync'"),
]
# How to read the IP addresses of one row of each tool whose examples hold an address.
ADDRESSES: dict[str, tuple[str, Callable[[dict[str, Any]], list[str]]]] = {
    "list_source_addresses": ("id,source_ip", lambda row: [row["source_ip"]]),
    "list_local_destination_addresses": (
        "id,local_destination_ip",
        lambda row: [row["local_destination_ip"]],
    ),
    "list_assets": (
        "id,interfaces(ip_addresses(value))",
        lambda row: [
            address["value"]
            for interface in row.get("interfaces", [])
            for address in interface.get("ip_addresses", [])
        ],
    ),
}


@pytest.fixture(scope="module")
def lab() -> Iterator[httpx2.Client]:
    host = os.environ["QRADAR_LAB_URL"].strip().removeprefix("https://").rstrip("/")
    verify = os.environ.get("QRADAR_LAB_VERIFY_SSL", "true").strip().lower() != "false"
    with httpx2.Client(
        base_url=f"https://{host}/api",
        verify=verify,
        timeout=60,
        headers={
            "SEC": os.environ["QRADAR_LAB_TOKEN"].strip(),
            "Version": API_VERSION,
            "Accept": "application/json",
        },
    ) as client:
        yield client


def read(
    lab: httpx2.Client,
    tool_id: str,
    filter_: str | None,
    *,
    fields: str | None = None,
    rows: int = 1,
) -> httpx2.Response:
    params = {
        name: value
        for name, value in (("filter", filter_), ("fields", fields))
        if value is not None
    }
    return lab.get(REST_PATHS[tool_id], params=params, headers={"Range": f"items=0-{rows - 1}"})


def _ids(cases: list[tuple[str, str]]) -> list[str]:
    return [f"{tool_id}: {example}" for tool_id, example in cases]


@pytest.mark.parametrize(("tool_id", "example"), EXAMPLES, ids=_ids(EXAMPLES))
def test_each_filter_example_is_accepted(lab: httpx2.Client, tool_id: str, example: str) -> None:
    response = read(lab, tool_id, example)

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(("tool_id", "bad"), REFUSED, ids=_ids(REFUSED))
def test_the_filters_of_the_failed_calls_are_refused(
    lab: httpx2.Client, tool_id: str, bad: str
) -> None:
    # A 200 above means something only if QRadar refuses what the descriptions warn against.
    assert read(lab, tool_id, bad).status_code == 422


@pytest.mark.parametrize(("tool_id", "example"), IP_EXAMPLES, ids=_ids(IP_EXAMPLES))
def test_an_ip_example_finds_the_row_of_a_real_address(
    lab: httpx2.Client, tool_id: str, example: str
) -> None:
    assert tool_id in ADDRESSES, f"{tool_id}: say how to read the addresses of its rows"
    fields, addresses_of = ADDRESSES[tool_id]
    listed = read(lab, tool_id, None, fields=fields, rows=50)
    listed.raise_for_status()
    found = next(
        ((row["id"], address) for row in listed.json() for address in addresses_of(row)), None
    )
    if found is None:
        pytest.skip(f"the lab has no {tool_id} row with an IP address")
    row_id, address = found

    real = example.replace(ip_addresses(example)[0], address, 1)
    response = read(lab, tool_id, real, fields="id", rows=50)

    assert response.status_code == 200
    assert row_id in [row["id"] for row in response.json()]
