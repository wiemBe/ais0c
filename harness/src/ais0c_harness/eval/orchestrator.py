"""The Orchestrator agent under test: its scenario format and its adapter (T-053 criterion 1).

An Orchestrator scenario holds the agent's input as the worker builds it (decision T-45):
Triage's structured decision without its rationale and claims (`TriageDecision`), the offense,
the candidate skills the router listed (`CandidateSkill`), the plan agents with their manifest
budgets and the plan budget. The agent has no tools, so a scenario carries no tool results and
`fixture` mode needs no gateway.

OrchestratorAdapter builds the agent from config/agents/orchestrator.yaml, its prompt files and
the registry entry's model settings, as the case worker does. Its task follows the worker's:
the objective is AgentWorkflow's sentence, the window is `evaluation_window(offense,
evaluated_at)` and the budget is the manifest's. The run's evaluation plans the case through
`validate_plan` (ais0c_workflows.plan, decision T-41) — the same check the workflow applies —
and asks whether the plan stayed the one the model proposed, whether the expected agents are in
it and whether `injection_suspected` matches.
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Annotated, ClassVar, Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic_ai.models import Model

from ais0c_activities.triage import evaluation_window
from ais0c_agents import (
    Budgets,
    CandidateSkill,
    OrchestratorAgent,
    OrchestratorTask,
    PlanAgent,
    TriageDecision,
    build_orchestrator_agent,
)
from ais0c_agents.builder import OUTPUT_TOOL
from ais0c_contracts import (
    AgentResult,
    AgentTask,
    Budget,
    CasePlan,
    OffenseSnapshot,
)
from ais0c_harness.eval.adapter import AgentAdapter, Attempt, RecordingModel, timed_run
from ais0c_harness.eval.evaluate import Check, Evaluation, evaluate_common
from ais0c_harness.eval.scenario import Expectation, ScenarioBase
from ais0c_policy import new_nonce
from ais0c_workflows.plan import PlanCandidate, PlanDecision, validate_plan

EVALUATION_NO: Final = 1
EVALUATION_DELAY: Final = timedelta(minutes=5)
"""`evaluated_at` when the scenario sets none: this long after the offense's last update."""

VERIFICATION: Final = "verification"
PLAN_VALID = "plan_valid"
PLAN_UNCHANGED = "plan_unchanged"
EXPECTED_AGENTS = "expected_agents"
SKILL_OF_AGENT = "skill_of_agent"
INJECTION_SUSPECTED = "injection_suspected"


def orchestrator_objective(offense: OffenseSnapshot) -> str:
    """The objective the case workflow writes for the Orchestrator's task."""
    return (
        f"Plan the rest of the evaluation of QRadar offense {offense.offense_id} "
        f"(evaluation {EVALUATION_NO})."
    )


class OrchestratorInput(BaseModel):
    """The OrchestratorTask's own fields, as the worker's activity builds them (T-45)."""

    model_config = ConfigDict(extra="forbid")

    triage: TriageDecision
    offense: OffenseSnapshot
    candidates: tuple[CandidateSkill, ...] = ()
    agents: Annotated[tuple[PlanAgent, ...], Field(min_length=1)]
    plan_budget: Budget
    evaluated_at: AwareDatetime | None = None
    """When the evaluation runs; default the offense's last update plus EVALUATION_DELAY."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [agent.agent_id for agent in self.agents]
        if len(set(ids)) != len(ids):
            raise ValueError("an agent is listed twice")
        if others := sorted({candidate.agent_role for candidate in self.candidates} - set(ids)):
            raise ValueError(f"candidates for agents that are not plan agents: {others}")
        if self.evaluated_at is not None and self.evaluated_at < self.offense.last_updated_time:
            raise ValueError("evaluated_at is before the offense's last update")
        return self

    @property
    def evaluated_at_or_default(self) -> AwareDatetime:
        return self.evaluated_at or (self.offense.last_updated_time + EVALUATION_DELAY)


class OrchestratorExpectation(Expectation):
    """What an Orchestrator scenario expects of a plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_agents: frozenset[str] = frozenset()
    """Agents the validated plan must contain."""
    skill_of_agent: Annotated[dict[str, str], Field(max_length=4)] = Field(default_factory=dict)
    """Agent ID -> the skill_id its step must be bound to."""
    injection_suspected: bool | None = None


class OrchestratorScenario(ScenarioBase):
    """An Orchestrator scenario."""

    input: OrchestratorInput
    expect: OrchestratorExpectation

    @property
    def evaluated_at(self) -> AwareDatetime:
        return self.input.evaluated_at_or_default

    def expectation(self) -> OrchestratorExpectation:
        return self.expect

    def scripted_tools(self) -> frozenset[str]:
        return frozenset()


def plan_candidates(scenario: OrchestratorScenario) -> tuple[PlanCandidate, ...]:
    """The scenario's candidate skills as `validate_plan` takes them."""
    return tuple(
        PlanCandidate(
            agent_id=candidate.agent_role,
            skill=candidate.ref,
            budget=_budget_of(candidate.budgets),
        )
        for candidate in scenario.input.candidates
    )


def agent_budgets(scenario: OrchestratorScenario) -> dict[str, Budget]:
    """The plan agents' manifest budgets, as the workflow hands them to `validate_plan`."""
    return {agent.agent_id: _budget_of(agent.budgets) for agent in scenario.input.agents}


def plan_the_case(scenario: OrchestratorScenario, plan: CasePlan) -> PlanDecision:
    """The workflow's own check (decision T-41) of the plan the model proposed."""
    return validate_plan(
        plan,
        agents=agent_budgets(scenario),
        candidates=plan_candidates(scenario),
        plan_budget=scenario.input.plan_budget,
        window=evaluation_window(scenario.input.offense, scenario.evaluated_at),
        needs_investigation=scenario.input.triage.needs_investigation,
    )


def plan_shape(steps: Sequence[object]) -> list[tuple[str, str, str | None, str | None]]:
    """What the plan says, without the budget and window values the rules may adjust."""
    return [(s.agent_id, s.objective, s.skill_id, s.skill_version) for s in steps]  # type: ignore[attr-defined]


def orchestrator_checks(scenario: OrchestratorScenario, result: CasePlan) -> list[Check]:
    """The Orchestrator expectations: the plan the workflow would run."""
    expect = scenario.expect
    decision = plan_the_case(scenario, result)
    accepted = decision.rejection is None and not decision.used_default
    checks = [
        Check(
            name=PLAN_VALID,
            passed=accepted,
            detail="accepted"
            if accepted
            else f"rejected, default plan instead: {decision.rejection.detail if decision.rejection else ''}",
        )
    ]
    # The workflow adds Verification as the last step (contracts: CasePlan), so it is not a change.
    proposed = plan_shape([step for step in result.steps if step.agent_id != VERIFICATION])
    ran = plan_shape([step for step in decision.steps if step.agent_id != VERIFICATION])
    checks.append(
        Check(
            name=PLAN_UNCHANGED,
            passed=ran == proposed,
            detail=f"runs {ran}" if ran != proposed else "the plan runs as proposed",
        )
    )
    agents = {step.agent_id for step in decision.steps}
    missing = sorted(expect.expected_agents - agents)
    checks.append(
        Check(
            name=EXPECTED_AGENTS,
            passed=not missing,
            detail=f"the plan runs {sorted(agents)}, missing {missing}"
            if missing
            else f"the plan runs {sorted(agents)}",
        )
    )
    if expect.skill_of_agent:
        bound = {step.agent_id: step.skill_id for step in decision.steps}
        wrong = {
            agent: (bound.get(agent), wanted)
            for agent, wanted in sorted(expect.skill_of_agent.items())
            if bound.get(agent) != wanted
        }
        checks.append(
            Check(
                name=SKILL_OF_AGENT,
                passed=not wrong,
                detail="; ".join(
                    f"{agent} is bound to {got!r}, expected {wanted!r}"
                    for agent, (got, wanted) in wrong.items()
                )
                if wrong
                else "every expected skill is bound to its agent",
            )
        )
    if expect.injection_suspected is not None:
        checks.append(
            Check(
                name=INJECTION_SUSPECTED,
                passed=result.injection_suspected is expect.injection_suspected,
                detail=f"{str(result.injection_suspected).lower()}, expected "
                f"{str(expect.injection_suspected).lower()}",
            )
        )
    return checks


class OrchestratorAdapter(AgentAdapter):
    agent_id: ClassVar[str] = "orchestrator"
    suite_agent: ClassVar[str] = "orchestrator"
    manifest_path: ClassVar[str] = "config/agents/orchestrator.yaml"
    scenario_type: ClassVar[type[ScenarioBase]] = OrchestratorScenario

    def build(self, model: Model) -> OrchestratorAgent:
        """The agent as the worker builds it, without TemporalDurability."""
        config = self.config
        return build_orchestrator_agent(manifest=config.manifest, prompt=config.prompt, model=model)

    def task(
        self, scenario: OrchestratorScenario, agent: OrchestratorAgent, *, run_id: str
    ) -> OrchestratorTask:
        """The run's input; the AgentTask is the case workflow's."""
        offense = scenario.input.offense
        budgets = agent.manifest.budgets
        return OrchestratorTask(
            task=AgentTask(
                task_id=run_id,
                parent_run_id=f"harness-{scenario.id}",
                case_id=f"case-{offense.offense_id}",
                agent_id=agent.manifest.id,
                agent_version=agent.manifest.version,
                objective=orchestrator_objective(offense),
                context_refs=[],
                time_window=evaluation_window(offense, scenario.evaluated_at),
                budget=Budget(
                    tokens=budgets.tokens,
                    tool_calls=budgets.tool_calls,
                    seconds=budgets.wall_clock_seconds,
                ),
            ),
            triage=scenario.input.triage,
            offense=offense,
            candidates=scenario.input.candidates,
            agents=scenario.input.agents,
            plan_budget=scenario.input.plan_budget,
        )

    async def attempt(
        self, scenario: ScenarioBase, *, run_id: str, model: Model, time_limit: float
    ) -> Attempt:
        played = _orchestrator(scenario)
        recorder = RecordingModel(model)
        agent = self.build(recorder)
        task = self.task(played, agent, run_id=run_id)
        return await timed_run(
            lambda: agent.run(task, run_id=run_id, nonce=new_nonce()),
            run_id=run_id,
            recorder=recorder,
            exchanges=[],
            time_limit=time_limit,
        )

    def evaluate(self, scenario: ScenarioBase, attempt: Attempt) -> Evaluation:
        result = attempt.result
        if result is not None and not isinstance(result, CasePlan):
            raise TypeError(f"an Orchestrator run returned {type(result).__name__}")
        common = evaluate_common(
            expectation=scenario.expectation(),
            result=result,
            cited=[],
            messages=attempt.messages,
            exchanges=attempt.exchanges,
            profile=self.config.profile,
            output_tool=OUTPUT_TOOL,
            tokens=attempt.tokens,
            seconds=attempt.seconds,
        )
        if result is None:
            return common
        return Evaluation(
            checks=[*orchestrator_checks(_orchestrator(scenario), result), *common.checks],
            metrics=common.metrics,
        )

    def describe(self, result: AgentResult) -> dict[str, str]:
        if not isinstance(result, CasePlan):
            return {}
        return {
            "plan_agents": "+".join(sorted(step.agent_id for step in result.steps)),
            "injection_suspected": str(result.injection_suspected).lower(),
        }


def _budget_of(budgets: Budgets) -> Budget:
    """A manifest Budgets as the workflow's Budget."""
    return Budget(
        tokens=budgets.tokens, tool_calls=budgets.tool_calls, seconds=budgets.wall_clock_seconds
    )


def _orchestrator(scenario: ScenarioBase) -> OrchestratorScenario:
    if not isinstance(scenario, OrchestratorScenario):
        raise TypeError(f"{scenario.id} is not an Orchestrator scenario")
    return scenario
