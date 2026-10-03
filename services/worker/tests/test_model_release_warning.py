"""The worker's start-up warning about changed model releases (T-016 criterion 5).

A release in the model registry that differs from the one the alias's last agent run recorded is
a new model release; the worker logs a warning for it when it starts. The registry is the
repository's dev registry; the recorded runs are written through the storage repositories.
"""

import asyncio
import logging
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import URL
from temporalio.testing import WorkflowEnvironment
from worker_support import MODEL_REGISTRY, REPO_ROOT, TRIAGE_PROFILE

from ais0c_activities import SessionFactory, load_model_releases
from ais0c_contracts import AgentTask, Budget, ModelRelease, TimeWindow
from ais0c_storage.repositories import start_agent_run
from ais0c_worker.main import run_case_worker, warn_on_model_release_changes

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
TOKEN = "test-gateway-token-0123456789abcdef"  # noqa: S105 - a test value
RELEASES = load_model_releases(MODEL_REGISTRY)


async def record_run(sessions: SessionFactory, run_id: str, release: ModelRelease) -> None:
    """An agent run of `release`'s alias, recorded as `begin_triage_run` records one."""
    task = AgentTask(
        task_id=run_id,
        parent_run_id="case-run-1",
        case_id="case-7",
        agent_id="triage",
        agent_version="1.0.0",
        objective="Triage QRadar offense 7 (evaluation 1).",
        context_refs=[],
        time_window=TimeWindow(start=T0 - timedelta(hours=1), end=T0),
        budget=Budget(tokens=60000, tool_calls=12, seconds=180),
    )
    async with sessions.begin() as session:
        await start_agent_run(
            session,
            run_id=run_id,
            task=task,
            prompt_version="triage/v1",
            model_alias=release.alias,
            model_target=release.target,
            toolset_profile="qradar-triage-read",
            started_at=T0,
            model_release=release,
        )


def worker_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "ais0c.worker"]


def older(release: ModelRelease) -> ModelRelease:
    """The same model on an older vLLM with another quantization."""
    return ModelRelease.model_validate(
        release.model_dump() | {"engine_version": "0.10.1", "quantization": "awq"}
    )


async def test_a_changed_release_is_logged_as_a_warning(
    sessions: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    await record_run(sessions, "case-7-triage-1", older(RELEASES["soc-fast"]))
    await record_run(sessions, "case-8-triage-1", RELEASES["soc-verifier"])

    with caplog.at_level(logging.WARNING, logger="ais0c.worker"):
        changes = await warn_on_model_release_changes(sessions, RELEASES)

    assert [(change.alias, change.fields) for change in changes] == [
        ("soc-fast", ("quantization", "engine_version"))
    ]
    [record] = worker_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        "model release of soc-fast differs from the one its last agent run recorded "
        "(quantization: 'awq' -> None; engine_version: '0.10.1' -> None); "
        "the model gate must run again (docs/agent-harness.md §5, B2)"
    )


async def test_an_unchanged_or_unused_release_is_not_logged(
    sessions: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    await record_run(sessions, "case-7-triage-1", RELEASES["soc-fast"])

    with caplog.at_level(logging.WARNING, logger="ais0c.worker"):
        changes = await warn_on_model_release_changes(sessions, RELEASES)

    assert changes == []
    assert worker_records(caplog) == []


class StubGateway:
    """Serves the Triage profile's tools to the right token, as `GET /v1/tools` does."""

    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/v1/tools" or self.headers.get("Authorization") != (
                    f"Bearer {TOKEN}"
                ):
                    self.send_error(401)
                    return
                body = TRIAGE_PROFILE.model_dump_json().encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def gateway() -> Iterator[StubGateway]:
    stub = StubGateway()
    yield stub
    stub.close()


async def test_the_worker_warns_when_it_starts(
    env: WorkflowEnvironment,
    sessions: SessionFactory,
    database_url: URL,
    gateway: StubGateway,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`run_case_worker` checks the releases after loading its runtime, before it runs."""
    await record_run(sessions, "case-7-triage-1", older(RELEASES["soc-fast"]))
    (tmp_path / "gateway-token-qradar-triage-read").write_text(TOKEN, encoding="utf-8")
    environ = {
        "AIS0C_DATABASE_URL": database_url.render_as_string(hide_password=False),
        "AIS0C_GATEWAY_URL": gateway.url,
        "AIS0C_WORKER_SECRETS_DIR": str(tmp_path),
        "AIS0C_WORKER_ROOT": str(REPO_ROOT),
        "AIS0C_MODEL_REGISTRY": str(MODEL_REGISTRY.relative_to(REPO_ROOT)),
        "LITELLM_BASE_URL": "http://127.0.0.1:4000",
        "LITELLM_API_KEY": "litellm-test-key",
        "TEMPORAL_ADDRESS": env.client.service_client.config.target_host,
        # The time-skipping test server has no Schedules.
        "AIS0C_INTAKE_SCHEDULE": "off",
    }
    stop = asyncio.Event()
    stop.set()

    with caplog.at_level(logging.INFO, logger="ais0c.worker"):
        await run_case_worker(stop, environ)

    logged = [(record.levelno, record.getMessage()) for record in worker_records(caplog)]
    assert [level for level, _ in logged] == [logging.WARNING, logging.INFO, logging.INFO]
    assert logged[0][1].startswith("model release of soc-fast differs")
    assert [message for _, message in logged[1:]] == ["case worker running", "case worker stopped"]
