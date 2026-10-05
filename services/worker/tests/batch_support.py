"""Support for the batch worker tests: a stand-in gateway that answers the inventory reads, the
worker's environment and the worker process itself.

The gateway has the HTTP shape of the MCP Policy Gateway: `GET /v1/tools` gives the token's
profile and `POST /v1/tool-calls` answers a ToolIntent with a ToolResult. Only the reads are
stand-ins; the inventory, the catalog sync, the workflow and the worker are the repository's own.
The real gateway comes from the dev stack, in `test_batch_worker_dev_stack.py` (T-037 criterion 4).

The data is synthetic: made-up rule and log source names, `example.com` hosts, and log source
types of the kind a QRadar installation holds.
"""

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Final

from pydantic import JsonValue
from temporalio.client import Client, ScheduleDescription
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from worker_support import eventually

from ais0c_activities import INVENTORY_PROFILE
from ais0c_agents import ToolsetProfile, ToolSpec
from ais0c_contracts import (
    CostClass,
    ToolCoverage,
    ToolIntent,
    ToolResult,
    ToolStatus,
)
from ais0c_workflows.names import KNOWLEDGE_SYNC_SCHEDULE_ID

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
TOKEN: Final = "batch-worker-test-token-0123456789abcdef"  # noqa: S105 - a test value
TOKEN_FILE: Final = f"gateway-token-{INVENTORY_PROFILE}"
TOOLS_PATH: Final = "/v1/tools"
TOOL_CALLS_PATH: Final = "/v1/tool-calls"
LOG_PREFIX: Final = "ais0c.worker: "
# What the worker's own logger says when it polls its queue and when it stops.
RUNNING: Final = "batch worker running"
STOPPED: Final = "batch worker stopped"
# Long enough for a worker process to import, load its runtime and start polling.
STARTUP_SECONDS: Final = 90.0

# The tools of `qradar-inventory-read` (config/connectors/qradar.yaml); the sync reads three.
INVENTORY_TOOL_IDS: Final = (
    "list_rules",
    "get_rule",
    "list_log_sources",
    "get_log_source",
    "list_log_source_types",
)
WINDOWS_SECURITY: Final = "Microsoft Windows Security Event Log"
FORTIGATE: Final = "Fortinet FortiGate Security Gateway"

type Row = dict[str, JsonValue]

RULES: Final[tuple[Row, ...]] = (
    {"id": 100001, "name": "AIS0C TEST - Excessive Firewall Accepts", "enabled": True},
    {"id": 100002, "name": "AIS0C TEST - Repeated Logon Failures", "enabled": False},
    {"id": 100003, "name": "AIS0C TEST - Outbound Connection to a Rare Domain", "enabled": True},
)
LOG_SOURCES: Final[tuple[Row, ...]] = (
    {"id": 2001, "name": "SRV-0001.example.com", "type_id": 12},
    {"id": 2002, "name": "SRV-0002.example.com", "type_id": 73},
)
LOG_SOURCE_TYPES: Final[tuple[Row, ...]] = (
    {"id": 12, "name": WINDOWS_SECURITY},
    {"id": 73, "name": FORTIGATE},
)
LISTS: Final[Mapping[str, tuple[Row, ...]]] = {
    "list_rules": RULES,
    "list_log_sources": LOG_SOURCES,
    "list_log_source_types": LOG_SOURCE_TYPES,
}


def inventory_profile(name: str = INVENTORY_PROFILE, *tool_ids: str) -> ToolsetProfile:
    """The profile `name` with the inventory tools, as `GET /v1/tools` serves it."""
    return ToolsetProfile(
        name=name,
        connector="qradar",
        tools=tuple(
            ToolSpec(
                id=tool_id,
                description=f"Platform description of {tool_id}.",
                schema_version=f"v-{tool_id}",
                cost_class=CostClass.LOW,
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            )
            for tool_id in (tool_ids or INVENTORY_TOOL_IDS)
        ),
    )


class StubGateway:
    """The gateway's two paths for the inventory token; records every tool call it answers.

    `served` is what `GET /v1/tools` returns, so a test can hand out another profile.
    """

    def __init__(self) -> None:
        self.served = inventory_profile()
        self.calls: list[tuple[str, dict[str, JsonValue]]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                if self.path != TOOLS_PATH or not self._authorized():
                    self.send_error(401)
                    return
                self._reply(stub.served.model_dump_json().encode())

            def do_POST(self) -> None:
                if self.path != TOOL_CALLS_PATH or not self._authorized():
                    self.send_error(401)
                    return
                intent = ToolIntent.model_validate_json(self.rfile.read(self._length()))
                stub.calls.append((intent.tool_id, intent.arguments))
                self._reply(stub.answer(intent).model_dump_json().encode())

            def _authorized(self) -> bool:
                return self.headers.get("Authorization") == f"Bearer {TOKEN}"

            def _length(self) -> int:
                return int(self.headers.get("Content-Length", "0"))

            def _reply(self, body: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def answer(self, intent: ToolIntent) -> ToolResult:
        """The page of a list the intent asks for, as the fork answers it: QRadar's rows for
        `limit` and `offset`, with `fields` and `sort` applied."""
        rows = list(LISTS.get(intent.tool_id, ()))
        if intent.arguments.get("sort") == "+id":
            rows.sort(key=lambda row: _as_int(row.get("id", 0)))
        fields = intent.arguments.get("fields")
        wanted = (
            [name.strip() for name in fields.split(",")]
            if isinstance(fields, str)
            else list(rows[0])
            if rows
            else []
        )
        offset = _as_int(intent.arguments.get("offset", 0))
        limit = _as_int(intent.arguments.get("limit", 50))
        page = [
            {name: row[name] for name in wanted if name in row}
            for row in rows[offset : offset + limit]
        ]
        return ToolResult(
            status=ToolStatus.OK,
            # An EvidenceId the gateway would issue (`^ev_\S+$`), one per run and tool.
            evidence_id=f"ev_{intent.run_id[:12]}_{intent.tool_id}",
            data=page,
            truncated=False,
            coverage=ToolCoverage(complete=True, gaps=[]),
        )

    def calls_of(self, tool_id: str) -> list[dict[str, JsonValue]]:
        """The arguments of every call of `tool_id`, in order."""
        return [arguments for called, arguments in self.calls if called == tool_id]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _as_int(value: JsonValue) -> int:
    assert isinstance(value, int)
    assert not isinstance(value, bool)
    return value


def batch_environ(
    gateway: StubGateway, database_url: str, secrets_dir: Path, **extra: str
) -> dict[str, str]:
    """The batch worker's environment; `extra` adds a setting or overrides one."""
    return {
        "AIS0C_DATABASE_URL": database_url,
        "AIS0C_GATEWAY_URL": gateway.url,
        "AIS0C_WORKER_SECRETS_DIR": str(secrets_dir),
        **extra,
    }


class WorkerProcess:
    """`python -m ais0c_worker batch` on this host, its output in a log file next to the test's
    data."""

    def __init__(
        self, name: str, environ: Mapping[str, str], log_dir: Path, *arguments: str
    ) -> None:
        self.name = name
        self._log_path = log_dir / f"{name}.log"
        self._log: IO[bytes] = self._log_path.open("ab")
        self._process = subprocess.Popen(  # noqa: S603 - the repository's own worker
            [sys.executable, "-m", "ais0c_worker", *arguments],
            env={**os.environ, **environ},
            stdout=self._log,
            stderr=subprocess.STDOUT,
            cwd=REPO_ROOT,
        )

    @property
    def log_path(self) -> Path:
        return self._log_path

    def running(self) -> bool:
        return self._process.poll() is None

    def signal_and_wait(self, number: signal.Signals, *, timeout: float = 60.0) -> int:
        """Send `number` and wait for the exit status; a worker that keeps running fails."""
        try:
            self._process.send_signal(number)
            return self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=30)
            raise AssertionError(
                f"{self.name} ignored {number.name}; see {self._log_path}"
            ) from None
        finally:
            self._log.close()

    def stop(self) -> None:
        """SIGTERM the worker if it is still running, as a test's clean-up."""
        if self.running():
            self.signal_and_wait(signal.SIGTERM)
        else:
            self._log.close()

    def messages(self) -> list[str]:
        """What the `ais0c.worker` logger logged, without its timestamps and levels."""
        log = self._log_path.read_text(encoding="utf-8", errors="replace")
        return [line.split(LOG_PREFIX, 1)[1] for line in log.splitlines() if LOG_PREFIX in line]


async def until_started(worker: WorkerProcess, *, seconds: float = STARTUP_SECONDS) -> None:
    """Wait until the worker process polls its queue, which it logs; fail if it stopped before."""

    async def running() -> bool | None:
        if not worker.running():
            raise AssertionError(f"{worker.name} stopped before it ran; see {worker.log_path}")
        return True if RUNNING in worker.messages() else None

    await eventually(running, seconds)


def temporal_address(env: WorkflowEnvironment) -> str:
    """Where a Temporal test server listens, for a worker on this host."""
    return str(env.client.service_client.config.target_host)


async def schedule_of(
    client: Client, schedule_id: str = KNOWLEDGE_SYNC_SCHEDULE_ID
) -> ScheduleDescription:
    """The Schedule as Temporal holds it, once it exists."""

    async def described() -> ScheduleDescription | None:
        try:
            return await client.get_schedule_handle(schedule_id).describe()
        except RPCError as error:
            if error.status is RPCStatusCode.NOT_FOUND:
                return None
            raise

    return await eventually(described, STARTUP_SECONDS)


async def delete_schedule(client: Client, schedule_id: str = KNOWLEDGE_SYNC_SCHEDULE_ID) -> None:
    """Delete the Schedule if it is there; a knowledge sync Schedule has no checkpoint."""
    try:
        await client.get_schedule_handle(schedule_id).delete()
    except RPCError as error:
        if error.status is not RPCStatusCode.NOT_FOUND:
            raise
