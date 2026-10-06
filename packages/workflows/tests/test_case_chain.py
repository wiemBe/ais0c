"""CaseWorkflow's agent chain (T-026 criteria 1, 3, 4, 6, 7 and 8).

Triage is TriageStub and every later agent AgentStub, whose scripted activity gets the request
CaseWorkflow built and answers for the agent. The tests read what each agent was handed, what
the case recorded and which runs started. IPs are from the RFC 5737 ranges.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflow_fakes import (
    MANIFEST_BUDGETS,
    PLAN_BUDGET,
    AgentBehavior,
    AgentCall,
    AgentStub,
    CaseFakes,
    ChainResult,
    TriageBehavior,
    TriageCall,
    TriageStub,
    agent_failure,
    answer_agents,
    executor_worker,
    investigation_of,
    offense,
    plan_of,
    triage_failure,
    verification_of,
)

from ais0c_contracts import (
    Budget,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    DataGapReason,
    Disagreement,
    Level,
    PlanStep,
    QAReason,
    RunStatus,
    SkillRef,
    TimeWindow,
    TriageResult,
    UrgentEvent,
    VerificationResult,
)
from ais0c_workflows import AgentFailure, AgentOutcome, CaseStatus, CaseView, CaseWorkflow
from ais0c_workflows.agent_runtime import (
    AgentKind,
    InvestigationInput,
    OrchestratorInput,
    ReportingInput,
    VerificationInput,
)
from ais0c_workflows.names import CASE_TASK_QUEUE, OFFENSE_CLOSED
from ais0c_workflows.plan import DEFAULT_OBJECTIVES

pytestmark = pytest.mark.anyio

OFFENSE_ID = 101
CASE_ID = "case-101"
# Free text of the agents: none of it may reach a later agent (T-45).
RATIONALE = "Triage rationale that stays with Triage."
HYPOTHESIS = "Investigation hypothesis that stays with Investigation."
TIMELINE = "Investigation timeline entry that stays with Investigation."
DISAGREEMENT = "Verification reason that stays with Verification."
SKILL = SkillRef(
    skill_id="windows-dcsync",
    version="1.0.0",
    content_hash="sha256:" + "ab" * 32,
)
SKILL_BUDGET = Budget(tokens=120000, tool_calls=24, seconds=300)


def claim(text: str, *evidence_ids: str) -> Claim:
    return Claim(text=text, evidence_ids=list(evidence_ids))


TRIAGE_CLAIMS = (
    claim("svc_backup replicated the directory from 198.51.100.15.", "ev_1", "ev_2"),
    claim("198.51.100.15 is not a domain controller.", "ev_2"),
)
INVESTIGATION_CLAIMS = (
    claim("The replication used DS-Replication-Get-Changes-All.", "ev_3"),
    claim("No other account replicated in the window.", "ev_4"),
)


def triage(
    *,
    verdict: CaseVerdict = CaseVerdict.SUSPICIOUS,
    confidence: Confidence = Confidence.MEDIUM,
    ai_level: Level = Level.MEDIUM,
    needs_investigation: bool = True,
    claims: tuple[Claim, ...] = TRIAGE_CLAIMS,
    data_gaps: tuple[DataGap, ...] = (),
    injection_suspected: bool = False,
) -> TriageResult:
    return TriageResult.model_validate(
        {
            "task_id": "case-101-triage-1",
            "status": RunStatus.COMPLETED,
            "claims": list(claims),
            "data_gaps": list(data_gaps),
            "injection_suspected": injection_suspected,
            "usage": {"tokens": 0, "tool_calls": 0, "seconds": 0.0},
            "verdict": verdict,
            "confidence": confidence,
            "ai_level": ai_level,
            "rationale": RATIONALE,
            "needs_investigation": needs_investigation,
            "investigation_focus": ["Replication rights of svc_backup"],
        }
    )


def gap(start: datetime) -> DataGap:
    return DataGap(
        source="Microsoft Windows Security Event Log",
        period_start=start,
        period_end=start + timedelta(hours=1),
        reason=DataGapReason.NO_DATA,
    )


def urgent_event(evidence_id: str, at: datetime) -> UrgentEvent:
    return UrgentEvent(
        rank=1,
        time=at,
        log_source="DC-01",
        event_name="Directory Service Access",
        qid=5000849,
        source="198.51.100.15",
        reason="Directory replication by a non-machine account.",
        checklist=["Is svc_backup an approved directory sync account?"],
        evidence_id=evidence_id,
    )


def deciding(result: TriageResult) -> TriageBehavior:
    async def behavior(call: TriageCall) -> TriageResult:
        return result

    return behavior


async def investigates(call: AgentCall) -> ChainResult:
    """A plan of Investigation and Verification; Investigation decides `tp`/high with its own
    claims, hypotheses, timeline and one urgent event candidate."""
    match call.agent:
        case AgentKind.ORCHESTRATOR:
            return plan_of(call, "investigation", "verification")
        case AgentKind.INVESTIGATION:
            at = call.request.time_window.start
            return investigation_of(
                call,
                verdict=CaseVerdict.TP,
                confidence=Confidence.HIGH,
                ai_level=Level.HIGH,
                claims=list(INVESTIGATION_CLAIMS),
                hypotheses=[{"text": HYPOTHESIS, "status": "supported"}],
                timeline=[{"time": at, "description": TIMELINE, "evidence_ids": ["ev_3"]}],
                urgent_event_candidates=[urgent_event("ev_5", at)],
            )
        case _:
            return await answer_agents(call)


@asynccontextmanager
async def running_case(
    env: WorkflowEnvironment, fakes: CaseFakes
) -> AsyncIterator[WorkflowHandle[CaseWorkflow, CaseView]]:
    async with executor_worker(env, fakes):
        async with Worker(
            env.client,
            task_queue=CASE_TASK_QUEUE,
            workflows=[CaseWorkflow, TriageStub, AgentStub],
            activities=fakes.activities(),
        ):
            yield await env.client.start_workflow(
                CaseWorkflow.run, args=[OFFENSE_ID], id=CASE_ID, task_queue=CASE_TASK_QUEUE
            )


async def decided_once(fakes: CaseFakes, env: WorkflowEnvironment) -> CaseFakes:
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("decided", 1)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()
    return fakes


async def chain(
    env: WorkflowEnvironment,
    *,
    result: TriageResult | None = None,
    agents: AgentBehavior = answer_agents,
    floor_level: Level | None = None,
    candidates: tuple[tuple[str, SkillRef, Budget], ...] = (),
) -> CaseFakes:
    now = await env.get_current_time()
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now),
        floor_level=floor_level,
        triage_behavior=deciding(triage() if result is None else result),
        agent_behavior=agents,
        candidates=candidates,
    )
    return await decided_once(fakes, env)


def only[T](items: list[T]) -> T:
    assert len(items) == 1, items
    return items[0]


# --- the chain -------------------------------------------------------------------------------


async def test_the_chain_runs_the_plan_and_reporting_after_triage(
    env: WorkflowEnvironment,
) -> None:
    """Criteria 1 and 3: the runs' IDs and their order; the decision is Investigation's."""
    fakes = await chain(env, agents=investigates, floor_level=Level.MEDIUM)

    assert fakes.run_ids() == [
        "case-101-orchestrator-1",
        "case-101-investigation-1",
        "case-101-verification-1",
        "case-101-reporting-1",
    ]
    decision = only(fakes.chain_decisions)
    assert (decision.verdict, decision.confidence, decision.ai_level) == (
        CaseVerdict.TP,
        Confidence.HIGH,
        Level.HIGH,
    )
    assert (decision.notify_level, decision.floor_level) == (Level.HIGH, Level.MEDIUM)
    assert decision.report is not None
    assert decision.report.task_id == "case-101-reporting-1"
    assert (decision.report.verdict, decision.report.notify_level) == (CaseVerdict.TP, Level.HIGH)
    assert decision.qa_reasons == []
    assert decision.rule_ids == [100201]
    assert fakes.plans == []


async def test_each_agent_gets_the_structured_results_before_it(
    env: WorkflowEnvironment,
) -> None:
    """Criterion 4 (T-45): what each agent is handed; no rationale, hypothesis, timeline or
    other free text of an earlier agent."""
    fakes = await chain(env, agents=investigates)

    orchestrator = only(fakes.requests(AgentKind.ORCHESTRATOR))
    assert isinstance(orchestrator.inputs, OrchestratorInput)
    assert (orchestrator.inputs.verdict, orchestrator.inputs.needs_investigation) == (
        CaseVerdict.SUSPICIOUS,
        True,
    )
    assert orchestrator.inputs.investigation_focus == ("Replication rights of svc_backup",)
    assert (orchestrator.inputs.agents, orchestrator.inputs.plan_budget) == (
        MANIFEST_BUDGETS,
        PLAN_BUDGET,
    )
    assert (orchestrator.budget, orchestrator.skill, orchestrator.evidence_ids) == (None, None, ())

    investigation = only(fakes.requests(AgentKind.INVESTIGATION))
    assert isinstance(investigation.inputs, InvestigationInput)
    assert investigation.inputs.claims == TRIAGE_CLAIMS
    assert investigation.evidence_ids == ("ev_1", "ev_2")
    # The plan step's objective, window and budget are the task's (T-48).
    assert investigation.objective == "Planned investigation step."
    assert investigation.budget == Budget(tokens=20000, tool_calls=6, seconds=120)

    verification = only(fakes.requests(AgentKind.VERIFICATION))
    assert isinstance(verification.inputs, VerificationInput)
    # Verification reviews the decision: Investigation's, with its claims.
    assert (verification.inputs.verdict, verification.inputs.ai_level) == (
        CaseVerdict.TP,
        Level.HIGH,
    )
    assert verification.inputs.claims == INVESTIGATION_CLAIMS
    assert verification.evidence_ids == ("ev_3", "ev_4")
    assert verification.objective == "Planned verification step."

    reporting = only(fakes.requests(AgentKind.REPORTING))
    assert isinstance(reporting.inputs, ReportingInput)
    assert (reporting.inputs.verdict, reporting.inputs.notify_level) == (
        CaseVerdict.TP,
        Level.HIGH,
    )
    assert reporting.inputs.claims == INVESTIGATION_CLAIMS
    assert [event.evidence_id for event in reporting.inputs.urgent_event_candidates] == ["ev_5"]
    # The claims' evidence and the candidates'.
    assert reporting.evidence_ids == ("ev_3", "ev_4", "ev_5")

    for call in fakes.agent_calls:
        handed = call.request.model_dump_json()
        for text in (RATIONALE, HYPOTHESIS, TIMELINE):
            assert text not in handed, (call.agent, text)
        assert "rationale" not in handed


async def test_without_investigation_in_the_plan_triage_decides(env: WorkflowEnvironment) -> None:
    """The default answers plan Verification alone: it reviews Triage's claims, and Reporting
    gets no urgent event candidates."""
    fakes = await chain(env, result=triage(needs_investigation=False))

    assert fakes.run_ids() == [
        "case-101-orchestrator-1",
        "case-101-verification-1",
        "case-101-reporting-1",
    ]
    verification = only(fakes.requests(AgentKind.VERIFICATION))
    assert isinstance(verification.inputs, VerificationInput)
    assert verification.inputs.claims == TRIAGE_CLAIMS
    reporting = only(fakes.requests(AgentKind.REPORTING))
    assert isinstance(reporting.inputs, ReportingInput)
    assert reporting.inputs.urgent_event_candidates == ()
    decision = only(fakes.chain_decisions)
    assert (decision.verdict, decision.ai_level) == (CaseVerdict.SUSPICIOUS, Level.MEDIUM)


async def test_a_step_runs_with_the_skill_the_plan_chose(env: WorkflowEnvironment) -> None:
    """The step's skill reaches the run with its content hash, for begin_agent_run to check."""

    async def with_skill(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.ORCHESTRATOR:
            plan = plan_of(call, "investigation", "verification")
            step = plan.steps[0].model_copy(
                update={"skill_id": SKILL.skill_id, "skill_version": SKILL.version}
            )
            return plan.model_copy(update={"steps": [step, plan.steps[1]]})
        return await answer_agents(call)

    fakes = await chain(
        env, agents=with_skill, candidates=(("investigation", SKILL, SKILL_BUDGET),)
    )

    orchestrator = only(fakes.requests(AgentKind.ORCHESTRATOR))
    assert isinstance(orchestrator.inputs, OrchestratorInput)
    [candidate] = orchestrator.inputs.candidates
    assert (candidate.agent_id, candidate.skill, candidate.budget) == (
        "investigation",
        SKILL,
        SKILL_BUDGET,
    )
    assert only(fakes.requests(AgentKind.INVESTIGATION)).skill == SKILL
    assert only(fakes.requests(AgentKind.VERIFICATION)).skill is None


@pytest.mark.parametrize(
    ("verdict", "ai_level", "floor", "critical"),
    [
        pytest.param(CaseVerdict.FP, Level.LOW, None, True, id="fp"),
        pytest.param(CaseVerdict.SUSPICIOUS, Level.HIGH, None, True, id="high"),
        pytest.param(CaseVerdict.TP, Level.MEDIUM, Level.CRITICAL, True, id="critical-floor"),
        pytest.param(CaseVerdict.SUSPICIOUS, Level.MEDIUM, Level.LOW, False, id="medium"),
    ],
)
async def test_claims_are_critical_for_fp_or_a_high_level(
    env: WorkflowEnvironment,
    verdict: CaseVerdict,
    ai_level: Level,
    floor: Level | None,
    critical: bool,
) -> None:
    """Criterion 4: all of the decision's claims are critical when it is FP or its notification
    level is high or critical."""
    result = triage(verdict=verdict, ai_level=ai_level, needs_investigation=False)
    fakes = await chain(env, result=result, floor_level=floor)

    verification = only(fakes.requests(AgentKind.VERIFICATION))
    assert isinstance(verification.inputs, VerificationInput)
    assert verification.inputs.critical is critical


async def test_disputed_claims_do_not_reach_reporting(env: WorkflowEnvironment) -> None:
    """Criterion 4 (T-51): a claim Verification disputes, named by its exact text, stays out
    of the report; the conflict goes to operator review."""

    async def disputes(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.VERIFICATION:
            contested = Disagreement(claim_text=TRIAGE_CLAIMS[1].text, reason=DISAGREEMENT)
            near_miss = Disagreement(claim_text=TRIAGE_CLAIMS[0].text + " ", reason=DISAGREEMENT)
            return verification_of(call, agrees=False, disagreements=[contested, near_miss])
        return await answer_agents(call)

    fakes = await chain(env, result=triage(needs_investigation=False), agents=disputes)

    reporting = only(fakes.requests(AgentKind.REPORTING))
    assert isinstance(reporting.inputs, ReportingInput)
    assert reporting.inputs.claims == TRIAGE_CLAIMS[:1]
    assert reporting.evidence_ids == ("ev_1", "ev_2")
    assert DISAGREEMENT not in reporting.model_dump_json()
    decision = only(fakes.chain_decisions)
    assert decision.qa_reasons == [QAReason.VERIFIER_CONFLICT]
    # The decision does not change (T-42 (3)).
    assert decision.verdict is CaseVerdict.SUSPICIOUS


# --- a link without a result -----------------------------------------------------------------


async def test_without_a_plan_the_default_plan_runs(env: WorkflowEnvironment) -> None:
    """Criterion 3: the Orchestrator gives no result; Triage asked for an investigation, so
    the default plan is Investigation and Verification. The reason is recorded."""

    async def no_plan(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.ORCHESTRATOR:
            raise agent_failure(AgentFailure.INVALID_OUTPUT)
        return await answer_agents(call)

    fakes = await chain(env, agents=no_plan)

    assert fakes.run_ids() == [
        "case-101-orchestrator-1",
        "case-101-investigation-1",
        "case-101-verification-1",
        "case-101-reporting-1",
    ]
    investigation = only(fakes.requests(AgentKind.INVESTIGATION))
    assert investigation.objective == DEFAULT_OBJECTIVES["investigation"]
    assert investigation.budget == MANIFEST_BUDGETS["investigation"]
    plan = only(fakes.plans)
    assert (plan.case_id, plan.run_id, plan.reason, plan.step) == (
        CASE_ID,
        "case-101-orchestrator-1",
        "no_plan",
        None,
    )
    assert plan.steps == ["investigation", "verification"]


async def test_a_rejected_plan_is_replaced_and_its_reason_recorded(
    env: WorkflowEnvironment,
) -> None:
    """Criterion 3 (T-41): a plan with Triage as a step is rejected; without Triage asking for
    an investigation the default plan is Verification alone."""

    async def plans_triage(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.ORCHESTRATOR:
            return plan_of(call, "triage", "verification")
        return await answer_agents(call)

    fakes = await chain(env, result=triage(needs_investigation=False), agents=plans_triage)

    assert AgentKind.INVESTIGATION not in {call.agent for call in fakes.agent_calls}
    plan = only(fakes.plans)
    assert (plan.reason, plan.step, plan.steps) == ("not_a_plan_agent", 1, ["verification"])
    assert plan.detail is not None


async def test_without_investigation_the_decision_stays_triages(
    env: WorkflowEnvironment,
) -> None:
    async def investigation_fails(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.INVESTIGATION:
            raise agent_failure(AgentFailure.TOOL_ERROR)
        return await investigates(call)

    fakes = await chain(env, agents=investigation_fails)

    verification = only(fakes.requests(AgentKind.VERIFICATION))
    assert isinstance(verification.inputs, VerificationInput)
    assert verification.inputs.claims == TRIAGE_CLAIMS
    decision = only(fakes.chain_decisions)
    assert (decision.verdict, decision.ai_level) == (CaseVerdict.SUSPICIOUS, Level.MEDIUM)
    assert decision.qa_reasons == []


async def test_without_verification_the_case_goes_to_review(env: WorkflowEnvironment) -> None:
    async def verification_fails(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.VERIFICATION:
            raise agent_failure(AgentFailure.BUDGET_EXHAUSTED)
        return await answer_agents(call)

    fakes = await chain(env, agents=verification_fails)

    decision = only(fakes.chain_decisions)
    assert decision.qa_reasons == [QAReason.VERIFIER_CONFLICT]
    assert decision.report is not None
    reporting = only(fakes.requests(AgentKind.REPORTING))
    assert isinstance(reporting.inputs, ReportingInput)
    assert reporting.inputs.claims == TRIAGE_CLAIMS


async def test_without_reporting_the_decision_is_recorded_without_a_report(
    env: WorkflowEnvironment,
) -> None:
    async def reporting_fails(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.REPORTING:
            raise agent_failure(AgentFailure.INVALID_OUTPUT)
        return await answer_agents(call)

    fakes = await chain(env, agents=reporting_fails)

    decision = only(fakes.chain_decisions)
    assert decision.report is None
    assert (decision.verdict, decision.notify_level) == (CaseVerdict.SUSPICIOUS, Level.MEDIUM)


async def test_an_agent_the_model_outage_ended_runs_once_more(env: WorkflowEnvironment) -> None:
    """Criterion 1 (D-33): the retry waits the retry delay and has the `-retry` ID."""

    async def model_down_once(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.ORCHESTRATOR and not call.retry:
            raise agent_failure(AgentFailure.MODEL_ERROR)
        return await answer_agents(call)

    now = await env.get_current_time()
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now),
        triage_behavior=deciding(triage()),
        agent_behavior=model_down_once,
    )
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("retry")
        await env.sleep(timedelta(minutes=5))
        await fakes.events.wait_for("decided", 1)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    assert fakes.run_ids()[:2] == ["case-101-orchestrator-1", "case-101-orchestrator-1-retry"]
    assert fakes.plans == []


async def test_without_a_triage_decision_no_later_agent_runs(env: WorkflowEnvironment) -> None:
    async def no_decision(call: TriageCall) -> TriageResult:
        raise triage_failure(AgentFailure.INVALID_OUTPUT)

    now = await env.get_current_time()
    fakes = CaseFakes(offense(OFFENSE_ID, start=now), triage_behavior=no_decision)
    async with running_case(env, fakes) as handle:
        await fakes.events.wait_for("no_ai_decision", 1)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    assert fakes.agent_calls == []
    assert fakes.chain_decisions == []


# --- operator review -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent",
    [
        AgentKind.ORCHESTRATOR,
        AgentKind.INVESTIGATION,
        AgentKind.VERIFICATION,
        AgentKind.REPORTING,
    ],
)
async def test_injection_suspected_by_any_agent_goes_to_review(
    env: WorkflowEnvironment, agent: AgentKind
) -> None:
    """Criterion 7: `injection_suspected` in any agent's output."""

    async def suspects(call: AgentCall) -> ChainResult:
        result = await investigates(call)
        if call.agent is agent:
            return result.model_copy(update={"injection_suspected": True})
        return result

    fakes = await chain(env, agents=suspects)

    assert only(fakes.chain_decisions).qa_reasons == [QAReason.INJECTION_SUSPECTED]


async def test_injection_suspected_by_triage_goes_to_review(env: WorkflowEnvironment) -> None:
    fakes = await chain(env, result=triage(needs_investigation=False, injection_suspected=True))

    assert only(fakes.chain_decisions).qa_reasons == [QAReason.INJECTION_SUSPECTED]


async def test_low_confidence_goes_to_review(env: WorkflowEnvironment) -> None:
    result = triage(confidence=Confidence.LOW, needs_investigation=False)
    fakes = await chain(env, result=result)

    assert only(fakes.chain_decisions).qa_reasons == [QAReason.LOW_CONFIDENCE]


async def test_fp_with_a_data_gap_goes_to_review(env: WorkflowEnvironment) -> None:
    start = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    result = triage(
        verdict=CaseVerdict.FP,
        ai_level=Level.LOW,
        needs_investigation=False,
        data_gaps=(gap(start),),
    )
    fakes = await chain(env, result=result)

    decision = only(fakes.chain_decisions)
    assert decision.qa_reasons == [QAReason.FP_WITH_DATA_GAP]
    reporting = only(fakes.requests(AgentKind.REPORTING))
    assert isinstance(reporting.inputs, ReportingInput)
    assert reporting.inputs.data_gaps == (gap(start),)


async def test_a_verifier_with_another_verdict_goes_to_review(env: WorkflowEnvironment) -> None:
    async def other_verdict(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.VERIFICATION:
            return verification_of(call, verdict=CaseVerdict.TP)
        return await answer_agents(call)

    fakes = await chain(env, result=triage(needs_investigation=False), agents=other_verdict)

    assert only(fakes.chain_decisions).qa_reasons == [QAReason.VERIFIER_CONFLICT]


# --- SLA and closing -------------------------------------------------------------------------


async def test_the_sla_covers_the_whole_chain(env: WorkflowEnvironment) -> None:
    """Criterion 8: Reporting finishes after the SLA; the case is `no_ai_decision` meanwhile
    and the late decision replaces it (D-30)."""

    async def slow_report(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.REPORTING and call.attempt == 1:
            raise ApplicationError("model unavailable", next_retry_delay=timedelta(minutes=15))
        return await answer_agents(call)

    now = await env.get_current_time()
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now),
        sla=timedelta(minutes=10),
        triage_behavior=deciding(triage(needs_investigation=False)),
        agent_behavior=slow_report,
    )
    async with running_case(env, fakes) as handle:
        await fakes.agent_events.wait_for("reporting", 1)
        await env.sleep(timedelta(minutes=11))
        await fakes.events.wait_for("no_ai_decision", 1)
        assert fakes.chain_decisions == []
        await env.sleep(timedelta(minutes=10))
        await fakes.events.wait_for("decided", 1)
        state = await handle.query(CaseWorkflow.state)
        await handle.signal(OFFENSE_CLOSED)
        await handle.result()

    assert state.status is CaseStatus.DECIDED
    assert only(fakes.chain_decisions).report is not None


async def test_closing_the_case_abandons_the_chain(env: WorkflowEnvironment) -> None:
    """Criterion 8: the agent running when the offense is closed is abandoned, not cancelled,
    and nothing is recorded for the evaluation."""
    release = asyncio.Event()

    async def verification_waits(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.VERIFICATION:
            await release.wait()
        return await answer_agents(call)

    now = await env.get_current_time()
    fakes = CaseFakes(
        offense(OFFENSE_ID, start=now),
        triage_behavior=deciding(triage(needs_investigation=False)),
        agent_behavior=verification_waits,
    )
    async with running_case(env, fakes) as handle:
        await fakes.agent_events.wait_for("verification", 1)
        await handle.signal(OFFENSE_CLOSED)
        result = await handle.result()
        run = env.client.get_workflow_handle(
            "case-101-verification-1", result_type=AgentOutcome[VerificationResult]
        )
        assert (await run.describe()).status is WorkflowExecutionStatus.RUNNING
        release.set()
        outcome = await run.result()

    assert result.status is CaseStatus.CLOSED
    assert outcome.status is RunStatus.COMPLETED
    assert AgentKind.REPORTING not in {call.agent for call in fakes.agent_calls}
    assert fakes.chain_decisions == []


async def test_a_plan_step_window_lies_inside_the_evaluation_window(
    env: WorkflowEnvironment,
) -> None:
    """The step's window is clipped to the evaluation's (T-41 rule 5)."""

    async def wide_step(call: AgentCall) -> ChainResult:
        if call.agent is AgentKind.ORCHESTRATOR:
            window = call.request.time_window
            wide = TimeWindow(start=window.start - timedelta(days=3), end=window.end)
            step = PlanStep(
                agent_id="verification",
                objective="Check the claims.",
                time_window=wide,
                budget=Budget(tokens=1000, tool_calls=2, seconds=60),
            )
            return plan_of(call, "verification").model_copy(update={"steps": [step]})
        return await answer_agents(call)

    fakes = await chain(env, result=triage(needs_investigation=False), agents=wide_step)

    orchestrator = only(fakes.requests(AgentKind.ORCHESTRATOR))
    verification = only(fakes.requests(AgentKind.VERIFICATION))
    assert verification.time_window == orchestrator.time_window


async def test_reporting_gets_the_evaluation_window_and_no_plan_budget(
    env: WorkflowEnvironment,
) -> None:
    fakes = await chain(env, result=triage(needs_investigation=False))

    orchestrator = only(fakes.requests(AgentKind.ORCHESTRATOR))
    reporting = only(fakes.requests(AgentKind.REPORTING))
    assert reporting.time_window == orchestrator.time_window
    assert (reporting.budget, reporting.skill) == (None, None)
    assert reporting.objective == "Write the report of QRadar offense 101 (evaluation 1)."
    assert orchestrator.objective == (
        "Plan the rest of the evaluation of QRadar offense 101 (evaluation 1)."
    )
