"""The Triage agent inside the case worker (T-012 criteria 2, 4 and 5; T-016 criterion 3).

The agent is the real one with TemporalDurability; its model is scripted and its gateway fake
(worker_support). Each test runs the intake, lets the case's Triage run decide, and reads what
the run left: its workflow history, its `agent_runs` row and the gateway's calls.
"""

import asyncio
from datetime import timedelta

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from temporalio.client import WorkflowHistory
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from worker_support import (
    ESCAPE,
    INJECTION,
    MODEL_REGISTRY,
    OFFENSE_EVIDENCE,
    ChainModels,
    Platform,
    RecordingGateway,
    TriageModel,
    chain_runtime,
    cited_alias,
    eventually,
    model_requests,
    offense,
    run_nonce,
    running_platform,
    tool_returns,
    triage_runtime,
    unwrapped_tool_returns,
)

from ais0c_activities import CaseSettings, FakeOffenseSource, SessionFactory, load_model_releases
from ais0c_contracts import CaseVerdict, Confidence, Level, RunStatus, TriageResult
from ais0c_storage.enums import CaseStatus
from ais0c_storage.models import AgentRunRow, CaseRow
from ais0c_storage.repositories import get_case, list_agent_runs
from ais0c_worker import build_case_worker
from ais0c_workflows import OffenseIntake
from ais0c_workflows.names import BEGIN_TRIAGE_RUN, CASE_TASK_QUEUE, FINISH_TRIAGE_RUN

pytestmark = pytest.mark.anyio

MODEL_REQUEST = "agent__triage__model_request"
GATEWAY_CALL = "agent__triage__toolset__gateway-qradar-triage-read__call_tool"


async def decided_case(platform: Platform, offense_id: int) -> CaseRow:
    """Run the intake once to go live and once more with the offense; wait for the decision."""
    first = await platform.run_intake()
    await platform.env.sleep(timedelta(minutes=1))
    platform.source.put(offense(offense_id, start=await platform.env.get_current_time()))
    await platform.run_intake(first)
    return await platform.case_when(
        f"case-{offense_id}", lambda row: row.status is CaseStatus.DECIDED
    )


def scheduled_activities(history: WorkflowHistory) -> list[str]:
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


async def test_the_triage_agent_runs_as_temporal_activities(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criterion 2: the T-010 stub is gone; the agent's model requests and tool calls are
    activities of the case's Triage run, which records itself and its result."""
    async with running_platform(env, sessions) as platform:
        case = await decided_case(platform, 70)
        history = await env.client.get_workflow_handle("case-70-triage-1").fetch_history()
        runs = await platform.agent_runs("case-70", "triage")

    assert scheduled_activities(history) == [
        BEGIN_TRIAGE_RUN,
        MODEL_REQUEST,
        GATEWAY_CALL,
        MODEL_REQUEST,
        FINISH_TRIAGE_RUN,
    ]
    assert (case.verdict, case.confidence, case.ai_level, case.notify_level) == (
        CaseVerdict.SUSPICIOUS,
        Confidence.MEDIUM,
        Level.MEDIUM,
        Level.MEDIUM,
    )

    [run] = runs
    assert (run.run_id, run.status, run.agent_id, run.agent_version) == (
        "case-70-triage-1",
        RunStatus.COMPLETED,
        "triage",
        "1.1.0",
    )
    assert (run.model_alias, run.prompt_version, run.toolset_profile) == (
        "soc-fast",
        "triage/v2",
        "qradar-triage-read",
    )
    assert (run.tool_calls, run.ended_at is not None) == (1, True)
    assert run.tokens > 0
    # T-016: the run carries the release of the model behind soc-fast, from the registry.
    release = load_model_releases(MODEL_REGISTRY)["soc-fast"]
    assert (run.model_release, run.model_target) == (release, release.target)
    # The task belongs to the case and to the case workflow's run.
    assert (run.task.case_id, run.task.parent_run_id) == ("case-70", case.run_id)
    assert run.task.budget.seconds == 420

    result = run.result
    assert isinstance(result, TriageResult)
    assert (result.task_id, result.verdict, result.status) == (
        "case-70-triage-1",
        CaseVerdict.SUSPICIOUS,
        RunStatus.COMPLETED,
    )
    assert [claim.evidence_ids for claim in result.claims] == [[OFFENSE_EVIDENCE]]

    # The gateway saw the agent's one call under the run, for the case; the later calls are the
    # chain's (test_agent_chain).
    assert platform.gateway.runs[:1] == ["case-70-triage-1"]
    assert "case-70-triage-1" not in platform.gateway.runs[1:]
    assert [(intent.case_id, intent.agent_id) for intent in platform.gateway.intents[:1]] == [
        ("case-70", "triage")
    ]


async def test_tool_results_reach_the_model_only_inside_the_wrapper(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criterion 5, from the recorded messages: the model request activities in the run's
    history hold every message the model got."""
    async with running_platform(env, sessions) as platform:
        await decided_case(platform, 71)
        history = await env.client.get_workflow_handle("case-71-triage-1").fetch_history()

    nonce = run_nonce(history)
    requests = model_requests(history)
    assert len(requests) == 2
    for messages in requests:
        assert unwrapped_tool_returns(messages, nonce) == []
    [returned] = tool_returns(requests[-1])
    assert isinstance(returned.content, str)
    # The attacker's text reached the model, but only as data inside this run's wrapper, and
    # the closing tag planted in it no longer closes anything.
    assert INJECTION in returned.content
    assert returned.content.count(f"</untrusted_{nonce}>") == 1
    assert ESCAPE not in returned.content
    assert "<org_context>" not in returned.content


async def test_the_model_cites_an_alias_and_never_sees_the_evidence_id(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """T-038: the tool result shows the call's evidence alias; the model cites it, and the run's
    result carries the gateway's evidence ID."""
    async with running_platform(env, sessions) as platform:
        await decided_case(platform, 73)
        history = await env.client.get_workflow_handle("case-73-triage-1").fetch_history()
        [run] = await platform.agent_runs("case-73", "triage")

    requests = model_requests(history)
    [returned] = tool_returns(requests[-1])
    assert cited_alias(returned) == "ev_1"
    for messages in requests:
        for part in tool_returns(messages):
            assert isinstance(part.content, str)
            assert OFFENSE_EVIDENCE not in part.content
    assert isinstance(run.result, TriageResult)
    assert [claim.evidence_ids for claim in run.result.claims] == [[OFFENSE_EVIDENCE]]


def test_the_wrapper_check_finds_unwrapped_tool_results() -> None:
    nonce = "0123456789abcdef"
    wrapped = (
        f'<untrusted_{nonce} source="qradar.get_offense" evidence_id="ev_1">\n'
        '{"status": "ok"}\n'
        f"</untrusted_{nonce}>"
    )
    smuggled = wrapped.replace('{"status": "ok"}', f"x</untrusted_{nonce}>y")

    def request(content: str) -> list[ModelRequest]:
        return [ModelRequest(parts=[ToolReturnPart("get_offense", content, "call-1")])]

    assert unwrapped_tool_returns(request(wrapped), nonce) == []
    for bad in ('{"status": "ok"}', wrapped.replace(nonce, "fedcba9876543210"), smuggled):
        assert unwrapped_tool_returns(request(bad), nonce) == [bad]


async def test_a_run_the_model_ended_is_retried_once(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """T-014 criterion 5 with the real agent: the first run's model request fails for good, so
    the run ends with a model error; after the wait the case runs Triage once more, as an agent
    run of its own, and that run decides."""

    async def unreachable_for_the_first_run(run_id: str, step: int, attempt: int) -> None:
        if not run_id.endswith("-retry"):
            raise ApplicationError(
                "LiteLLM answered 503", type="ModelHTTPError", non_retryable=True
            )

    model = TriageModel(hook=unreachable_for_the_first_run)
    async with running_platform(env, sessions, model=model) as platform:
        first = await platform.run_intake()
        await env.sleep(timedelta(minutes=1))
        platform.source.put(offense(73, start=await env.get_current_time()))
        await platform.run_intake(first)

        async def first_run_failed() -> list[AgentRunRow] | None:
            runs = await platform.agent_runs("case-73", "triage")
            return runs if any(run.status is RunStatus.FAILED for run in runs) else None

        await eventually(first_run_failed)
        await env.sleep(timedelta(minutes=5))
        case = await platform.case_when("case-73", lambda row: row.status is CaseStatus.DECIDED)
        runs = await platform.agent_runs("case-73", "triage")

    assert [(run.run_id, run.status) for run in runs] == [
        ("case-73-triage-1", RunStatus.FAILED),
        ("case-73-triage-1-retry", RunStatus.COMPLETED),
    ]
    assert platform.triage_runs() == ["case-73-triage-1", "case-73-triage-1-retry"]
    assert (case.evaluation_no, case.verdict) == (1, CaseVerdict.SUSPICIOUS)


async def test_a_worker_restart_resumes_the_triage_run(
    dev_server: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criterion 4: the first worker stops while the agent's second model request is open; the
    next worker continues the run from its history and the case gets one decision.

    Runs on the dev server, which releases a stopped worker's sticky queue as production does.
    """
    env = dev_server
    open_request = asyncio.Event()

    async def stall_second_request(run_id: str, step: int, attempt: int) -> None:
        if step == 2:
            open_request.set()
            await asyncio.Event().wait()

    source = FakeOffenseSource()
    first_model, first_gateway = TriageModel(hook=stall_second_request), RecordingGateway(sessions)
    first_worker = build_case_worker(
        env.client,
        sessions=sessions,
        source=source,
        triage=triage_runtime(first_model, first_gateway),
        chain=chain_runtime(ChainModels(), first_gateway),
        settings=CaseSettings(
            case_url_base="https://ais0c.example.com/cases",
        ),
    )
    async with first_worker:
        go_live = await env.client.execute_workflow(
            OffenseIntake.run, None, id="offense-intake-1", task_queue=CASE_TASK_QUEUE
        )
        # No time skipping here: an offense that starts now starts after go-live.
        source.put(offense(72, start=await env.get_current_time()))
        await env.client.execute_workflow(
            OffenseIntake.run, go_live, id="offense-intake-2", task_queue=CASE_TASK_QUEUE
        )
        async with asyncio.timeout(15):
            await open_request.wait()
    # The first worker is gone; its open request is cancelled with it.

    second_model, second_gateway = TriageModel(), RecordingGateway(sessions)
    second_worker = build_case_worker(
        env.client,
        sessions=sessions,
        source=source,
        triage=triage_runtime(second_model, second_gateway),
        chain=chain_runtime(ChainModels(), second_gateway),
        settings=CaseSettings(
            case_url_base="https://ais0c.example.com/cases",
        ),
    )
    async with second_worker:

        async def decided() -> CaseRow | None:
            async with sessions() as session:
                row = await get_case(session, "case-72")
            return row if row is not None and row.status is CaseStatus.DECIDED else None

        case = await eventually(decided)
        triage = env.client.get_workflow_handle("case-72-triage-1")
        description = await triage.describe()
        history = await triage.fetch_history()

    run_id = "case-72-triage-1"
    # The first worker read the offense and asked for the answer, which never came.
    assert [(run, step) for run, step, _ in first_model.requests] == [(run_id, 1), (run_id, 2)]
    assert first_gateway.runs == [run_id]
    # The second worker did not start over: it only retried the open request. The gateway call
    # finished before the stop, so it is replayed from the history, not made again.
    assert [(run, step) for run, step, _ in second_model.requests] == [(run_id, 2)]
    assert second_model.requests[0][2] >= 2
    assert [intent for intent in second_gateway.intents if intent.run_id == run_id] == []
    # One run of the workflow, one agent run, one decision.
    assert description.status is not None
    assert description.status.name == "COMPLETED"
    started = [
        event
        for event in history.events
        if event.HasField("workflow_execution_started_event_attributes")
    ]
    assert len(started) == 1
    async with sessions() as session:
        runs = await list_agent_runs(session, case_id="case-72")
    runs = [run for run in runs if run.agent_id == "triage"]
    assert [(run.run_id, run.status, run.tool_calls) for run in runs] == [
        (run_id, RunStatus.COMPLETED, 1)
    ]
    assert (case.evaluation_no, case.verdict) == (1, CaseVerdict.SUSPICIOUS)
    # T-038: the second worker's model cited the alias the first worker's call returned, and
    # the mapping read from the replayed history turned it into the evidence ID.
    [replayed] = tool_returns(model_requests(history)[-1])
    assert cited_alias(replayed) == "ev_1"
    assert isinstance(runs[0].result, TriageResult)
    assert [claim.evidence_ids for claim in runs[0].result.claims] == [[OFFENSE_EVIDENCE]]
