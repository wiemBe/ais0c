"""T-037: the batch worker of the `soc-batch` queue and the Schedule that starts KnowledgeSync.

T-022 built the workflow, the Schedule and the activity; these tests are about the wiring to a
process:

- `run_batch_worker` runs KnowledgeSync with the real catalog sync activity on the real
  database, over a stand-in gateway that answers the inventory reads (criterion 1);
- the process `python -m ais0c_worker batch` does the same and stops on SIGINT or SIGTERM, while
  no command is the case worker still (criterion 1);
- the process creates or updates the Schedule at start-up, keeps its state and leaves it alone
  when `AIS0C_KNOWLEDGE_SYNC_SCHEDULE=off` (criterion 2);
- a missing inventory token, or another profile at the gateway, stops the worker with a
  `RuntimeConfigError` (criterion 3).

The time-skipping test server has no Schedules, so the process tests use Temporal's local
development server in real time; the runs are triggered by hand. The dev stack's own gateway is
`test_batch_worker_dev_stack.py` (criterion 4).
"""

import asyncio
import logging
import signal
from collections.abc import Iterator
from pathlib import Path

import pytest
from batch_support import (
    FORTIGATE,
    LOG_SOURCES,
    RULES,
    RUNNING,
    STOPPED,
    TOKEN,
    TOKEN_FILE,
    WINDOWS_SECURITY,
    StubGateway,
    WorkerProcess,
    batch_environ,
    delete_schedule,
    inventory_profile,
    schedule_of,
    temporal_address,
    until_started,
)
from sqlalchemy import URL
from temporalio.client import (
    Client,
    ScheduleActionStartWorkflow,
    ScheduleOverlapPolicy,
    ScheduleRange,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment

from ais0c_activities import BatchRuntime, CatalogSyncActivities, RuntimeConfigError, SessionFactory
from ais0c_agents import FakeGatewayClient
from ais0c_contracts import CatalogMode
from ais0c_storage.repositories import list_catalog_log_sources, list_catalog_rules
from ais0c_worker import build_batch_worker, connect
from ais0c_worker.main import (
    BATCH_COMMAND,
    EXECUTOR_COMMAND,
    EXIT_CONFIG_ERROR,
    main,
    run_batch_worker,
)
from ais0c_workflows import KnowledgeSync, KnowledgeSyncResult
from ais0c_workflows.names import BATCH_TASK_QUEUE, KNOWLEDGE_SYNC, KNOWLEDGE_SYNC_SCHEDULE_ID
from ais0c_workflows.schedules import SOC_TIME_ZONE

pytestmark = pytest.mark.anyio

# What the first sync of the stand-in gateway's inventory reports.
FIRST_COUNTS: dict[str, int] = {
    "rules": len(RULES),
    "rules_added": len(RULES),
    "log_sources": len(LOG_SOURCES),
    "log_sources_added": len(LOG_SOURCES),
    "log_sources_untyped": 0,
}
CATALOG_RULES: dict[int, tuple[str, bool]] = {
    100001: ("AIS0C TEST - Excessive Firewall Accepts", True),
    100002: ("AIS0C TEST - Repeated Logon Failures", False),
    100003: ("AIS0C TEST - Outbound Connection to a Rare Domain", True),
}
CATALOG_LOG_SOURCES: dict[int, tuple[str, str]] = {
    2001: ("SRV-0001.example.com", WINDOWS_SECURITY),
    2002: ("SRV-0002.example.com", FORTIGATE),
}


@pytest.fixture
def gateway() -> Iterator[StubGateway]:
    stub = StubGateway()
    try:
        yield stub
    finally:
        stub.close()


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    """The directory `load_batch_runtime` reads the inventory token from."""
    (tmp_path / TOKEN_FILE).write_text(TOKEN + "\n", encoding="utf-8")
    return tmp_path


def environ(
    database_url: URL, gateway: StubGateway, secrets_dir: Path, **extra: str
) -> dict[str, str]:
    """The batch worker's environment: the test database, the stand-in gateway, its token."""
    return batch_environ(
        gateway, database_url.render_as_string(hide_password=False), secrets_dir, **extra
    )


def worker_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "ais0c.worker"]


# --- criterion 1: the worker runs KnowledgeSync ------------------------------------------------


async def test_the_batch_worker_syncs_the_catalog(
    env: WorkflowEnvironment,
    sessions: SessionFactory,
    database_url: URL,
    gateway: StubGateway,
    secrets_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Criterion 1: the `soc-batch` queue, the workflow of T-022 and its catalog sync activity,
    with the runtime `load_batch_runtime` builds. Temporal keeps the workflow queued until the
    worker polls, so the test need not wait for the worker to be ready."""
    stop = asyncio.Event()
    running = asyncio.create_task(
        run_batch_worker(
            stop,
            environ(
                database_url,
                gateway,
                secrets_dir,
                TEMPORAL_ADDRESS=temporal_address(env),
                # The time-skipping test server has no Schedules.
                AIS0C_KNOWLEDGE_SYNC_SCHEDULE="off",
            ),
        )
    )
    try:
        with caplog.at_level(logging.INFO, logger="ais0c.worker"):
            handle = await env.client.start_workflow(
                KnowledgeSync.run, id="knowledge-sync-worker", task_queue=BATCH_TASK_QUEUE
            )
            result: KnowledgeSyncResult = await handle.result()
            stop.set()
            await running
    finally:
        stop.set()
        await running

    assert {key: result.catalog[key] for key in FIRST_COUNTS} == FIRST_COUNTS
    assert result.catalog["rules_missing"] == result.catalog["log_sources_missing"] == 0
    async with sessions() as session:
        rules = await list_catalog_rules(session)
        sources = await list_catalog_log_sources(session)
    assert {row.rule_id: (row.rule_name, row.qradar_enabled) for row in rules} == CATALOG_RULES
    assert {row.log_source_id: (row.name, row.type_name) for row in sources} == CATALOG_LOG_SOURCES
    assert {(row.defined, row.mode) for row in rules} == {(False, CatalogMode.ANALYZE)}
    assert [row.missing_since for row in rules] == [None] * len(rules)
    # QRadar's three lists, one page each, read through the gateway with the inventory token.
    assert [tool_id for tool_id, _ in gateway.calls] == [
        "list_rules",
        "list_log_sources",
        "list_log_source_types",
    ]
    assert gateway.calls_of("list_log_sources")[0]["sort"] == "+id"
    assert worker_messages(caplog) == [RUNNING, STOPPED]


async def test_the_batch_worker_needs_pydantic_ais_plugin(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """The run's result is a Pydantic model; a client without the plugin cannot carry it."""
    plain = Client(
        env.client.service_client,
        namespace=env.client.namespace,
        data_converter=pydantic_data_converter,
    )
    runtime = BatchRuntime(
        sessions=sessions,
        catalog_sync=CatalogSyncActivities(
            sessions=sessions, gateway=FakeGatewayClient({}), profile=inventory_profile()
        ),
    )

    with pytest.raises(ValueError, match="Pydantic AI's plugin"):
        build_batch_worker(plain, runtime)


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
async def test_the_batch_worker_process_syncs_and_stops_on_a_signal(
    dev_server: WorkflowEnvironment,
    database_url: URL,
    gateway: StubGateway,
    secrets_dir: Path,
    tmp_path: Path,
    number: signal.Signals,
) -> None:
    """Criterion 1: the process itself, with its Schedule left to the worker that owns it."""
    worker = WorkerProcess(
        "batch-worker-signal",
        environ(
            database_url,
            gateway,
            secrets_dir,
            TEMPORAL_ADDRESS=temporal_address(dev_server),
            AIS0C_KNOWLEDGE_SYNC_SCHEDULE="off",
        ),
        tmp_path,
        BATCH_COMMAND,
    )
    try:
        await until_started(worker)
        handle = await dev_server.client.start_workflow(
            KnowledgeSync.run, id="knowledge-sync-process", task_queue=BATCH_TASK_QUEUE
        )
        result: KnowledgeSyncResult = await handle.result()
        status = worker.signal_and_wait(number)
    finally:
        worker.stop()

    assert {key: result.catalog[key] for key in FIRST_COUNTS} == FIRST_COUNTS
    assert status == 0
    assert worker.messages()[-1] == STOPPED


# --- criterion 2: the Schedule -----------------------------------------------------------------


async def test_the_worker_creates_the_schedule_and_a_second_one_keeps_its_state(
    dev_server: WorkflowEnvironment,
    database_url: URL,
    gateway: StubGateway,
    secrets_dir: Path,
    tmp_path: Path,
) -> None:
    """Criterion 2: the Schedule of `knowledge_sync_schedule()` is created at start-up, a second
    start-up updates it in place, and a paused Schedule stays paused."""
    client = await connect(temporal_address(dev_server))
    await delete_schedule(client)
    worker_environ = environ(
        database_url, gateway, secrets_dir, TEMPORAL_ADDRESS=temporal_address(dev_server)
    )
    first = WorkerProcess("batch-worker-1", worker_environ, tmp_path, BATCH_COMMAND)
    second: WorkerProcess | None = None
    try:
        await until_started(first)
        handle = client.get_schedule_handle(KNOWLEDGE_SYNC_SCHEDULE_ID)
        schedule = (await schedule_of(client)).schedule
        assert schedule.spec.calendars is not None
        # 03:00 every day, SOC time, as `knowledge_sync_schedule()` says it.
        [calendar] = schedule.spec.calendars
        assert list(calendar.hour) == [ScheduleRange(start=3, end=3, step=1)]
        assert (list(calendar.minute), list(calendar.second)) == (
            [ScheduleRange(start=0, end=0, step=1)],
            [ScheduleRange(start=0, end=0, step=1)],
        )
        assert schedule.spec.time_zone_name == SOC_TIME_ZONE
        assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
        action = schedule.action
        assert isinstance(action, ScheduleActionStartWorkflow)
        assert (action.workflow, action.task_queue) == (KNOWLEDGE_SYNC, BATCH_TASK_QUEUE)

        await handle.pause()
        second = WorkerProcess("batch-worker-2", worker_environ, tmp_path, BATCH_COMMAND)
        await until_started(second)
        assert second.messages() == [RUNNING]
        assert (await handle.describe()).schedule.state.paused is True
    finally:
        if second is not None:
            second.stop()
        first.stop()
        await delete_schedule(client)


async def test_the_worker_leaves_the_schedule_alone_when_it_is_off(
    dev_server: WorkflowEnvironment,
    database_url: URL,
    gateway: StubGateway,
    secrets_dir: Path,
    tmp_path: Path,
) -> None:
    """Criterion 2: `AIS0C_KNOWLEDGE_SYNC_SCHEDULE=off`, as on a second batch worker."""
    client = await connect(temporal_address(dev_server))
    await delete_schedule(client)
    worker = WorkerProcess(
        "batch-worker-off",
        environ(
            database_url,
            gateway,
            secrets_dir,
            TEMPORAL_ADDRESS=temporal_address(dev_server),
            AIS0C_KNOWLEDGE_SYNC_SCHEDULE="off",
        ),
        tmp_path,
        BATCH_COMMAND,
    )
    try:
        await until_started(worker)
        with pytest.raises(RPCError) as error:
            await client.get_schedule_handle(KNOWLEDGE_SYNC_SCHEDULE_ID).describe()
        assert error.value.status is RPCStatusCode.NOT_FOUND
    finally:
        worker.stop()
        await delete_schedule(client)


# --- criterion 3: the start-up check -------------------------------------------------------------


async def test_the_worker_stops_without_the_inventory_token(
    database_url: URL, gateway: StubGateway, secrets_dir: Path
) -> None:
    (secrets_dir / TOKEN_FILE).unlink()

    with pytest.raises(RuntimeConfigError, match=TOKEN_FILE):
        await run_batch_worker(asyncio.Event(), environ(database_url, gateway, secrets_dir))
    assert gateway.calls == []


async def test_the_worker_stops_when_the_gateway_serves_another_profile(
    database_url: URL, gateway: StubGateway, secrets_dir: Path
) -> None:
    gateway.served = inventory_profile("qradar-triage-read")

    with pytest.raises(RuntimeConfigError, match=r"the gateway serves qradar-triage-read$"):
        await run_batch_worker(asyncio.Event(), environ(database_url, gateway, secrets_dir))


def test_the_command_line_names_the_worker_and_reports_a_bad_start_up(
    database_url: URL,
    gateway: StubGateway,
    secrets_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No command names the case worker; `batch` selects the separate batch worker."""
    worker_environ = environ(database_url, gateway, secrets_dir)

    assert main([], worker_environ) == EXIT_CONFIG_ERROR
    assert "AIS0C_MODEL_REGISTRY is not set" in capsys.readouterr().err

    gateway.served = inventory_profile("qradar-hunt-read")
    assert main([BATCH_COMMAND], worker_environ) == EXIT_CONFIG_ERROR
    error = capsys.readouterr().err
    assert error.startswith("error: the catalog sync needs qradar-inventory-read")
    assert "the gateway serves qradar-hunt-read" in error

    # T-045: `executor` selects the executor worker, which stops without its own secrets.
    assert main([EXECUTOR_COMMAND], worker_environ) == EXIT_CONFIG_ERROR
    error = capsys.readouterr().err
    assert "gateway-token-qradar-note-write" in error


def test_an_unknown_command_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["hunt"], {})

    assert raised.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
