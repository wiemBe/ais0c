"""AgentWorkflow: one chain agent run (T-026 criteria 1 and 2): the run record around the agent,
the evidence and skill it gets, its run ID (T-29), the wall clock budget and why a run failed.

The agents are ScriptedChainAgent, installed the way the worker installs the real ones; its
activities stand for the model and tool activities TemporalDurability creates and have their
names. The real agents are covered in services/worker. Time is skipped on the Temporal test
server.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowHandle
from temporalio.common import RetryPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker
from workflow_fakes import NO_USAGE, offense

from ais0c_contracts import (
    AgentResult,
    AgentTask,
    Budget,
    CaseVerdict,
    CatalogContext,
    Claim,
    Confidence,
    EnrichmentContext,
    EvidenceRef,
    Level,
    RunStatus,
    SkillRef,
    TimeWindow,
    Usage,
    VerificationResult,
)
from ais0c_workflows import (
    MODEL_ACCESS_FAILURES,
    AgentFailure,
    AgentOutcome,
    AgentRequest,
    AgentWorkflow,
    ChainResult,
    agent_runtime,
)
from ais0c_workflows.agent_runtime import (
    AgentKind,
    InvestigationInput,
    OrchestratorInput,
    ReportingInput,
    VerificationInput,
)
from ais0c_workflows.names import (
    BEGIN_AGENT_RUN,
    CASE_TASK_QUEUE,
    FINISH_AGENT_RUN,
    LOAD_EVIDENCE,
)

pytestmark = pytest.mark.anyio

RUN_ID = "case-101-verification-1"
NONCE = "fedcba9876543210"
START = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
WINDOW = TimeWindow(start=START, end=START + timedelta(hours=1))
OFFENSE = offense(101, start=START)
ENRICHMENT = EnrichmentContext(
    catalog=CatalogContext(rules=[], log_sources=[]),
    critical_asset_hits=[],
    ioc_hits=[],
    entity_resolutions=[],
)
CLAIM = Claim(text="svc_backup replicated the directory.", evidence_ids=["ev_2", "ev_1"])
SKILL = SkillRef(skill_id="windows-dcsync", version="1.0.0", content_hash="sha256:" + "0" * 64)

type ChainInput = OrchestratorInput | InvestigationInput | VerificationInput | ReportingInput


def inputs_of(agent: AgentKind) -> ChainInput:
    match agent:
        case AgentKind.ORCHESTRATOR:
            return OrchestratorInput(
                offense=OFFENSE,
                verdict=CaseVerdict.SUSPICIOUS,
                confidence=Confidence.MEDIUM,
                ai_level=Level.MEDIUM,
                needs_investigation=False,
                investigation_focus=(),
                data_gaps=(),
                injection_suspected=False,
                candidates=(),
                agents={},
                plan_budget=Budget(tokens=1, tool_calls=1, seconds=1),
            )
        case AgentKind.INVESTIGATION:
            return InvestigationInput(
                offense=OFFENSE,
                enrichment=ENRICHMENT,
                verdict=CaseVerdict.SUSPICIOUS,
                confidence=Confidence.MEDIUM,
                ai_level=Level.MEDIUM,
                investigation_focus=(),
                claims=(CLAIM,),
                data_gaps=(),
            )
        case AgentKind.VERIFICATION:
            return VerificationInput(
                offense=OFFENSE,
                verdict=CaseVerdict.SUSPICIOUS,
                confidence=Confidence.MEDIUM,
                ai_level=Level.MEDIUM,
                claims=(CLAIM,),
                critical=True,
            )
        case AgentKind.REPORTING:
            return ReportingInput(
                offense=OFFENSE,
                enrichment=ENRICHMENT,
                verdict=CaseVerdict.SUSPICIOUS,
                confidence=Confidence.MEDIUM,
                notify_level=Level.MEDIUM,
                claims=(CLAIM,),
                urgent_event_candidates=(),
                data_gaps=(),
            )


def request(
    agent: AgentKind = AgentKind.VERIFICATION,
    *,
    evidence_ids: tuple[str, ...] = ("ev_2", "ev_1"),
    skill: SkillRef | None = None,
    inputs: ChainInput | None = None,
) -> AgentRequest:
    return AgentRequest(
        case_id="case-101",
        evaluation_no=1,
        parent_run_id="case-run-1",
        objective="Check the claims.",
        time_window=WINDOW,
        budget=Budget(tokens=20000, tool_calls=6, seconds=120),
        skill=skill,
        evidence_ids=evidence_ids,
        inputs=inputs_of(agent) if inputs is None else inputs,
    )


def verification_result(task_id: str) -> VerificationResult:
    return VerificationResult(
        task_id=task_id,
        status=RunStatus.COMPLETED,
        claims=[],
        data_gaps=[],
        injection_suspected=False,
        usage=Usage(tokens=900, tool_calls=1, seconds=2.0),
        agrees=True,
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        disagreements=[],
        checked_evidence_ids=[],
    )


def evidence(evidence_id: str) -> EvidenceRef:
    return EvidenceRef.model_validate(
        {
            "evidence_id": evidence_id,
            "source": "qradar",
            "query_hash": "sha256:5d41402abc4b2a76",
            "query_text": "SELECT username FROM events",
            "time_start": START,
            "time_end": START + timedelta(hours=1),
            "identifiers": {"qid": "5000849"},
            "excerpt": "4662 on DC-01",
            "retrieved_at": START + timedelta(hours=1),
        }
    )


@dataclass(frozen=True)
class Report:
    status: RunStatus
    result: AgentResult | None
    usage: Usage
    error: str | None


@dataclass
class Received:
    task: AgentTask
    inputs: object
    evidence: list[EvidenceRef]
    skill: SkillRef | None
    run_id: str
    nonce: str


type Step = Callable[[str, int], ChainResult]


def answer(run_id: str, attempt: int) -> ChainResult:
    return verification_result(run_id)


@dataclass
class ChainFakes:
    """The run record activities of AgentWorkflow and the agent's scripted steps."""

    budget_seconds: int = 120
    skill_passes: bool = True
    tool_call: bool = False
    step: Step = answer
    tool_fails: bool = False
    begun: list[tuple[str, str, str, str, list[str], SkillRef | None]] = field(default_factory=list)
    loaded: list[list[str]] = field(default_factory=list)
    finished: list[tuple[str, RunStatus, AgentResult | None, str | None]] = field(
        default_factory=list
    )
    stepped: asyncio.Event = field(default_factory=asyncio.Event)

    def activities(self) -> list[Callable[..., object]]:
        return [
            self.begin_agent_run,
            self.load_evidence,
            self.finish_agent_run,
            self.model,
            self.tool,
        ]

    @activity.defn(name=BEGIN_AGENT_RUN)
    async def begin_agent_run(
        self,
        run_id: str,
        agent: str,
        case_id: str,
        parent_run_id: str,
        objective: str,
        time_window: TimeWindow,
        budget: Budget | None,
        context_refs: list[str],
        skill: SkillRef | None,
    ) -> tuple[AgentTask, str, SkillRef | None]:
        self.begun.append((run_id, agent, case_id, parent_run_id, context_refs, skill))
        task = AgentTask(
            task_id=run_id,
            parent_run_id=parent_run_id,
            case_id=case_id,
            agent_id=agent,
            agent_version="1.0.0",
            objective=objective,
            context_refs=context_refs,
            time_window=time_window,
            budget=Budget(tokens=20000, tool_calls=6, seconds=self.budget_seconds),
        )
        return task, NONCE, skill if self.skill_passes else None

    @activity.defn(name=LOAD_EVIDENCE)
    async def load_evidence(self, evidence_ids: list[str]) -> list[EvidenceRef]:
        self.loaded.append(evidence_ids)
        return [evidence(evidence_id) for evidence_id in evidence_ids]

    @activity.defn(name=FINISH_AGENT_RUN)
    async def finish_agent_run(
        self,
        run_id: str,
        status: RunStatus,
        result: ChainResult | None,
        usage: Usage,
        error: str | None,
    ) -> None:
        self.finished.append((run_id, status, result, error))

    @activity.defn(name="agent__verification__model_request")
    async def model(self, run_id: str) -> VerificationResult:
        self.stepped.set()
        result = self.step(run_id, activity.info().attempt)
        assert isinstance(result, VerificationResult)
        return result

    @activity.defn(name="agent__verification__toolset__gateway-qradar-verify-read__call_tool")
    async def tool(self, run_id: str) -> None:
        if self.tool_fails:
            raise ApplicationError("GatewayError: the gateway did not answer")


class ScriptedChainAgent:
    """Stands in for every chain agent: Verification plays the fakes' model (and tool) steps;
    the others answer at once with a result of their own kind."""

    def __init__(self, fakes: ChainFakes) -> None:
        self.fakes = fakes
        self.received: list[tuple[AgentKind, Received]] = []

    def installed(self) -> None:
        agent_runtime.install_chain_agents(
            orchestrator=self._as(AgentKind.ORCHESTRATOR),
            investigation=self._as(AgentKind.INVESTIGATION),
            verification=self._as(AgentKind.VERIFICATION),
            reporting=self._as(AgentKind.REPORTING),
        )

    def _as(self, kind: AgentKind) -> Any:  # noqa: ANN401 - a ChainAgentRun of any agent
        async def run(
            task: AgentTask,
            inputs: object,
            *,
            evidence: Sequence[EvidenceRef],
            skill: SkillRef | None,
            run_id: str,
            nonce: str,
        ) -> Report:
            if not workflow.unsafe.is_replaying():
                self.received.append(
                    (kind, Received(task, inputs, list(evidence), skill, run_id, nonce))
                )
            if isinstance(inputs, VerificationInput) and not inputs.claims:
                raise ValueError("the task holds no claim")
            if kind is not AgentKind.VERIFICATION:
                return Report(status=RunStatus.FAILED, result=None, usage=NO_USAGE, error="x")
            if self.fakes.tool_call:
                await workflow.execute_activity(
                    "agent__verification__toolset__gateway-qradar-verify-read__call_tool",
                    run_id,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                    cancellation_type=workflow.ActivityCancellationType.ABANDON,
                )
            result = await workflow.execute_activity(
                "agent__verification__model_request",
                run_id,
                result_type=VerificationResult,
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=RetryPolicy(maximum_attempts=3),
                cancellation_type=workflow.ActivityCancellationType.ABANDON,
            )
            return Report(status=RunStatus.COMPLETED, result=result, usage=result.usage, error=None)

        return run


@asynccontextmanager
async def running_agent(
    env: WorkflowEnvironment, fakes: ChainFakes, agent_request: AgentRequest, run_id: str = RUN_ID
) -> AsyncIterator[WorkflowHandle[AgentWorkflow, AgentOutcome[ChainResult]]]:
    async with Worker(
        env.client,
        task_queue=CASE_TASK_QUEUE,
        workflows=[AgentWorkflow],
        activities=fakes.activities(),
    ):
        yield await env.client.start_workflow(
            AgentWorkflow.run, agent_request, id=run_id, task_queue=CASE_TASK_QUEUE
        )


async def outcome_of(
    handle: WorkflowHandle[AgentWorkflow, AgentOutcome[ChainResult]],
) -> AgentOutcome[ChainResult]:
    """The workflow's result. Time is skipped only while the test waits on the handle that
    started the workflow."""
    return await handle.result()


async def test_a_run_is_recorded_around_the_agent_with_its_evidence_and_skill(
    env: WorkflowEnvironment,
) -> None:
    fakes = ChainFakes()
    agent = ScriptedChainAgent(fakes)
    agent.installed()

    async with running_agent(env, fakes, request(skill=SKILL)) as handle:
        outcome = await outcome_of(handle)

    assert outcome == AgentOutcome[ChainResult](
        run_id=RUN_ID,
        status=RunStatus.COMPLETED,
        result=verification_result(RUN_ID),
        usage=verification_result(RUN_ID).usage,
        error=None,
        failure=None,
    )
    assert fakes.begun == [
        (RUN_ID, "verification", "case-101", "case-run-1", ["ev_2", "ev_1"], SKILL)
    ]
    assert fakes.loaded == [["ev_2", "ev_1"]]
    assert fakes.finished == [(RUN_ID, RunStatus.COMPLETED, verification_result(RUN_ID), None)]
    [(kind, received)] = agent.received
    assert kind is AgentKind.VERIFICATION
    # T-29: the run ID comes from the workflow; the nonce from the run's record.
    assert (received.run_id, received.nonce, received.task.task_id) == (RUN_ID, NONCE, RUN_ID)
    assert [ref.evidence_id for ref in received.evidence] == ["ev_2", "ev_1"]
    assert received.skill == SKILL
    assert received.inputs == request().inputs


async def test_a_skill_that_fails_its_check_does_not_reach_the_agent(
    env: WorkflowEnvironment,
) -> None:
    fakes = ChainFakes(skill_passes=False)
    agent = ScriptedChainAgent(fakes)
    agent.installed()

    async with running_agent(env, fakes, request(skill=SKILL)) as handle:
        await handle.result()

    [(_, received)] = agent.received
    assert received.skill is None


async def test_without_context_refs_no_evidence_is_read(env: WorkflowEnvironment) -> None:
    fakes = ChainFakes()
    agent = ScriptedChainAgent(fakes)
    agent.installed()

    async with running_agent(env, fakes, request(evidence_ids=())) as handle:
        await handle.result()

    assert fakes.loaded == []
    [(_, received)] = agent.received
    assert received.evidence == []


@pytest.mark.parametrize("kind", list(AgentKind))
async def test_each_request_runs_its_own_agent(env: WorkflowEnvironment, kind: AgentKind) -> None:
    fakes = ChainFakes()
    agent = ScriptedChainAgent(fakes)
    agent.installed()
    run_id = f"case-101-{kind}-1"

    async with running_agent(env, fakes, request(kind), run_id=run_id) as handle:
        await handle.result()

    assert [received_kind for received_kind, _ in agent.received] == [kind]
    assert fakes.begun[0][:2] == (run_id, kind.value)


async def test_the_wall_clock_budget_ends_the_run(env: WorkflowEnvironment) -> None:
    def slow(run_id: str, attempt: int) -> ChainResult:
        raise ApplicationError("LiteLLM answered 429", next_retry_delay=timedelta(minutes=10))

    fakes = ChainFakes(budget_seconds=120, step=slow)
    ScriptedChainAgent(fakes).installed()

    async with running_agent(env, fakes, request()) as handle:
        await asyncio.wait_for(fakes.stepped.wait(), 10)
        outcome = await outcome_of(handle)

    assert (outcome.status, outcome.failure) == (RunStatus.BUDGET_EXHAUSTED, AgentFailure.TIMEOUT)
    assert outcome.failure in MODEL_ACCESS_FAILURES
    assert outcome.error == "the wall clock budget of 120 seconds ran out"
    assert fakes.finished[0][1:] == (
        RunStatus.BUDGET_EXHAUSTED,
        None,
        "the wall clock budget of 120 seconds ran out",
    )


async def test_a_failed_model_request_is_a_model_error(env: WorkflowEnvironment) -> None:
    def down(run_id: str, attempt: int) -> ChainResult:
        raise ApplicationError("model unavailable")

    fakes = ChainFakes(budget_seconds=3600, step=down)
    ScriptedChainAgent(fakes).installed()

    async with running_agent(env, fakes, request()) as handle:
        outcome = await outcome_of(handle)

    assert (outcome.status, outcome.failure) == (RunStatus.FAILED, AgentFailure.MODEL_ERROR)
    assert outcome.failure in MODEL_ACCESS_FAILURES


async def test_a_failed_tool_call_is_a_tool_error(env: WorkflowEnvironment) -> None:
    fakes = ChainFakes(tool_call=True, tool_fails=True)
    ScriptedChainAgent(fakes).installed()

    async with running_agent(env, fakes, request()) as handle:
        outcome = await outcome_of(handle)

    assert (outcome.status, outcome.failure) == (RunStatus.FAILED, AgentFailure.TOOL_ERROR)
    assert outcome.failure not in MODEL_ACCESS_FAILURES


async def test_an_input_the_agent_refuses_fails_the_run_not_the_workflow(
    env: WorkflowEnvironment,
) -> None:
    """A fault of the platform's input ends the run `failed` and is never retried."""
    fakes = ChainFakes()
    ScriptedChainAgent(fakes).installed()
    no_claims = VerificationInput(
        offense=OFFENSE,
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        ai_level=Level.MEDIUM,
        claims=(),
        critical=False,
    )
    refused = request(inputs=no_claims)

    async with running_agent(env, fakes, refused) as handle:
        outcome = await outcome_of(handle)

    assert (outcome.status, outcome.failure) == (RunStatus.FAILED, AgentFailure.INVALID_INPUT)
    assert outcome.error == "ValueError: the task holds no claim"
    assert fakes.finished[0][1] is RunStatus.FAILED


async def test_an_agent_run_history_replays(env: WorkflowEnvironment) -> None:
    fakes = ChainFakes(tool_call=True)
    ScriptedChainAgent(fakes).installed()
    async with running_agent(env, fakes, request(skill=SKILL)) as handle:
        await handle.result()

    replayer = Replayer(workflows=[AgentWorkflow], data_converter=pydantic_data_converter)
    await replayer.replay_workflow(await handle.fetch_history())


def test_without_installed_chain_agents_the_workflow_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("_orchestrator_agent", "_investigation_agent", "_verification_agent"):
        monkeypatch.setattr(agent_runtime, name, None)
    monkeypatch.setattr(agent_runtime, "_reporting_agent", None)

    for get in (
        agent_runtime.orchestrator_agent,
        agent_runtime.investigation_agent,
        agent_runtime.verification_agent,
        agent_runtime.reporting_agent,
    ):
        with pytest.raises(RuntimeError, match="is installed"):
            get()
