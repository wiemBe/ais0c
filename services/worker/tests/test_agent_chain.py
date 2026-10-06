"""The agent chain inside the case worker (T-026 criteria 1, 2, 4, 6 and 7) with the real agents.

Triage, the Orchestrator, Investigation, Verification and Reporting are the real agents with
TemporalDurability; their models are scripted (worker_support.ChainModels) and their gateway is
the fake one, which records the evidence it returns as the real gateway does. Each test runs the
intake, waits for the case's decision and reads what the chain left: the runs, the report, the
urgent events, the QA items, the gateway's calls and the agents' prompts.
"""

from datetime import timedelta

import pytest
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from worker_support import (
    INVESTIGATION_CLAIM,
    MODEL_REGISTRY,
    OFFENSE_EVIDENCE,
    ChainModels,
    Platform,
    eventually,
    offense,
    running_platform,
)

from ais0c_activities import SessionFactory, load_model_releases
from ais0c_contracts import (
    CasePlan,
    CaseReport,
    CaseVerdict,
    InvestigationResult,
    Level,
    QAReason,
    RunStatus,
    VerificationResult,
)
from ais0c_storage.enums import CaseStatus
from ais0c_storage.models import CaseRow
from ais0c_storage.repositories import list_qa_items, list_urgent_events
from ais0c_workflows.names import BEGIN_AGENT_RUN, FINISH_AGENT_RUN, LOAD_EVIDENCE

pytestmark = pytest.mark.anyio

# What TriageModel writes and no later agent may see (T-45).
TRIAGE_RATIONALE = "Firewall accepts from one source; the offense record shows the volume."
TRIAGE_CLAIM = "The offense groups 12 firewall accepts."


async def decided(platform: Platform, offense_id: int) -> CaseRow:
    first = await platform.run_intake()
    await platform.env.sleep(timedelta(minutes=1))
    platform.source.put(offense(offense_id, start=await platform.env.get_current_time()))
    await platform.run_intake(first)
    return await platform.case_when(
        f"case-{offense_id}", lambda row: row.status is CaseStatus.DECIDED
    )


def scheduled(history: WorkflowHistory) -> list[str]:
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


async def test_the_chain_runs_the_real_agents_and_records_the_decision(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criteria 1, 3 and 6: every agent runs as its own child workflow and agent run; the case
    holds Investigation's decision, the report and its urgent event."""
    models = ChainModels(plan=("investigation", "verification"))
    async with running_platform(env, sessions, chain=models) as platform:
        case = await decided(platform, 80)
        runs = await platform.agent_runs("case-80")
        history = await env.client.get_workflow_handle("case-80-verification-1").fetch_history()
        async with sessions() as session:
            events = await list_urgent_events(session, "case-80")
            items = await list_qa_items(session, "case-80")

    assert [(run.run_id, run.agent_id, run.status) for run in runs] == [
        ("case-80-triage-1", "triage", RunStatus.COMPLETED),
        ("case-80-orchestrator-1", "orchestrator", RunStatus.COMPLETED),
        ("case-80-investigation-1", "investigation", RunStatus.COMPLETED),
        ("case-80-verification-1", "verification", RunStatus.COMPLETED),
        ("case-80-reporting-1", "reporting", RunStatus.COMPLETED),
    ]
    releases = load_model_releases(MODEL_REGISTRY)
    for run in runs:
        assert run.model_release == releases[run.model_alias], run.run_id
        assert run.task.parent_run_id == case.run_id
    by_agent = {run.agent_id: run for run in runs}
    assert isinstance(by_agent["orchestrator"].result, CasePlan)
    assert isinstance(by_agent["investigation"].result, InvestigationResult)
    assert isinstance(by_agent["verification"].result, VerificationResult)
    assert isinstance(by_agent["reporting"].result, CaseReport)
    assert (by_agent["investigation"].toolset_profile, by_agent["reporting"].toolset_profile) == (
        "qradar-investigate-read",
        "",
    )
    # The plan step's objective is the run's (T-48).
    assert by_agent["investigation"].task.objective == "Planned investigation step."
    # The cited evidence the run read from storage is its task's context.
    assert by_agent["investigation"].task.context_refs == [OFFENSE_EVIDENCE]

    # Investigation decided (T-42 (1)); its level is high, so is the case's.
    assert (case.verdict, case.ai_level, case.notify_level) == (
        CaseVerdict.TP,
        Level.HIGH,
        Level.HIGH,
    )
    report = case.report
    assert isinstance(report, CaseReport)
    assert (report.verdict, report.notify_level) == (CaseVerdict.TP, Level.HIGH)
    assert [claim.text for claim in report.claims] == [INVESTIGATION_CLAIM]
    assert [(event.evaluation_no, event.rank) for event in events] == [(1, 1)]
    assert events[0].event == report.urgent_events[0]
    assert events[0].event.evidence_id == OFFENSE_EVIDENCE
    # Triage suspected injection (TriageModel says so): the case goes to review.
    assert [item.reason for item in items] == [QAReason.INJECTION_SUSPECTED]

    # The chain agent's own activities are AgentWorkflow's.
    assert scheduled(history)[:2] == [BEGIN_AGENT_RUN, LOAD_EVIDENCE]
    assert scheduled(history)[-1] == FINISH_AGENT_RUN
    assert "agent__verification__model_request" in scheduled(history)


async def test_every_tool_call_carries_the_run_id_the_workflow_gave(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criterion 2 (T-29): the gateway sees each agent's calls under that agent's run."""
    models = ChainModels(plan=("investigation", "verification"))
    async with running_platform(env, sessions, chain=models) as platform:
        await decided(platform, 81)
        intents = list(platform.gateway.intents)

    assert [(intent.run_id, intent.agent_id) for intent in intents] == [
        ("case-81-triage-1", "triage"),
        ("case-81-investigation-1", "investigation"),
        ("case-81-verification-1", "verification"),
    ]
    assert models.run_ids() == [
        "case-81-orchestrator-1",
        "case-81-investigation-1",
        "case-81-verification-1",
        "case-81-reporting-1",
    ]


async def test_each_agent_sees_structured_results_and_no_earlier_free_text(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criterion 4 (T-45, T-48): the claims and the objective reach the next agent as
    untrusted `agent.*` data; Triage's rationale reaches none."""
    models = ChainModels(plan=("investigation", "verification"))
    async with running_platform(env, sessions, chain=models) as platform:
        await decided(platform, 82)

    for agent in ("orchestrator", "investigation", "verification", "reporting"):
        for text in models.instructions(agent):
            assert TRIAGE_RATIONALE not in text, agent
    [investigation] = models.instructions("investigation")[:1]
    assert TRIAGE_CLAIM in investigation
    assert 'source="agent.claim"' in investigation
    assert 'source="agent.objective"' in investigation
    assert "Planned investigation step." in investigation
    # Verification reviews Investigation's decision and claims.
    [verification] = models.instructions("verification")[:1]
    assert INVESTIGATION_CLAIM in verification
    assert TRIAGE_CLAIM not in verification
    assert "verdict: tp" in verification
    [reporting] = models.instructions("reporting")
    assert INVESTIGATION_CLAIM in reporting
    assert 'source="agent.urgent_event"' in reporting


async def test_a_disputed_claim_stays_out_of_the_report(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criteria 4 and 7: Verification contests Triage's claim; Reporting never sees it and the
    case goes to review with `verifier_conflict`."""
    models = ChainModels(plan=("verification",), disputes=True)
    async with running_platform(env, sessions, chain=models) as platform:
        case = await decided(platform, 83)
        async with sessions() as session:
            items = await list_qa_items(session, "case-83")

    [reporting] = models.instructions("reporting")
    assert TRIAGE_CLAIM not in reporting
    assert isinstance(case.report, CaseReport)
    assert case.report.claims == []
    assert {item.reason for item in items} == {
        QAReason.VERIFIER_CONFLICT,
        QAReason.INJECTION_SUSPECTED,
    }
    # The decision stays Triage's.
    assert (case.verdict, case.ai_level) == (CaseVerdict.SUSPICIOUS, Level.MEDIUM)


async def test_without_a_plan_the_default_plan_runs(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """Criterion 3: the Orchestrator's model is down for its run and the retry; Triage asked
    for an investigation, so the default plan runs Investigation and Verification."""
    models = ChainModels(fail=("orchestrator",))
    async with running_platform(env, sessions, chain=models) as platform:
        first = await platform.run_intake()
        await env.sleep(timedelta(minutes=1))
        platform.source.put(offense(84, start=await env.get_current_time()))
        await platform.run_intake(first)

        async def orchestrator_failed() -> bool | None:
            runs = await platform.agent_runs("case-84", "orchestrator")
            return True if any(run.status is RunStatus.FAILED for run in runs) else None

        await eventually(orchestrator_failed)
        await env.sleep(timedelta(minutes=5))
        case = await platform.case_when("case-84", lambda row: row.status is CaseStatus.DECIDED)
        runs = await platform.agent_runs("case-84")

    assert [(run.run_id, run.status) for run in runs if run.agent_id == "orchestrator"] == [
        ("case-84-orchestrator-1", RunStatus.FAILED),
        ("case-84-orchestrator-1-retry", RunStatus.FAILED),
    ]
    investigation = [run for run in runs if run.agent_id == "investigation"]
    assert [run.task.objective for run in investigation] == [
        "Investigate the offense with the general method and decide its verdict."
    ]
    assert case.verdict is CaseVerdict.TP
