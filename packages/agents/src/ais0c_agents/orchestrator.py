"""The Orchestrator agent (architecture §7, "Orchestrator"): the plan for the rest of a case.

It plans and never investigates: it has no tools and sees no agent's conversation. Its input is
an OrchestratorTask (decision T-45): Triage's structured decision without its rationale or claim
texts, the offense, the candidate skills the router listed, the plan agents with their manifest
budgets and the plan budget. Each part reaches the model in its trust layer (architecture §22):

- the candidates' manifest information is approved content and part of the prompt (§7,
  "Seçim" 4), as are the agents, the budgets, the evaluation window and Triage's verdict,
  confidence, level and flags, which are platform values;
- Triage's investigation focus and data gaps are model text, and the offense's fields come from
  QRadar, so all three are inside the `untrusted_*` wrapper, each in its own block: the focus as
  `agent.focus`, the data gaps as `agent.data_gap` (decision T-48), the offense as
  `qradar.offense`. The offense's free text (`description`, `rule_names`) stays out: the agent
  gets the offense's structured fields (T-45).

The model returns an OrchestratorOutput, checked against its schema only. Whether the plan may
run is decided by the workflow (ais0c_workflows.plan, decision T-41), which replaces an invalid
plan with the default one. The run adds the task ID, status and usage to make the CasePlan.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator
from pydantic_ai import Agent, AgentRetries
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from ais0c_agents.builder import AgentSpec, check_agent_config, create_agent
from ais0c_agents.manifest import AgentManifest, Budgets, Name
from ais0c_agents.prompts import PromptTemplate, wrap_json_lines
from ais0c_agents.runner import AgentRun, run_agent, usage_limits
from ais0c_agents.skills import SkillEvidence
from ais0c_agents.toolset import RunDeps
from ais0c_contracts import (
    AgentTask,
    Budget,
    CasePlan,
    CaseVerdict,
    Confidence,
    DataGap,
    Level,
    OffenseSnapshot,
    PlanStep,
    RunStatus,
    ShortText,
    SkillRef,
    TriageResult,
    Usage,
)
from ais0c_policy import neutralize_tags

INPUT_SCHEMA: Final = "OrchestratorTask"
OUTPUT_SCHEMA: Final = "CasePlan"
# No tools to retry; corrections the model gets for invalid output.
RETRIES: Final[AgentRetries] = {"tools": 0, "output": 2}
# The template's inputs besides the shared rules (prompts/orchestrator/v1.md).
PLACEHOLDERS: Final = frozenset(
    {
        "evaluation_window",
        "triage",
        "triage_notes",
        "offense",
        "agents",
        "plan_budget",
        "candidates",
    }
)
OFFENSE_SOURCE: Final = "qradar.offense"
# Triage's model text about the offense (decision T-48): neither QRadar data nor knowledge.
FOCUS_SOURCE: Final = "agent.focus"
DATA_GAP_SOURCE: Final = "agent.data_gap"
# The offense's free-text fields; the Orchestrator gets the structured ones (T-45).
OFFENSE_FREE_TEXT: Final = frozenset({"description", "rule_names"})
NO_CANDIDATES: Final = "No skill is a candidate for this offense: plan every step without a skill."


# Not ContractModels: contract models are defined only in packages/contracts.
class TriageDecision(BaseModel):
    """What the Orchestrator sees of the TriageResult (T-45): no rationale and no claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    needs_investigation: bool
    investigation_focus: Annotated[tuple[ShortText, ...], Field(max_length=5)]
    data_gaps: tuple[DataGap, ...]
    injection_suspected: bool

    @classmethod
    def from_result(cls, result: TriageResult) -> Self:
        return cls(
            verdict=result.verdict,
            confidence=result.confidence,
            ai_level=result.ai_level,
            needs_investigation=result.needs_investigation,
            investigation_focus=tuple(result.investigation_focus),
            data_gaps=tuple(result.data_gaps),
            injection_suspected=result.injection_suspected,
        )


# As ais0c_knowledge.skills.manifest.Summary; the agents package cannot import knowledge.
SkillSummary = Annotated[str, StringConstraints(max_length=200, pattern=r"^[!-~][ -~]*\.$")]


class CandidateSkill(BaseModel):
    """A skill the router listed for one agent (ais0c_knowledge.skills.candidate_skills).

    The activity that builds the task takes it from the loader's Skill: `ref` from its ID,
    version and content hash, the rest from its manifest.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: SkillRef
    agent_role: Name
    """The agent the router listed it for; a plan step of that agent may use it."""
    summary: SkillSummary
    """What the skill investigates; the Orchestrator chooses between candidates by it."""
    required_evidence: Annotated[tuple[SkillEvidence, ...], Field(min_length=1, max_length=10)]
    budgets: Budgets


class PlanAgent(BaseModel):
    """An agent a plan step can run, with its manifest budget."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: Name
    budgets: Budgets


class OrchestratorTask(BaseModel):
    """Input of the Orchestrator agent; `input_schema: OrchestratorTask` in its manifest.

    `task.time_window` is the evaluation's window; plan steps lie inside it.
    """

    model_config = ConfigDict(extra="forbid")

    task: AgentTask
    triage: TriageDecision
    offense: OffenseSnapshot
    candidates: tuple[CandidateSkill, ...]
    agents: Annotated[tuple[PlanAgent, ...], Field(min_length=1)]
    plan_budget: Budget

    @model_validator(mode="after")
    def _candidates_for_plan_agents(self) -> Self:
        agents = [agent.agent_id for agent in self.agents]
        if len(set(agents)) != len(agents):
            raise ValueError("an agent is listed twice")
        if others := sorted({c.agent_role for c in self.candidates} - set(agents)):
            raise ValueError(f"candidates for agents that are not plan agents: {others}")
        return self


# What the model returns: a CasePlan without task_id, status and usage, which the run fills in,
# and without claims and data gaps. The Orchestrator has no evidence to cite and reads no data,
# so it can make neither; its plan is all it adds. The model sees this schema under the name
# CasePlan, as its prompt says. No docstring: Pydantic AI would add it to the output tool's
# description.
class OrchestratorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", title="CasePlan")

    steps: Annotated[list[PlanStep], Field(min_length=1, max_length=4)]
    injection_suspected: bool


SPEC: Final = AgentSpec(
    name="Orchestrator",
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    placeholders=PLACEHOLDERS,
    output_type=OrchestratorOutput,
    output_description="Return the CasePlan for this case.",
    retries=RETRIES,
)


@dataclass(frozen=True)
class OrchestratorAgent:
    manifest: AgentManifest
    prompt: PromptTemplate
    agent: Agent[RunDeps, OrchestratorOutput]

    def render_instructions(self, task: OrchestratorTask, *, nonce: str) -> str:
        """The prompt for one run; `nonce` is that run's `untrusted_*` tag suffix."""
        triage = task.triage
        window = task.task.time_window
        focus: JsonValue = {"investigation_focus": list(triage.investigation_focus)}
        gaps: JsonValue = {"data_gaps": [gap.model_dump(mode="json") for gap in triage.data_gaps]}
        return self.prompt.render(
            {
                "evaluation_window": f"{_utc(window.start)} to {_utc(window.end)}",
                "triage": "\n".join(
                    [
                        f"- verdict: {triage.verdict}",
                        f"- confidence: {triage.confidence}",
                        f"- level: {triage.ai_level}",
                        f"- needs investigation: {_yes_no(triage.needs_investigation)}",
                        f"- injection suspected: {_yes_no(triage.injection_suspected)}",
                    ]
                ),
                "triage_notes": "\n\n".join(
                    [
                        wrap_json_lines([focus], source=FOCUS_SOURCE, nonce=nonce),
                        wrap_json_lines([gaps], source=DATA_GAP_SOURCE, nonce=nonce),
                    ]
                ),
                "offense": wrap_json_lines(
                    [task.offense.model_dump(mode="json", exclude=set(OFFENSE_FREE_TEXT))],
                    source=OFFENSE_SOURCE,
                    nonce=nonce,
                ),
                "agents": "\n".join(
                    f"- {agent.agent_id}: {_budget(agent.budgets)}" for agent in task.agents
                ),
                "plan_budget": _budget(task.plan_budget),
                "candidates": render_candidates(task.candidates),
            }
        )

    async def run(
        self,
        task: OrchestratorTask,
        *,
        run_id: str,
        nonce: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> AgentRun[CasePlan]:
        """Plan the case as the agent run `run_id` (`agent_runs.run_id`).

        `nonce` must be fresh for every run (policy.new_nonce()). The run has no tool calls.
        Raises ValueError when the task is for another agent.
        """
        if task.task.agent_id != self.manifest.id:
            raise ValueError(f"task is for agent {task.task.agent_id!r}, not {self.manifest.id!r}")
        deps = RunDeps(
            run_id=run_id,
            case_id=task.task.case_id,
            hunt_id=task.task.hunt_id,
            time_window=task.task.time_window,
            nonce=nonce,
        )

        def finalize(output: OrchestratorOutput, usage: Usage) -> CasePlan:
            return CasePlan(
                task_id=task.task.task_id,
                status=RunStatus.COMPLETED,
                claims=[],
                data_gaps=[],
                injection_suspected=output.injection_suspected,
                usage=usage,
                steps=output.steps,
            )

        return await run_agent(
            self.agent,
            user_prompt=neutralize_tags(task.task.objective),
            instructions=self.render_instructions(task, nonce=nonce),
            deps=deps,
            limits=usage_limits(self.manifest, task.task.budget),
            prompt=self.prompt,
            finalize=finalize,
            clock=clock,
        )


def render_candidates(candidates: Sequence[CandidateSkill]) -> str:
    """The candidate skills as prompt text: approved content, not wrapped (architecture §7).

    Per skill: its ID and version, the agent it is for, its budget, its summary and its required
    evidence. The summary is not wrapped: it is approved content, like the rest. The loader
    scanned this text when it loaded the skill.
    """
    if not candidates:
        return NO_CANDIDATES
    lines: list[str] = []
    for candidate in candidates:
        ref = candidate.ref
        lines.append(
            f"- skill_id {ref.skill_id}, skill_version {ref.version}, for "
            f"{candidate.agent_role}; budget {_budget(candidate.budgets)}."
        )
        lines.append(f"  Summary: {candidate.summary}")
        lines.append("  Required evidence:")
        lines.extend(
            f"    - {evidence.id}: {evidence.description}"
            for evidence in candidate.required_evidence
        )
    return "\n".join(lines)


def build_orchestrator_agent(
    *,
    manifest: AgentManifest,
    prompt: PromptTemplate,
    model: Model,
    capabilities: Sequence[AbstractCapability[RunDeps]] = (),
) -> OrchestratorAgent:
    """Build the agent once, outside any workflow (TemporalDurability requires it).

    The agent has no tools. `capabilities` are attached when the agent is built, the only time
    Pydantic AI binds them; a workflow passes TemporalDurability here. Raises ValueError when
    the manifest does not describe an Orchestrator, names a toolset profile, or its prompt or
    shared rules are not the ones given, or the prompt does not take this agent's inputs.
    """
    check_agent_config(SPEC, manifest, prompt)
    agent = create_agent(
        SPEC, manifest=manifest, model=model, toolsets=[], aql=None, capabilities=capabilities
    )
    return OrchestratorAgent(manifest=manifest, prompt=prompt, agent=agent)


def _budget(budget: Budget | Budgets) -> str:
    seconds = budget.seconds if isinstance(budget, Budget) else budget.wall_clock_seconds
    return f"tokens {budget.tokens}, tool_calls {budget.tool_calls}, seconds {seconds}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
