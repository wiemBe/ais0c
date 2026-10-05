"""T-012: an offense in the lab QRadar reaches the Triage agent through the whole platform.

`@pytest.mark.lab`: skipped unless the lab settings are set; a missing prerequisite (compose
stack, the qradar-mcp fork, the LiteLLM key) skips it with a reason. How to run it:
tests/e2e/README.md.

The test starts the qradar-mcp fork and the MCP Policy Gateway on this host and the case worker
as `python -m ais0c_worker`, with its real intake Schedule. It sends the T-008 scenario
s2-dcsync to the lab QRadar, where the lab's test rule opens an offense; the intake reads it
through the gateway and the case is triaged by the Triage agent. While the agent's first model
request is open, the worker is killed and started again (criterion 4; `AIS0C_E2E_RESTART=0`
leaves it running). The test then checks the database and the run's Temporal history (criteria
1, 3, 5 and 6) and prints the run's latency and token use, which the PR reports.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from e2e_support import (
    LAB_RULE_NAME,
    MODEL_REGISTRY,
    REPO_ROOT,
    SCENARIO,
    E2ESetupError,
    LabSettings,
    Process,
    free_port,
    new_token,
    qradar_get,
    triage_profile,
    write_secret,
)
from sqlalchemy import select
from temporalio.api.enums.v1 import PendingActivityState
from temporalio.client import Client, ScheduleActionExecutionStartWorkflow, WorkflowHistory
from temporalio.service import RPCError, RPCStatusCode
from worker_support import (  # pyright: ignore[reportMissingImports]
    model_requests,
    run_nonce,
    unwrapped_tool_returns,
)

from ais0c_activities import SessionFactory
from ais0c_contracts import RunStatus, TriageResult
from ais0c_storage import (
    CaseStatus,
    PolicyDecision,
    create_engine,
    create_session_factory,
    create_sync_engine,
)
from ais0c_storage.migrate import upgrade
from ais0c_storage.models import AgentRunRow, CaseRow, EvidenceRow, OffenseSeenRow, ToolCallRow
from ais0c_worker import INTAKE_SCHEDULE_ID, connect
from ais0c_workflows import IntakeCheckpoint

pytestmark = [pytest.mark.lab, pytest.mark.anyio]

# QRadar opens the offense within a minute or two, and the intake runs every minute.
OFFENSE_TIMEOUT = 600
DECISION_TIMEOUT = 600
POLL_SECONDS = 0.25
# Go-live is taken from this host's clock, an offense's start time from QRadar's. The lab
# QRadar runs about a second behind this host, so logs sent right after go-live open an
# offense that seems to start before it, and the intake rightly ignores it (D-26).
CLOCK_SKEW_MARGIN = 5.0
MODEL_REQUEST = "agent__triage__model_request"


@pytest.fixture
def settings() -> LabSettings:
    try:
        return LabSettings.from_env()
    except E2ESetupError as error:
        pytest.skip(str(error))


@pytest.fixture
async def sessions(settings: LabSettings) -> AsyncIterator[SessionFactory]:
    """The dev stack's application database, migrated to the current schema."""
    engine = create_sync_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            upgrade(connection)
    finally:
        engine.dispose()
    async_engine = create_engine(settings.database_url)
    try:
        yield create_session_factory(async_engine)
    finally:
        await async_engine.dispose()


@pytest.fixture
async def client(settings: LabSettings) -> AsyncIterator[Client]:
    try:
        client = await connect(settings.temporal_address)
    except RuntimeError as error:
        pytest.skip(f"Temporal at {settings.temporal_address} cannot be reached: {error}")
    await _delete_intake_schedule(client)
    yield client
    await _delete_intake_schedule(client)


async def test_a_lab_offense_is_triaged_end_to_end(
    settings: LabSettings, sessions: SessionFactory, client: Client, tmp_path: Path
) -> None:
    rule_id = await _lab_rule_id(settings)
    await _no_open_offense_for(settings, rule_id)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(mode=0o700)
    logs = tmp_path / "logs"
    logs.mkdir()
    print(f"\nlogs: {logs}")

    fork_port, gateway_port = free_port(), free_port()
    write_secret(secrets_dir, "qradar-token", settings.qradar_token)
    write_secret(secrets_dir, "mcp-token-qradar-mcp-read", new_token())
    # The gateway serves, and the worker holds a token for, the Triage manifest's profile.
    write_secret(secrets_dir, f"gateway-token-{triage_profile()}", new_token())
    fork = Process(
        "qradar-mcp",
        [
            settings.fork_command,
            "--profile",
            "qradar-read",
            "--host",
            "127.0.0.1",
            "--port",
            str(fork_port),
        ],
        {
            "QRADAR_CONSOLE_FQDN": settings.qradar_host,
            "QRADAR_AUTH_TOKEN_FILE": str(secrets_dir / "qradar-token"),
            "MCP_AUTH_TOKEN_FILE": str(secrets_dir / "mcp-token-qradar-mcp-read"),
            "QRADAR_VERIFY_SSL": "true" if settings.verify_ssl else "false",
        },
        logs,
    )
    gateway = Process(
        "gateway",
        [sys.executable, "-m", "ais0c_mcp_gateway"],
        {
            "AIS0C_DATABASE_URL": settings.database_url,
            "AIS0C_GATEWAY_CONFIG_DIR": str(REPO_ROOT / "config"),
            "AIS0C_GATEWAY_SECRETS_DIR": str(secrets_dir),
            "AIS0C_GATEWAY_UPSTREAM_URL_QRADAR_MCP_READ": f"http://127.0.0.1:{fork_port}/mcp",
            "AIS0C_GATEWAY_HOST": "127.0.0.1",
            "AIS0C_GATEWAY_PORT": str(gateway_port),
        },
        logs,
    )
    worker_env = {
        "TEMPORAL_ADDRESS": settings.temporal_address,
        "AIS0C_DATABASE_URL": settings.database_url,
        "AIS0C_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}",
        "AIS0C_WORKER_SECRETS_DIR": str(secrets_dir),
        "AIS0C_WORKER_ROOT": str(REPO_ROOT),
        "AIS0C_MODEL_REGISTRY": MODEL_REGISTRY,
        "LITELLM_BASE_URL": settings.litellm_base_url,
        "LITELLM_API_KEY": settings.litellm_api_key,
    }
    workers: list[Process] = []
    try:
        fork.wait_http_ok(f"http://127.0.0.1:{fork_port}/healthz")
        gateway.wait_http_ok(f"http://127.0.0.1:{gateway_port}/healthz")
        workers.append(
            Process("worker-1", [sys.executable, "-m", "ais0c_worker"], worker_env, logs)
        )

        # Go live: the Schedule's first run sets the checkpoint; only offenses that start
        # after it are processed (D-26).
        go_live = await _first_intake_run(client, workers[-1])
        await asyncio.sleep(CLOCK_SKEW_MARGIN)
        sent_at = datetime.now(UTC)
        _send_scenario(settings, tmp_path)

        # Criterion 1: the intake reads the new offense through the gateway.
        seen = await _eventually(
            lambda: _offense_of_rule(sessions, rule_id, go_live.go_live_at), OFFENSE_TIMEOUT
        )
        case_id = f"case-{seen.offense_id}"
        run_id = f"{case_id}-triage-1"
        print(f"offense {seen.offense_id}, case {case_id}, triage run {run_id}")

        restart: dict[str, Any] = {}
        if os.environ.get("AIS0C_E2E_RESTART", "1") != "0":
            # Criterion 4: kill the worker while the agent waits for the model, start another.
            attempt = await _eventually(lambda: _open_model_request(client, run_id), 300)
            workers[-1].kill()
            restart = {"killed_at": datetime.now(UTC).isoformat(), "open_attempt": attempt}
            await asyncio.sleep(2)
            workers.append(
                Process("worker-2", [sys.executable, "-m", "ais0c_worker"], worker_env, logs)
            )

        case = await _eventually(lambda: _decided(sessions, case_id), DECISION_TIMEOUT)
        triage = client.get_workflow_handle(run_id)
        description = await triage.describe()
        history = await triage.fetch_history()
        run, calls, evidence = await _records(sessions, case_id, run_id)
    finally:
        for process in reversed(workers):
            process.stop()
        gateway.stop()
        fork.stop()

    # Criterion 3: the case holds the decision, the run its TriageResult, and the gateway
    # recorded each call with its policy decision and the evidence with its query hash.
    assert (case.status, case.evaluation_no) == (CaseStatus.DECIDED, 1)
    assert case.verdict is not None
    assert run.status is RunStatus.COMPLETED
    assert isinstance(run.result, TriageResult)
    assert (run.result.verdict, run.result.ai_level) == (case.verdict, case.ai_level)
    assert calls, "the Triage agent made no tool call"
    assert {call.policy_decision for call in calls} <= {PolicyDecision.ALLOW, PolicyDecision.DENY}
    assert [row for row in evidence if row.query_hash], "no evidence with a query hash"
    # Criterion 6: the model was called by its alias.
    assert run.model_alias == "soc-fast"
    # Criterion 5: every tool result in the recorded model requests is wrapped.
    nonce = run_nonce(history)
    requests = model_requests(history)
    assert requests
    for messages in requests:
        assert unwrapped_tool_returns(messages, nonce) == []
    # Criterion 4: one run of the Triage workflow, one agent run, the open request retried.
    assert description.status is not None
    assert description.status.name == "COMPLETED"
    starts = [
        e for e in history.events if e.HasField("workflow_execution_started_event_attributes")
    ]
    assert len(starts) == 1
    if restart:
        assert max(_attempts(history)) >= 2

    report = _report(seen, case, run, calls, evidence, history, sent_at, restart)
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


# --- preconditions ----------------------------------------------------------------------------


async def _lab_rule_id(settings: LabSettings) -> int:
    rules = await qradar_get(
        settings,
        "/analytics/rules",
        {"fields": "id,name,enabled", "filter": f'name = "{LAB_RULE_NAME}"'},
    )
    if not rules or rules[0].get("enabled") is not True or not isinstance(rules[0].get("id"), int):
        pytest.skip(f"the lab has no enabled rule {LAB_RULE_NAME!r} (tests/e2e/README.md)")
    rule_id = rules[0]["id"]
    assert isinstance(rule_id, int)
    return rule_id


async def _no_open_offense_for(settings: LabSettings, rule_id: int) -> None:
    """QRadar adds new events to an open offense of the same rule and account instead of
    opening another; the intake would ignore it, since it started before go-live."""
    offenses = await qradar_get(
        settings,
        "/siem/offenses",
        {"fields": "id,offense_source,rules(id)", "filter": 'status = "OPEN"'},
    )
    for offense in offenses:
        rules = offense.get("rules")
        ids = (
            {rule.get("id") for rule in rules if isinstance(rule, dict)}
            if isinstance(rules, list)
            else set()
        )
        if rule_id in ids and offense.get("offense_source") == settings.attacker:
            pytest.fail(
                f"offense {offense.get('id')} of the lab rule for {settings.attacker} is still "
                "open; close it in QRadar or use the other AIS0C_E2E_SEED (tests/e2e/README.md)"
            )


async def _delete_intake_schedule(client: Client) -> None:
    try:
        await client.get_schedule_handle(INTAKE_SCHEDULE_ID).delete()
    except RPCError as error:
        if error.status is not RPCStatusCode.NOT_FOUND:
            raise


# --- steps ------------------------------------------------------------------------------------


async def _first_intake_run(client: Client, worker: Process) -> IntakeCheckpoint:
    """Trigger the worker's intake Schedule once and wait for that run's checkpoint."""

    async def created() -> bool | None:
        if not worker.running():
            pytest.fail(f"the worker exited; see {worker.log_path}")
        try:
            await client.get_schedule_handle(INTAKE_SCHEDULE_ID).describe()
        except RPCError as error:
            if error.status is RPCStatusCode.NOT_FOUND:
                return None
            raise
        return True

    await _eventually(created, 120)
    schedule = client.get_schedule_handle(INTAKE_SCHEDULE_ID)
    await schedule.trigger()

    async def started() -> ScheduleActionExecutionStartWorkflow | None:
        actions = (await schedule.describe()).info.recent_actions
        if not actions:
            return None
        action = actions[0].action
        assert isinstance(action, ScheduleActionExecutionStartWorkflow)
        return action

    action = await _eventually(started, 120)
    handle = client.get_workflow_handle(
        action.workflow_id, run_id=action.first_execution_run_id, result_type=IntakeCheckpoint
    )
    return await handle.result()


def _send_scenario(settings: LabSettings, tmp_path: Path) -> None:
    """T-008's generator, over TCP syslog; time compressed so the attack is sent at once."""
    subprocess.run(  # noqa: S603 - the repository's own generator
        [
            sys.executable,
            "-m",
            "ais0c_harness.loggen",
            "run",
            "--scenario",
            SCENARIO,
            "--target",
            settings.syslog_target,
            "--seed",
            settings.seed,
            "--speed",
            "100000",
            "--out-dir",
            str(tmp_path),
        ],
        check=True,
        cwd=REPO_ROOT,
    )


async def _offense_of_rule(
    sessions: SessionFactory, rule_id: int, go_live: datetime
) -> OffenseSeenRow | None:
    async with sessions() as session:
        rows = await session.scalars(
            select(OffenseSeenRow).where(OffenseSeenRow.first_seen_at >= go_live)
        )
        for row in rows:
            if rule_id in row.rule_ids and row.case_id is not None:
                return row
    return None


async def _open_model_request(client: Client, run_id: str) -> int | None:
    """The attempt of the Triage run's model request that a worker is running now."""
    try:
        description = await client.get_workflow_handle(run_id).describe()
    except RPCError as error:
        if error.status is RPCStatusCode.NOT_FOUND:
            return None
        raise
    if description.status is not None and description.status.name != "RUNNING":
        pytest.fail("the Triage run ended before the worker could be stopped; run it again")
    for pending in description.raw_description.pending_activities:
        if (
            pending.activity_type.name == MODEL_REQUEST
            and pending.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
        ):
            return pending.attempt
    return None


async def _decided(sessions: SessionFactory, case_id: str) -> CaseRow | None:
    async with sessions() as session:
        case = await session.get(CaseRow, case_id)
    if case is None or case.status is CaseStatus.RUNNING:
        return None
    if case.status is not CaseStatus.DECIDED:
        pytest.fail(f"{case_id} ended as {case.status.value}, without a decision")
    return case


async def _records(
    sessions: SessionFactory, case_id: str, run_id: str
) -> tuple[AgentRunRow, list[ToolCallRow], list[EvidenceRow]]:
    async with sessions() as session:
        runs = list(
            await session.scalars(
                select(AgentRunRow).where(
                    AgentRunRow.case_id == case_id, AgentRunRow.agent_id == "triage"
                )
            )
        )
        assert [row.run_id for row in runs] == [run_id], "expected exactly one Triage run"
        calls = list(
            await session.scalars(
                select(ToolCallRow)
                .where(ToolCallRow.run_id == run_id)
                .order_by(ToolCallRow.created_at)
            )
        )
        evidence_ids = [call.evidence_id for call in calls if call.evidence_id]
        evidence = list(
            await session.scalars(
                select(EvidenceRow).where(EvidenceRow.evidence_id.in_(evidence_ids))
            )
        )
    return runs[0], calls, evidence


async def _eventually[T](check: Callable[[], Awaitable[T | None]], seconds: float) -> T:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = await check()
        if value is not None:
            return value
        await asyncio.sleep(POLL_SECONDS)
    raise TimeoutError(f"nothing after {seconds} seconds")


# --- what the run left ------------------------------------------------------------------------


def _attempts(history: WorkflowHistory) -> list[int]:
    return [
        event.activity_task_started_event_attributes.attempt
        for event in history.events
        if event.HasField("activity_task_started_event_attributes")
    ]


def _activity_seconds(history: WorkflowHistory) -> list[tuple[str, float]]:
    """(activity type, seconds from scheduled to completed) of every activity of the run."""
    scheduled: dict[int, tuple[str, datetime]] = {}
    durations: list[tuple[str, float]] = []
    for event in history.events:
        when = event.event_time.ToDatetime(tzinfo=UTC)
        if event.HasField("activity_task_scheduled_event_attributes"):
            name = event.activity_task_scheduled_event_attributes.activity_type.name
            scheduled[event.event_id] = (name, when)
        elif event.HasField("activity_task_completed_event_attributes"):
            name, start = scheduled[
                event.activity_task_completed_event_attributes.scheduled_event_id
            ]
            durations.append((name, round((when - start).total_seconds(), 2)))
    return durations


def _report(
    seen: OffenseSeenRow,
    case: CaseRow,
    run: AgentRunRow,
    calls: list[ToolCallRow],
    evidence: list[EvidenceRow],
    history: WorkflowHistory,
    sent_at: datetime,
    restart: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(run.result, TriageResult)
    assert run.ended_at is not None
    assert case.decided_at is not None
    activities = _activity_seconds(history)
    model_seconds = [seconds for name, seconds in activities if name == MODEL_REQUEST]
    return {
        "offense_id": seen.offense_id,
        "case_id": case.case_id,
        "run_id": run.run_id,
        "model_alias": run.model_alias,
        "verdict": run.result.verdict.value,
        "confidence": run.result.confidence.value,
        "ai_level": run.result.ai_level.value,
        "notify_level": None if case.notify_level is None else case.notify_level.value,
        "claims": len(run.result.claims),
        "injection_suspected": run.result.injection_suspected,
        "tokens": run.tokens,
        "tool_calls": run.tool_calls,
        "tool_call_records": [
            (call.intent.tool_id, call.policy_decision.value, call.status.value, call.latency_ms)
            for call in calls
        ],
        "evidence_with_query_hash": sum(1 for row in evidence if row.query_hash),
        "model_requests": len(model_seconds),
        "model_request_seconds": model_seconds,
        "activities": activities,
        "seconds": {
            "logs_sent_to_offense_seen": round((seen.first_seen_at - sent_at).total_seconds(), 1),
            "offense_seen_to_triage_start": round(
                (run.started_at - seen.first_seen_at).total_seconds(), 1
            ),
            "triage_run": round((run.ended_at - run.started_at).total_seconds(), 1),
            "agent_reported": run.result.usage.seconds,
            "offense_seen_to_decision": round(
                (case.decided_at - seen.first_seen_at).total_seconds(), 1
            ),
            "logs_sent_to_decision": round((case.decided_at - sent_at).total_seconds(), 1),
        },
        "restart": restart,
    }
