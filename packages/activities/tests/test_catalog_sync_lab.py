"""T-022 criterion 7: the lab QRadar's system rules and log sources reach the catalog, with
each rule's enabled state (T-041 criterion 6).

`@pytest.mark.lab`: skipped unless `QRADAR_LAB_URL` and `QRADAR_LAB_TOKEN` are set (see
ais0c_harness.pytest_plugin), and skipped with a reason without the qradar-mcp fork
(`AIS0C_E2E_QRADAR_MCP`, as for tests/e2e). Docker runs the test database, as for every
activity test; no compose stack is needed.

The test starts the fork (`--profile qradar-read`) on this host with the lab token, puts the
real gateway in front of it in process with only `qradar-inventory-read` enabled
(catalog_gateway.py), and runs KnowledgeSync twice on a test Temporal server with the real
catalog sync activity. It then compares the catalog with what the lab QRadar's REST API lists
and prints what the PR reports. Tokens live only in pytest's temporary directory.

    set -a; . ~/.config/ais0c/lab.env; set +a
    AIS0C_E2E_QRADAR_MCP=<fork>/.venv/bin/qradar-mcp-fork \
        uv run pytest packages/activities/tests/test_catalog_sync_lab.py -m lab -s
"""

import json
import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from catalog_gateway import inventory_client
from pydantic import SecretStr
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ais0c_activities import KNOWLEDGE_SYNC_CONTEXT, CatalogSyncActivities, SessionFactory
from ais0c_activities.gateway import utc_now
from ais0c_contracts import CatalogMode, RunStatus
from ais0c_knowledge.catalog import clean_name
from ais0c_mcp_gateway.upstream import McpUpstream
from ais0c_storage.repositories import (
    list_agent_runs,
    list_catalog_log_sources,
    list_catalog_rules,
    list_tool_calls,
)
from ais0c_workflows import BATCH_QUEUE_WORKFLOWS, KnowledgeSync, KnowledgeSyncResult
from ais0c_workflows.names import BATCH_TASK_QUEUE

pytestmark = [pytest.mark.lab, pytest.mark.anyio]

FORK_ENV = "AIS0C_E2E_QRADAR_MCP"
API_VERSION = "29.0"


class Lab:
    def __init__(self) -> None:
        self.host = os.environ["QRADAR_LAB_URL"].strip().removeprefix("https://").rstrip("/")
        self.token = os.environ["QRADAR_LAB_TOKEN"].strip()
        self.verify = os.environ.get("QRADAR_LAB_VERIFY_SSL", "true").strip().lower() != "false"

    async def get(self, path: str, fields: str) -> list[dict[str, object]]:
        """A list from the lab's REST API, for the test's own comparison only."""
        async with httpx2.AsyncClient(
            base_url=f"https://{self.host}/api", verify=self.verify, timeout=60
        ) as client:
            response = await client.get(
                path,
                params={"fields": fields},
                headers={"SEC": self.token, "Version": API_VERSION, "Accept": "application/json"},
            )
        response.raise_for_status()
        rows = response.json()
        assert isinstance(rows, list)
        return [row for row in rows if isinstance(row, dict)]


@pytest.fixture
def fork_url(tmp_path: Path) -> Iterator[tuple[str, str]]:
    """The fork on a free local port; yields its MCP endpoint and its bearer token."""
    command = os.environ.get(FORK_ENV, "").strip()
    if not command:
        pytest.skip(f"lab test: set {FORK_ENV} to the qradar-mcp fork's executable")
    lab = Lab()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(mode=0o700)
    mcp_token = secrets.token_urlsafe(32)
    for name, value in (("qradar-token", lab.token), ("mcp-token", mcp_token)):
        (secrets_dir / name).write_text(value + "\n", encoding="utf-8")
        (secrets_dir / name).chmod(0o600)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    log = (tmp_path / "qradar-mcp.log").open("ab")
    process = subprocess.Popen(  # noqa: S603 - the fork the user points to
        [command, "--profile", "qradar-read", "--host", "127.0.0.1", "--port", str(port)],
        env={
            **os.environ,
            "QRADAR_CONSOLE_FQDN": lab.host,
            "QRADAR_AUTH_TOKEN_FILE": str(secrets_dir / "qradar-token"),
            "MCP_AUTH_TOKEN_FILE": str(secrets_dir / "mcp-token"),
            "QRADAR_VERIFY_SSL": "true" if lab.verify else "false",
        },
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_healthy(process, f"http://127.0.0.1:{port}/healthz", tmp_path / "qradar-mcp.log")
        yield f"http://127.0.0.1:{port}/mcp", mcp_token
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


def _wait_healthy(process: subprocess.Popen[bytes], url: str, log: Path) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"the qradar-mcp fork exited; see {log}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    pytest.fail(f"the qradar-mcp fork did not answer {url}; see {log}")


async def test_the_lab_rules_and_log_sources_reach_the_catalog(
    sessions: SessionFactory, fork_url: tuple[str, str]
) -> None:
    endpoint, mcp_token = fork_url
    upstream = McpUpstream(endpoint, SecretStr(mcp_token), timeout_seconds=60)
    gateway, profile = await inventory_client(sessions, upstream, now=utc_now)
    activities = CatalogSyncActivities(sessions=sessions, gateway=gateway, profile=profile)

    started = time.monotonic()
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue=BATCH_TASK_QUEUE,
            workflows=list(BATCH_QUEUE_WORKFLOWS),
            activities=activities.activities(),
        ),
    ):
        first: KnowledgeSyncResult = await env.client.execute_workflow(
            KnowledgeSync.run, id="knowledge-sync-lab-1", task_queue=BATCH_TASK_QUEUE
        )
        took = time.monotonic() - started
        second: KnowledgeSyncResult = await env.client.execute_workflow(
            KnowledgeSync.run, id="knowledge-sync-lab-2", task_queue=BATCH_TASK_QUEUE
        )

    lab = Lab()
    rules = await lab.get("analytics/rules", "id,name,origin,enabled")
    sources = await lab.get(
        "config/event_sources/log_source_management/log_sources", "id,name,type_id"
    )
    types = await lab.get("config/event_sources/log_source_management/log_source_types", "id,name")
    type_names = {row["id"]: clean_name(str(row["name"])) for row in types}
    async with sessions() as session:
        catalog_rules = await list_catalog_rules(session)
        catalog_sources = await list_catalog_log_sources(session)
        runs = await list_agent_runs(session, case_id=KNOWLEDGE_SYNC_CONTEXT)
        calls = [call for run in runs for call in await list_tool_calls(session, run.run_id)]

    # Every rule the lab lists, its system rules among them, is in the catalog, undefined.
    assert {row.rule_id: row.rule_name for row in catalog_rules} == {
        row["id"]: clean_name(str(row["name"])) for row in rules
    }
    system_rules = {row["id"] for row in rules if row["origin"] == "SYSTEM"}
    assert system_rules
    assert {(row.defined, row.mode) for row in catalog_rules} == {(False, CatalogMode.ANALYZE)}
    # Each rule's enabled state is QRadar's (T-37); a complete read marks nothing missing.
    assert {row.rule_id: row.qradar_enabled for row in catalog_rules} == {
        row["id"]: row["enabled"] for row in rules
    }
    disabled_rules = sum(1 for row in rules if row["enabled"] is False)
    assert [row for row in [*catalog_rules, *catalog_sources] if row.missing_since] == []
    # Every log source, with the name of its type.
    assert {row.log_source_id: (row.name, row.type_name) for row in catalog_sources} == {
        row["id"]: (clean_name(str(row["name"])), type_names[row["type_id"]]) for row in sources
    }
    assert {(row.defined, row.in_scope) for row in catalog_sources} == {(False, True)}
    # The second run read QRadar again and changed nothing.
    assert first.catalog["rules_added"] == len(rules)
    assert first.catalog["log_sources_added"] == len(sources)
    assert second.catalog == {
        **first.catalog,
        "rules_added": 0,
        "log_sources_added": 0,
    }
    # Both runs are recorded runs of the pseudo agent, every call allowed.
    assert [run.status for run in runs] == [RunStatus.COMPLETED] * 2
    assert {call.policy_decision.value for call in calls} == {"allow"}

    print(
        "\n"
        + json.dumps(
            {
                "first_run": first.catalog,
                "second_run": second.catalog,
                "system_rules": len(system_rules),
                "disabled_rules": disabled_rules,
                "log_source_types": len(types),
                "gateway_calls_per_run": [run.tool_calls for run in runs],
                "first_run_seconds": round(took, 1),
            },
            indent=2,
        )
    )
