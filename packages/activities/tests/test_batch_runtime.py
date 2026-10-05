"""Building the batch worker's runtime from the environment (T-022).

The batch worker runs KnowledgeSync's catalog sync with the `qradar-inventory-read` token. A
stand-in gateway serves a profile's tools over HTTP, as `GET /v1/tools` does.
"""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import URL

from ais0c_activities import (
    INVENTORY_PROFILE,
    BatchRuntime,
    CatalogSyncActivities,
    RuntimeConfigError,
    load_batch_runtime,
)
from ais0c_activities.names import SYNC_ANALYSIS_CATALOG
from ais0c_agents import ToolsetProfile, ToolSpec
from ais0c_contracts import CostClass
from ais0c_storage import ConfigurationError

pytestmark = pytest.mark.anyio

TOKEN = "test-inventory-token-0123456789abcdef"  # noqa: S105 - a test value
TOKEN_FILE = f"gateway-token-{INVENTORY_PROFILE}"


def profile(name: str = INVENTORY_PROFILE, *tool_ids: str) -> ToolsetProfile:
    ids = tool_ids or ("list_rules", "get_rule", "list_log_sources", "list_log_source_types")
    return ToolsetProfile(
        name=name,
        connector="qradar",
        tools=tuple(
            ToolSpec(
                id=tool_id,
                description=f"Platform description of {tool_id}.",
                schema_version=f"v-{tool_id}",
                cost_class=CostClass.LOW,
                parameters={"type": "object", "properties": {}},
            )
            for tool_id in ids
        ),
    )


class StubGateway:
    """Serves `served` to the inventory token; records each request."""

    def __init__(self) -> None:
        self.served = profile()
        self.requests: list[tuple[str, str | None]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                authorization = self.headers.get("Authorization")
                stub.requests.append((self.path, authorization))
                if self.path != "/v1/tools" or authorization != f"Bearer {TOKEN}":
                    self.send_error(401)
                    return
                body = stub.served.model_dump_json().encode()
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

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def gateway() -> Iterator[StubGateway]:
    stub = StubGateway()
    yield stub
    stub.close()


@pytest.fixture
def environ(database_url: URL, gateway: StubGateway, tmp_path: Path) -> dict[str, str]:
    (tmp_path / TOKEN_FILE).write_text(TOKEN + "\n", encoding="utf-8")
    return {
        "AIS0C_DATABASE_URL": database_url.render_as_string(hide_password=False),
        "AIS0C_GATEWAY_URL": gateway.url,
        "AIS0C_WORKER_SECRETS_DIR": str(tmp_path),
    }


async def test_the_runtime_syncs_the_catalog_with_the_inventory_token(
    environ: dict[str, str], gateway: StubGateway
) -> None:
    runtime = await load_batch_runtime(environ)
    try:
        assert isinstance(runtime, BatchRuntime)
        assert isinstance(runtime.catalog_sync, CatalogSyncActivities)
        assert [
            getattr(activity, "__temporal_activity_definition").name
            for activity in runtime.activities()
        ] == [SYNC_ANALYSIS_CATALOG]
        # The tools are the gateway's, read with the inventory profile's token.
        assert gateway.requests == [("/v1/tools", f"Bearer {TOKEN}")]
    finally:
        await runtime.close()


async def test_another_profile_stops_the_runtime(
    environ: dict[str, str], gateway: StubGateway
) -> None:
    gateway.served = profile("qradar-triage-read")

    with pytest.raises(RuntimeConfigError, match=r"the gateway serves qradar-triage-read$"):
        await load_batch_runtime(environ)


async def test_a_profile_without_a_list_stops_the_runtime(
    environ: dict[str, str], gateway: StubGateway
) -> None:
    gateway.served = profile(INVENTORY_PROFILE, "list_log_sources", "list_log_source_types")

    with pytest.raises(RuntimeConfigError, match="serves qradar-inventory-read without list_rules"):
        await load_batch_runtime(environ)


@pytest.mark.parametrize(
    ("variable", "error"),
    [("AIS0C_GATEWAY_URL", RuntimeConfigError), ("AIS0C_DATABASE_URL", ConfigurationError)],
)
async def test_a_missing_setting_stops_the_runtime(
    environ: dict[str, str], gateway: StubGateway, variable: str, error: type[Exception]
) -> None:
    del environ[variable]

    with pytest.raises(error, match=f"{variable} is not set"):
        await load_batch_runtime(environ)
    assert gateway.requests == []


async def test_a_missing_token_stops_the_runtime(environ: dict[str, str], tmp_path: Path) -> None:
    (tmp_path / TOKEN_FILE).unlink()

    with pytest.raises(RuntimeConfigError, match=TOKEN_FILE):
        await load_batch_runtime(environ)
