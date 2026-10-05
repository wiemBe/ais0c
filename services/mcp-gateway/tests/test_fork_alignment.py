"""T-040 criterion 3: the registry follows the fork commit that drops the reference filter.

config/connectors/qradar.yaml pins the fork (server_version) and copies the input schemas of its
snapshots, adding the platform's limit to free-form strings. At the commit T-040 pins,
get_reference_table and get_reference_map have no `filter` argument: QRadar 29.0 answers one on
GET /reference_data/tables/{name} with 422, and the map read lost its filter with it (decision
T-34). A call that still sends one is denied by the schema and never reaches the MCP server.

The comparison with the fork's snapshots reads them from the fork's git history at
server_version: the repository at AIS0C_QRADAR_MCP_REPO, or ../qradar-mcp next to this one.
Without it, that comparison is skipped.
"""

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from gateway_support import HUNT_ID, REPO_ROOT, Harness, load_config, tool_result

from ais0c_contracts import ToolResult, ToolStatus
from ais0c_mcp_gateway.registry import Registry

pytestmark = pytest.mark.anyio

REFERENCE_READS = ("get_reference_table", "get_reference_map")
HUNT_PROFILE = "qradar-hunt-read"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
FORK_REPO = Path(os.environ.get("AIS0C_QRADAR_MCP_REPO") or REPO_ROOT.parent / "qradar-mcp")
# The maxLength the manifest gives a free-form string of these tools; JSON Schema keywords that
# pin a string's form, so that it is not free-form.
FREE_TEXT_LIMIT = 1000
_FIXED_FORM = frozenset({"const", "enum", "pattern", "maxLength"})


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return load_config()[0]


def fork_snapshot(commit: str, tool_id: str) -> dict[str, Any]:
    """The fork's snapshot of `tool_id` at `commit` (snapshots/tools/<tool>.json)."""
    git = shutil.which("git")
    if git is None or not (FORK_REPO / ".git").exists():
        pytest.skip(f"no qradar-mcp fork at {FORK_REPO}; set AIS0C_QRADAR_MCP_REPO")
    shown = subprocess.run(  # noqa: S603
        [git, "-C", str(FORK_REPO), "show", f"{commit}:snapshots/tools/{tool_id}.json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, (
        f"the fork at {FORK_REPO} has no snapshot of {tool_id} at {commit}: {shown.stderr.strip()}"
    )
    snapshot: dict[str, Any] = json.loads(shown.stdout)
    return snapshot


def with_platform_limits(fork_schema: dict[str, Any]) -> dict[str, Any]:
    """The fork's input schema as the manifest holds it: free-form strings get a maxLength."""
    schema = copy.deepcopy(fork_schema)
    for spec in schema["properties"].values():
        if spec.get("type") == "string" and not spec.keys() & _FIXED_FORM:
            spec["maxLength"] = FREE_TEXT_LIMIT
    return schema


def test_server_version_is_a_full_commit_sha(manifest: dict[str, Any]) -> None:
    assert FULL_SHA.fullmatch(manifest["server_version"])


@pytest.mark.parametrize("tool_id", REFERENCE_READS)
def test_the_reference_reads_take_no_filter(
    manifest: dict[str, Any], registry: Registry, tool_id: str
) -> None:
    tool = registry.profiles[HUNT_PROFILE].tools[tool_id]

    assert "filter" not in manifest["tools"][tool_id]["input_schema"]["properties"]
    assert "filter" not in tool.entry.inputs


@pytest.mark.parametrize("tool_id", REFERENCE_READS)
def test_the_reference_reads_match_the_forks_snapshot(
    manifest: dict[str, Any], tool_id: str
) -> None:
    snapshot = fork_snapshot(manifest["server_version"], tool_id)

    assert "filter" not in snapshot["inputSchema"]["properties"]
    assert manifest["tools"][tool_id]["input_schema"] == with_platform_limits(
        snapshot["inputSchema"]
    )


async def call_reference_read(
    harness: Harness, tool_id: str, arguments: dict[str, Any]
) -> ToolResult:
    run = await harness.start_run(
        f"run-hunt-{tool_id}", profile=HUNT_PROFILE, case_id=None, hunt_id=HUNT_ID
    )
    intent = harness.intent(HUNT_PROFILE, tool_id, arguments, case_id=None, hunt_id=HUNT_ID)
    async with harness.client() as client:
        return tool_result(await harness.post(client, intent, run_id=run))


@pytest.mark.parametrize("tool_id", REFERENCE_READS)
async def test_a_reference_read_with_a_filter_is_denied(harness: Harness, tool_id: str) -> None:
    result = await call_reference_read(
        harness, tool_id, {"name": "watched_users", "filter": "value = 'x'"}
    )

    assert result.status is ToolStatus.DENIED
    assert result.deny_reason == (
        f"invalid_arguments: unknown argument; {tool_id} takes fields, limit, name, namespace, "
        "offset"
    )
    assert harness.fake.calls == []


@pytest.mark.parametrize("tool_id", REFERENCE_READS)
async def test_the_same_read_without_a_filter_reaches_the_fork(
    harness: Harness, tool_id: str
) -> None:
    result = await call_reference_read(harness, tool_id, {"name": "watched_users"})

    assert result.status is ToolStatus.OK
    assert harness.fake.tool_calls(tool_id) == [{"name": "watched_users"}]
