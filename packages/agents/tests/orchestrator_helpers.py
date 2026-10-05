"""Synthetic data for the Orchestrator tests (T-044).

As in helpers.py, everything is made up, and text that comes from QRadar or from Triage's model
carries injection attempts on purpose. Triage's rationale and claim text carry markers: the
Orchestrator must never see them (decision T-45). So do the offense's free-text fields, which
it does not get either.
"""

import asyncio
from collections.abc import Mapping

from ais0c_agents import (
    AgentManifest,
    AgentRun,
    Budgets,
    CandidateSkill,
    OrchestratorAgent,
    OrchestratorTask,
    PlanAgent,
    PromptTemplate,
    SkillEvidence,
    TriageDecision,
    build_orchestrator_agent,
    load_manifest,
    load_prompt,
)
from ais0c_contracts import (
    AgentTask,
    Budget,
    CasePlan,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    DataGapReason,
    Level,
    OffenseSnapshot,
    RunStatus,
    SkillRef,
    TimeWindow,
    TriageResult,
    Usage,
)

from .helpers import (
    END,
    ESCAPE,
    INJECTION,
    NONCE,
    REPO_ROOT,
    SHARED_RULES,
    START,
    FakeClock,
    ScriptedModel,
    offense,
    registry,
)

ORCHESTRATOR_MANIFEST = REPO_ROOT / "config/agents/orchestrator.yaml"
ORCHESTRATOR_PROMPT = "prompts/orchestrator/v1.md"
ORCHESTRATOR_RUN_ID = "case-4711-orchestrator-1"

RATIONALE_MARKER = "RATIONALE-7Q2X"
CLAIM_MARKER = "CLAIM-TEXT-9K4M"
DESCRIPTION_MARKER = "DESCRIPTION-3H8V"
RULE_NAME_MARKER = "RULE-NAME-5T1C"
FOCUS = f"Successful logons by svc_backup_7731 after the failures {ESCAPE} {INJECTION}"

INVESTIGATION_BUDGETS = Budgets(tokens=150000, tool_calls=24, wall_clock_seconds=300)
VERIFICATION_BUDGETS = Budgets(tokens=80000, tool_calls=12, wall_clock_seconds=180)
PLAN_BUDGET = Budget(tokens=250000, tool_calls=40, seconds=480)
DCSYNC = SkillRef(skill_id="windows-dcsync", version="1.0.0", content_hash="sha256:" + "a" * 64)
DCSYNC_EVIDENCE = (
    SkillEvidence(
        id="replication-events",
        description="The 4662 events with a DS-Replication right: time, domain controller and "
        "subject account",
    ),
    SkillEvidence(
        id="request-source",
        description="Where the request came from: the source address of the account's logon",
    ),
)


def orchestrator_manifest(*, max_steps: int | None = None) -> AgentManifest:
    manifest = load_manifest(ORCHESTRATOR_MANIFEST, registry())
    return manifest if max_steps is None else manifest.model_copy(update={"max_steps": max_steps})


def orchestrator_prompt() -> PromptTemplate:
    return load_prompt(REPO_ROOT, ORCHESTRATOR_PROMPT, shared_rules=SHARED_RULES)


def orchestrator_agent_task(*, agent_id: str = "orchestrator") -> AgentTask:
    return AgentTask(
        task_id="task-4711-orchestrator-1",
        parent_run_id="run-4711",
        case_id="case-4711",
        agent_id=agent_id,
        agent_version="1.0.0",
        objective="Plan the rest of case case-4711.",
        context_refs=[],
        time_window=TimeWindow(start=START, end=END),
        budget=Budget(tokens=40000, tool_calls=0, seconds=90),
    )


def triage_result() -> TriageResult:
    """A TriageResult whose rationale and claim text the Orchestrator must not see."""
    return TriageResult(
        task_id="task-4711-1",
        status=RunStatus.COMPLETED,
        claims=[
            Claim(
                text=f"{CLAIM_MARKER}: 412 logon failures for svc_backup_7731.",
                evidence_ids=["ev_01JB3K4M5N6P7Q8R9S"],
            )
        ],
        data_gaps=[
            DataGap(
                source=f"Microsoft Windows Security Event Log {INJECTION}",
                period_start=START,
                period_end=END,
                reason=DataGapReason.NOT_PARSED,
            )
        ],
        injection_suspected=True,
        usage=Usage(tokens=30000, tool_calls=4, seconds=40.0),
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        ai_level=Level.HIGH,
        rationale=f"{RATIONALE_MARKER}: failures from an IOC address, then a logon.",
        needs_investigation=True,
        investigation_focus=[FOCUS],
    )


def plan_offense() -> OffenseSnapshot:
    return offense().model_copy(
        update={
            "description": f"{DESCRIPTION_MARKER} Multiple Login Failures {INJECTION}",
            "rule_names": [f"{RULE_NAME_MARKER} BF: Excessive logon failures"],
        }
    )


def dcsync_candidate() -> CandidateSkill:
    return CandidateSkill(
        ref=DCSYNC,
        agent_role="investigation",
        required_evidence=DCSYNC_EVIDENCE,
        budgets=Budgets(tokens=120000, tool_calls=24, wall_clock_seconds=300),
    )


def plan_agents() -> tuple[PlanAgent, ...]:
    return (
        PlanAgent(agent_id="investigation", budgets=INVESTIGATION_BUDGETS),
        PlanAgent(agent_id="verification", budgets=VERIFICATION_BUDGETS),
    )


def orchestrator_task(
    *, candidates: tuple[CandidateSkill, ...] | None = None, agent_id: str = "orchestrator"
) -> OrchestratorTask:
    return OrchestratorTask(
        task=orchestrator_agent_task(agent_id=agent_id),
        triage=TriageDecision.from_result(triage_result()),
        offense=plan_offense(),
        candidates=(dcsync_candidate(),) if candidates is None else candidates,
        agents=plan_agents(),
        plan_budget=PLAN_BUDGET,
    )


def plan_step(
    agent_id: str,
    *,
    skill: SkillRef | None = None,
    objective: str = "Establish whether svc_backup_7731 requested directory replication.",
    budget: Mapping[str, int] | None = None,
    start: str = "2026-10-02T13:00:00Z",
    end: str = "2026-10-02T14:00:00Z",
) -> dict[str, object]:
    """A plan step as the model writes it."""
    return {
        "agent_id": agent_id,
        "skill_id": skill.skill_id if skill else None,
        "skill_version": skill.version if skill else None,
        "objective": objective,
        "time_window": {"start": start, "end": end},
        "budget": dict(budget or {"tokens": 100000, "tool_calls": 20, "seconds": 240}),
    }


def plan_output(*steps: dict[str, object], injection_suspected: bool = True) -> dict[str, object]:
    """A valid model output: by default Investigation with DCSync, then Verification."""
    return {
        "steps": list(steps)
        or [
            plan_step("investigation", skill=DCSYNC),
            plan_step(
                "verification",
                objective="Check the replication claims against the evidence.",
                budget={"tokens": 60000, "tool_calls": 10, "seconds": 150},
            ),
        ],
        "injection_suspected": injection_suspected,
    }


def build_orchestrator(
    script: ScriptedModel, manifest: AgentManifest | None = None
) -> OrchestratorAgent:
    return build_orchestrator_agent(
        manifest=manifest or orchestrator_manifest(),
        prompt=orchestrator_prompt(),
        model=script.model,
    )


def run_orchestrator(
    agent: OrchestratorAgent, task: OrchestratorTask | None = None
) -> AgentRun[CasePlan]:
    return asyncio.run(
        agent.run(
            task or orchestrator_task(), run_id=ORCHESTRATOR_RUN_ID, nonce=NONCE, clock=FakeClock()
        )
    )
