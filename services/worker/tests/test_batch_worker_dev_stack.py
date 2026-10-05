"""T-037 criterion 4: the batch worker against the dev stack, KnowledgeSync end to end.

Opt-in, because it needs the running stack with the `qradar` profile (T-018), whose gateway
reaches the lab QRadar. From the repository root:

    docker compose -f deploy/compose/docker-compose.dev.yaml --profile qradar up -d --wait
    set -a; . ~/.config/ais0c/lab.env; set +a
    AIS0C_QRADAR_READ_TOKEN="$QRADAR_LAB_TOKEN" AIS0C_QRADAR_NOTE_TOKEN="$QRADAR_LAB_TOKEN" \\
      uv run python deploy/compose/make_secrets.py
    AIS0C_DATABASE_URL="postgresql+psycopg://ais0c:${AIS0C_DB_PASSWORD}@127.0.0.1:5432/ais0c" \\
      AIS0C_DEV_STACK=1 uv run pytest services/worker/tests/test_batch_worker_dev_stack.py -s

The stack's gateway serves `qradar-inventory-read` to the token of
`deploy/compose/secrets/agents/gateway-token-qradar-inventory-read`, which `make_secrets.py`
writes and the worker reads. `AIS0C_GATEWAY_URL` and `TEMPORAL_ADDRESS` point the test at another
stack.

The test starts `python -m ais0c_worker batch`, waits for the Schedule it creates, triggers that
by hand, waits for the run's result and compares the catalog with what the result reports. It
only reads: it opens, closes and writes nothing in the lab, and the sync is the only writer. The
report it prints is what the PR reports.
"""

import json
import os
from pathlib import Path
from typing import Final

import pytest
from batch_support import (
    REPO_ROOT,
    RUNNING,
    TOKEN_FILE,
    WorkerProcess,
    delete_schedule,
    until_started,
)
from temporalio.client import (
    Client,
    ScheduleActionExecutionStartWorkflow,
    ScheduleHandle,
    WorkflowHandle,
)
from worker_support import eventually

from ais0c_activities import KNOWLEDGE_SYNC_CONTEXT
from ais0c_storage import create_engine, create_session_factory, create_sync_engine
from ais0c_storage.migrate import upgrade
from ais0c_storage.repositories import (
    list_agent_runs,
    list_catalog_log_sources,
    list_catalog_rules,
)
from ais0c_worker import connect
from ais0c_worker.main import BATCH_COMMAND
from ais0c_workflows import KnowledgeSync, KnowledgeSyncResult
from ais0c_workflows.names import KNOWLEDGE_SYNC_SCHEDULE_ID

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("AIS0C_DEV_STACK") != "1",
        reason="dev stack test: start deploy/compose/docker-compose.dev.yaml with the qradar "
        "profile and set AIS0C_DEV_STACK=1",
    ),
]

GATEWAY_URL: Final = "http://127.0.0.1:8090"
SECRETS_DIR: Final = REPO_ROOT / "deploy/compose/secrets/agents"
# A catalog sync paces its gateway calls, so a large QRadar takes minutes (T-35).
RUN_SECONDS: Final = 900.0


async def test_the_batch_worker_syncs_the_catalog_of_the_stack(tmp_path: Path) -> None:
    """Criterion 4: the worker process, the Schedule it creates and the run its trigger starts."""
    settings = stack_settings()
    migrate(settings["AIS0C_DATABASE_URL"])
    client = await connect(settings["TEMPORAL_ADDRESS"])
    await delete_schedule(client)
    worker = WorkerProcess("dev-batch-worker", settings, tmp_path, BATCH_COMMAND)
    try:
        handle = client.get_schedule_handle(KNOWLEDGE_SYNC_SCHEDULE_ID)
        await until_started(worker)
        assert RUNNING in worker.messages()
        # The Schedule the worker created at start-up, started by hand (T-028 does the same).
        await handle.trigger()
        result: KnowledgeSyncResult = await (await triggered_run(client, handle)).result()
    finally:
        worker.stop()
        await delete_schedule(client)

    engine = create_engine(settings["AIS0C_DATABASE_URL"])
    try:
        sessions = create_session_factory(engine)
        async with sessions() as session:
            rules = await list_catalog_rules(session)
            sources = await list_catalog_log_sources(session)
            runs = await list_agent_runs(session, case_id=KNOWLEDGE_SYNC_CONTEXT)
    finally:
        await engine.dispose()

    # The catalog holds what QRadar listed, and the run's counts are that many.
    listed = [row for row in rules if row.missing_since is None]
    typed = [row for row in sources if row.missing_since is None]
    assert len(listed) == result.catalog["rules"]
    assert len(typed) == result.catalog["log_sources"]
    assert result.catalog["log_sources_untyped"] == 0
    report = {
        "catalog": result.catalog,
        "catalog_rules": len(rules),
        "catalog_log_sources": len(sources),
        "disabled_rules": sum(1 for row in listed if not row.qradar_enabled),
        "marked_missing": len(rules) + len(sources) - len(listed) - len(typed),
        "catalog_sync_runs": len(runs),
        "gateway_calls_of_the_last_run": runs[-1].tool_calls if runs else 0,
    }
    print(json.dumps(report, indent=2))
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def stack_settings() -> dict[str, str]:
    """The batch worker's settings in the dev stack; skips the test when one is missing."""
    database_url = os.environ.get("AIS0C_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("dev stack test: set AIS0C_DATABASE_URL to the stack's ais0c database")
    token = SECRETS_DIR / TOKEN_FILE
    if not token.is_file():
        pytest.skip(f"dev stack test: {token} is missing; run deploy/compose/make_secrets.py")
    return {
        "AIS0C_DATABASE_URL": database_url,
        "AIS0C_GATEWAY_URL": os.environ.get("AIS0C_GATEWAY_URL", "").strip() or GATEWAY_URL,
        "AIS0C_WORKER_SECRETS_DIR": str(SECRETS_DIR),
        "TEMPORAL_ADDRESS": os.environ.get("TEMPORAL_ADDRESS", "").strip() or "127.0.0.1:7233",
    }


def migrate(database_url: str) -> None:
    """The stack's schema, at the version the repository's migrations reach."""
    engine = create_sync_engine(database_url)
    try:
        with engine.begin() as connection:
            upgrade(connection)
    finally:
        engine.dispose()


async def triggered_run(
    client: Client, handle: ScheduleHandle
) -> WorkflowHandle[KnowledgeSync, KnowledgeSyncResult]:
    """The run of `handle` that the trigger started, once it has ended."""

    async def ended() -> WorkflowHandle[KnowledgeSync, KnowledgeSyncResult] | None:
        for action in (await handle.describe()).info.recent_actions:
            started = action.action
            if not isinstance(started, ScheduleActionExecutionStartWorkflow):
                continue
            if not started.workflow_id.startswith(KNOWLEDGE_SYNC_SCHEDULE_ID):
                continue
            run = client.get_workflow_handle(
                started.workflow_id,
                run_id=started.first_execution_run_id,
                result_type=KnowledgeSyncResult,
            )
            if (await run.describe()).status is not None:
                return run
        return None

    return await eventually(ended, RUN_SECONDS)
