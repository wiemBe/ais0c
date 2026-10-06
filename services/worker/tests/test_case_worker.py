"""The case worker end to end: real workflows and activities, PostgreSQL, a fake offense source
and the Triage agent with a scripted model, on the time-skipping test server.

Covers the acceptance criteria of T-010 across the workflow and activity boundary; the
recorded histories are replayed at the end of each scenario (criterion 9).
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Replayer
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from worker_support import (
    ChainModels,
    Platform,
    RecordingGateway,
    TriageModel,
    chain_runtime,
    offense,
    running_platform,
    triage_runtime,
)

from ais0c_activities import CaseLauncher, CaseSettings, FakeOffenseSource, SessionFactory
from ais0c_contracts import CatalogMode, Level
from ais0c_storage.enums import CaseStatus, CriticalAssetKind, OffenseStatus
from ais0c_storage.repositories import (
    SyncedRule,
    add_critical_asset,
    sync_catalog_rules,
    update_catalog_rule,
)
from ais0c_worker import build_case_worker
from ais0c_workflows import CASE_QUEUE_WORKFLOWS, CaseView
from ais0c_workflows import CaseStatus as WorkflowCaseStatus

pytestmark = pytest.mark.anyio

SKIP_RULE = 900001
SYNCED_AT = datetime(2026, 10, 1, tzinfo=UTC)


async def define_rule(
    sessions: SessionFactory,
    rule_id: int,
    *,
    mode: CatalogMode = CatalogMode.ANALYZE,
    min_level: Level | None = None,
) -> None:
    async with sessions.begin() as session:
        rule = SyncedRule(rule_id=rule_id, rule_name=f"Rule {rule_id}")
        await sync_catalog_rules(session, [rule], synced_by="sync", synced_at=SYNCED_AT)
        await update_catalog_rule(
            session,
            rule_id,
            mode=mode,
            min_level=min_level,
            has_automated_action=mode is CatalogMode.SKIP,
            context_note=None,
            updated_by="admin",
            updated_at=SYNCED_AT,
        )


async def replay_all(platform: Platform, *case_ids: str) -> None:
    """Replay the intake runs, the cases and every Triage run the model saw.

    A Triage run replays its agent, so it needs the agent the worker installed.
    """
    replayer = Replayer(workflows=list(CASE_QUEUE_WORKFLOWS), plugins=[PydanticAIPlugin()])
    handles: list[WorkflowHandle] = [*platform.intake_runs]
    workflow_ids = [*case_ids, *platform.triage_runs()]
    handles += [
        platform.env.client.get_workflow_handle(workflow_id) for workflow_id in workflow_ids
    ]
    for handle in handles:
        await replayer.replay_workflow(await handle.fetch_history())


async def assert_no_workflow(client: Client, workflow_id: str) -> None:
    with pytest.raises(RPCError) as error:
        await client.get_workflow_handle(workflow_id).describe()
    assert error.value.status is RPCStatusCode.NOT_FOUND


async def test_offenses_flow_from_intake_to_closed_cases(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    await define_rule(sessions, SKIP_RULE, mode=CatalogMode.SKIP)
    t0 = await env.get_current_time()

    async with running_platform(env, sessions) as platform:
        platform.source.put(offense(1, start=t0 - timedelta(hours=2)))
        first = await platform.run_intake()
        # Criterion 1: the first run sets the checkpoint to its own time.
        assert t0 <= first.go_live_at <= await env.get_current_time()
        assert await platform.seen(1) is None

        await env.sleep(timedelta(minutes=10))
        t1 = await env.get_current_time()
        # Offense 1 started before go-live and changes after it: never processed (D-26).
        platform.source.put(
            offense(1, start=t0 - timedelta(hours=2), updated=t1 - timedelta(minutes=5))
        )
        platform.source.put(offense(2, start=t1 - timedelta(minutes=4)))
        platform.source.put(offense(3, start=t1 - timedelta(minutes=3), rule_ids=[SKIP_RULE]))
        second = await platform.run_intake(first)

        assert await platform.seen(1) is None
        # Criterion 3: a skipped rule is recorded as skipped and gets no case workflow.
        skipped = await platform.seen(3)
        assert skipped is not None
        assert skipped.status is OffenseStatus.SKIPPED
        await assert_no_workflow(env.client, "case-3")
        assert await platform.case("case-3") is None
        # Offense 2 gets its case.
        decided = await platform.case_when("case-2", lambda row: row.status is CaseStatus.DECIDED)
        assert (decided.evaluation_no, decided.notify_level) == (1, Level.MEDIUM)

        # Criterion 2: the same offenses again are processed only once.
        await platform.run_intake(first)
        assert platform.triage_runs() == ["case-2-triage-1"]
        started = await platform.seen(2)
        assert started is not None
        assert (started.status, started.case_id) == (OffenseStatus.RUNNING, "case-2")

        # Criterion 7: an update re-evaluates the case, here one with a new destination (D-31)...
        t2 = await env.get_current_time()
        platform.source.put(
            offense(
                2,
                start=t1 - timedelta(minutes=4),
                updated=t2,
                destination_ips=["198.51.100.15", "192.0.2.20"],
            )
        )
        third = await platform.run_intake(second)
        await platform.case_when(
            "case-2", lambda row: row.evaluation_no == 2 and row.status is CaseStatus.DECIDED
        )
        assert platform.triage_runs() == ["case-2-triage-1", "case-2-triage-2"]

        # ...and closing the offense in QRadar ends it.
        platform.source.close(2)
        await platform.run_intake(third)
        result = await env.client.get_workflow_handle("case-2", result_type=CaseView).result()
        assert (result.status, result.evaluation_no) == (WorkflowCaseStatus.CLOSED, 2)

    closed = await platform.case("case-2")
    done = await platform.seen(2)
    assert closed is not None
    assert done is not None
    assert (closed.status, done.status) == (CaseStatus.CLOSED, OffenseStatus.DONE)
    await replay_all(platform, "case-2")


async def test_pending_cases_start_by_pre_priority_within_the_limit(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criterion 5: four offenses wait, two cases may evaluate at a time."""
    await define_rule(sessions, 10, min_level=Level.CRITICAL)
    await define_rule(sessions, 11, min_level=Level.HIGH)
    async with sessions.begin() as session:
        await add_critical_asset(
            session, kind=CriticalAssetKind.IP, value="192.0.2.50", label="SWIFT", level=Level.HIGH
        )

    async with running_platform(
        env,
        sessions,
        settings=CaseSettings(
            case_url_base="https://ais0c.example.com/cases", max_concurrent_cases=2
        ),
    ) as platform:
        first = await platform.run_intake()
        await env.sleep(timedelta(minutes=10))
        now = await env.get_current_time()
        platform.source.put(
            offense(40, start=now - timedelta(minutes=5), rule_ids=[12]),
            offense(
                41, start=now - timedelta(minutes=4), rule_ids=[12], destination_ips=["192.0.2.50"]
            ),
            offense(42, start=now - timedelta(minutes=3), rule_ids=[11]),
            offense(43, start=now - timedelta(minutes=2), rule_ids=[10]),
        )
        second = await platform.run_intake(first)

        statuses = {n: row.status for n in (40, 41, 42, 43) if (row := await platform.seen(n))}
        assert statuses == {
            40: OffenseStatus.PENDING,
            41: OffenseStatus.PENDING,
            42: OffenseStatus.RUNNING,
            43: OffenseStatus.RUNNING,
        }
        for case_id in ("case-42", "case-43"):
            await platform.case_when(case_id, lambda row: row.status is CaseStatus.DECIDED)

        # The first two have decided, so the other two start.
        await platform.run_intake(second)
        statuses = {n: row.status for n in (40, 41) if (row := await platform.seen(n))}
        assert statuses == {40: OffenseStatus.RUNNING, 41: OffenseStatus.RUNNING}


async def test_a_case_id_is_never_used_twice(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criterion 6: `case-<offense_id>`, and a second start is rejected, also after closing."""
    async with running_platform(env, sessions) as platform:
        first = await platform.run_intake()
        await env.sleep(timedelta(minutes=1))
        platform.source.put(offense(50, start=await env.get_current_time()))
        second = await platform.run_intake(first)
        handle = env.client.get_workflow_handle("case-50")
        original = (await handle.describe()).run_id
        launcher = CaseLauncher(client=env.client, sessions=sessions)

        assert await ActivityEnvironment().run(launcher.start_case, 50) is False
        platform.source.close(50)
        await platform.run_intake(second)
        await handle.result()
        assert await ActivityEnvironment().run(launcher.start_case, 50) is False

        description = await handle.describe()
        assert (description.id, description.run_id) == ("case-50", original)
        assert description.status is not None
        assert description.status.name == "COMPLETED"


async def test_a_case_without_a_decision_by_its_sla_is_marked(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criterion 8: the model is unreachable past the 10-minute SLA of a high-floor offense.

    The Triage run's wall clock budget is raised to an hour here, so the run itself outlasts the
    SLA; with the manifest's 180 seconds it would end without a decision.
    """
    await define_rule(sessions, 11, min_level=Level.HIGH)

    async def model_down_once(run_id: str, step: int, attempt: int) -> None:
        if step == 1 and attempt == 1:
            raise ApplicationError("model unavailable", next_retry_delay=timedelta(minutes=20))

    model = TriageModel(ai_level=Level.LOW, hook=model_down_once)
    async with running_platform(env, sessions, model=model, wall_clock_seconds=3600) as platform:
        first = await platform.run_intake()
        await env.sleep(timedelta(minutes=1))
        start = await env.get_current_time()
        platform.source.put(offense(60, start=start, rule_ids=[11]))
        await platform.run_intake(first)
        opened = await platform.case_when("case-60", lambda row: len(model.requests) == 1)
        assert opened.sla_due_at == start + timedelta(minutes=10)

        await env.sleep(timedelta(minutes=11))
        missed = await platform.case_when(
            "case-60", lambda row: row.status is CaseStatus.NO_AI_DECISION
        )
        assert missed.verdict is None

        # The late decision still lands, at least at the floor.
        await env.sleep(timedelta(minutes=10))
        late = await platform.case_when("case-60", lambda row: row.status is CaseStatus.DECIDED)
        assert (late.ai_level, late.notify_level) == (Level.LOW, Level.HIGH)
        assert late.decided_at is not None
        assert late.decided_at > late.sla_due_at

    await replay_all(platform, "case-60")


async def test_the_case_worker_runs_workflows_in_the_sandbox(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    worker = build_case_worker(
        env.client,
        sessions=sessions,
        source=FakeOffenseSource(),
        triage=triage_runtime(TriageModel(), RecordingGateway()),
        chain=chain_runtime(ChainModels(), RecordingGateway()),
        settings=CaseSettings(
            case_url_base="https://ais0c.example.com/cases",
        ),
    )

    config = worker.config()
    assert isinstance(config.get("workflow_runner"), SandboxedWorkflowRunner)
    assert config.get("task_queue") == "soc-case"


@pytest.mark.parametrize("pydantic_converter", [False, True])
async def test_the_case_worker_needs_pydantic_ais_plugin(
    env: WorkflowEnvironment, sessions: SessionFactory, pydantic_converter: bool
) -> None:
    """Contract models need the Pydantic converter, the agent the plugin's sandbox settings."""
    plain = (
        Client(
            env.client.service_client,
            namespace=env.client.namespace,
            data_converter=pydantic_data_converter,
        )
        if pydantic_converter
        else Client(env.client.service_client, namespace=env.client.namespace)
    )

    with pytest.raises(ValueError, match="Pydantic AI's plugin"):
        build_case_worker(
            plain,
            sessions=sessions,
            source=FakeOffenseSource(),
            triage=triage_runtime(TriageModel(), RecordingGateway()),
            chain=chain_runtime(ChainModels(), RecordingGateway()),
            settings=CaseSettings(
                case_url_base="https://ais0c.example.com/cases",
            ),
        )
