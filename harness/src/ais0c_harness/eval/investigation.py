"""The Investigation agent under test: its scenario format and its adapter (T-052 criterion 8).

An Investigation scenario names a recording (replay_scenario.py), says what Triage handed over
(its decision, claims and the evidence they cite) and what the run must find. The adapter builds
the agent as the worker does (`build_investigation_agent`, the manifest's gateway profile
`qradar-investigate-read`, the real prompt) and its task with the worker's own function
(`ais0c_activities.agent_runtimes.investigation_task`): the plan step's objective, Triage's claims
as `agent.*` sources, the evaluation window and the manifest's budget. The agent's tools are a
`ReplayGateway` on the recording.

The checks, besides the shared ones (evaluate.py):

- `verdict_in`: the verdict the agent reaches;
- `events_found`: every event in `find_events` is found: an urgent event candidate names its
  source (or destination) and user, or a claim or timeline entry cites evidence whose rows hold
  them. An expected event is the pair the recording's table can show.
- `data_gap_reason_in`: a missing required telemetry scenario ends with one of its allowed,
  non-budget data-gap reasons;
- `injection_suspected`: when set, the result's flag must match it.

Evidence the task handed over (`context_evidence`) may be cited too: it is not a tool result of
the run, but the gateway recorded it.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from ais0c_activities.agent_runtimes import investigation_task
from ais0c_activities.chain import manifest_budget
from ais0c_activities.names import case_workflow_id
from ais0c_activities.triage import evaluation_window
from ais0c_agents import (
    InvestigationAgent,
    InvestigationTask,
    InvestigationTriage,
    build_investigation_agent,
)
from ais0c_agents.builder import OUTPUT_TOOL
from ais0c_contracts import (
    AgentResult,
    AgentTask,
    Budget,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    DataGapReason,
    EnrichmentContext,
    EvidenceRef,
    InvestigationResult,
    Level,
    OffenseSnapshot,
    SkillRef,
    UrgentEvent,
)
from ais0c_harness.eval.adapter import AgentAdapter, Attempt, RecordingModel, timed_run
from ais0c_harness.eval.config import AgentConfig, gateway_tool_profile, tool_profile
from ais0c_harness.eval.evaluate import Check, Evaluation, evaluate_common
from ais0c_harness.eval.fixture_gateway import GatewayExchange
from ais0c_harness.eval.replay_scenario import ReplayInput, ReplayScenario
from ais0c_harness.eval.scenario import Expectation, ScenarioBase
from ais0c_harness.replay.gateway import ReplayGateway
from ais0c_knowledge.skills import SkillRegistry, load_skills
from ais0c_policy import new_nonce

EVALUATION_NO: Final = 1
VERDICT_IN: Final = "verdict_in"
EVENTS_FOUND: Final = "events_found"
DATA_GAP_REASON_IN: Final = "data_gap_reason_in"
INJECTION_SUSPECTED: Final = "injection_suspected"
SKILLS_DIR: Final = "skills"


class SkillSelection(BaseModel):
    """The skill the plan step carries, from skills/ (loaded in dev mode: drafts allowed)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$")]
    version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]


class InvestigationInput(ReplayInput):
    triage: InvestigationTriage
    context_evidence: list[EvidenceRef] = Field(default_factory=list[EvidenceRef])
    skill: SkillSelection | None = None


class ExpectedEvent(BaseModel):
    """An event the run must find: the address and the user it shows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: Annotated[str, StringConstraints(min_length=1)]
    """A source or destination address."""
    username: Annotated[str, StringConstraints(min_length=1)] | None = None


class InvestigationExpectation(Expectation):
    verdict_in: Annotated[frozenset[CaseVerdict], Field(min_length=1)]
    find_events: list[ExpectedEvent] = Field(default_factory=list[ExpectedEvent])
    data_gap_reason_in: frozenset[DataGapReason] = frozenset()
    injection_suspected: bool | None = None


class InvestigationScenario(ReplayScenario[InvestigationInput]):
    expect: InvestigationExpectation

    def expectation(self) -> InvestigationExpectation:
        return self.expect

    def check_files(self, root: Path) -> None:
        super().check_files(root)
        recording = self.recorded(root)
        known = {ref.evidence_id for ref in self.input.context_evidence}
        missing = {
            evidence_id
            for claim in self.input.triage.claims
            for evidence_id in claim.evidence_ids
            if evidence_id not in known
        }
        if missing:
            raise ValueError(f"claims cite evidence the scenario does not carry: {sorted(missing)}")
        if (
            self.input.skill is not None
            and skill_registry(root.resolve()).get(self.input.skill.id, self.input.skill.version)
            is None
        ):
            raise ValueError(f"no skill {self.input.skill.id} {self.input.skill.version}")
        table = [
            (event.sourceip, event.destinationip, event.username) for event in recording.events
        ]
        for expected in self.expect.find_events:
            if not any(
                expected.address in (source, destination)
                and (expected.username is None or expected.username == username)
                for source, destination, username in table
            ):
                raise ValueError(f"the recording has no event for {expected.model_dump()}")


@cache
def skill_registry(root: Path) -> SkillRegistry:
    return load_skills(root / SKILLS_DIR, mode="dev")


@dataclass(frozen=True)
class _Inputs:
    """The workflow's InvestigationInput, as `investigation_task` reads it."""

    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    investigation_focus: Sequence[str]
    claims: Sequence[Claim]
    data_gaps: Sequence[DataGap]


class InvestigationAdapter(AgentAdapter):
    agent_id: ClassVar[str] = "investigation"
    suite_agent: ClassVar[str] = "investigation"
    manifest_path: ClassVar[str] = "config/agents/investigation.yaml"
    scenario_type: ClassVar[type[ScenarioBase]] = InvestigationScenario

    def build(self, gateway: ReplayGateway, model: Model) -> InvestigationAgent:
        """The agent as the worker builds it, without TemporalDurability."""
        config = self.config
        return build_investigation_agent(
            manifest=config.manifest,
            prompt=config.prompt,
            profiles={tool_profile(config).name: tool_profile(config)},
            gateway=gateway,
            model=model,
            aql_rules_path=config.root / "config" / "policies" / "qradar.yaml",
        )

    def task(self, scenario: InvestigationScenario, *, run_id: str) -> InvestigationTask:
        """The run's input; built by the worker's `investigation_task`."""
        recording = scenario.recorded(self.config.root)
        offense = recording.offense
        triage = scenario.input.triage
        agent_task = AgentTask(
            task_id=run_id,
            parent_run_id=f"harness-{scenario.id}",
            case_id=case_workflow_id(offense.offense_id),
            agent_id=self.config.manifest.id,
            agent_version=self.config.manifest.version,
            objective=scenario.input.objective,
            context_refs=[],
            time_window=evaluation_window(offense, scenario.evaluated_moment(recording)),
            budget=self.budget_for(scenario),
        )
        registry = skill_registry(self.config.root.resolve())
        skill = self._skill(scenario, registry)
        return investigation_task(
            agent_task,
            _Inputs(
                offense=offense,
                enrichment=recording.enrichment,
                verdict=triage.verdict,
                confidence=triage.confidence,
                ai_level=triage.ai_level,
                investigation_focus=triage.investigation_focus,
                claims=triage.claims,
                data_gaps=triage.data_gaps,
            ),
            scenario.input.context_evidence,
            registry,
            skill,
        )

    @staticmethod
    def _skill(scenario: InvestigationScenario, registry: SkillRegistry) -> SkillRef | None:
        chosen = scenario.input.skill
        if chosen is None:
            return None
        found = registry.get(chosen.id, chosen.version)
        return None if found is None else found.ref

    def skill_for(self, scenario: ScenarioBase) -> SkillRef | None:
        investigation = _investigation(scenario)
        return self._skill(investigation, skill_registry(self.config.root.resolve()))

    def budget_for(self, scenario: ScenarioBase) -> Budget:
        manifest = manifest_budget(self.config.manifest)
        investigation = _investigation(scenario)
        chosen = investigation.input.skill
        if chosen is None:
            return manifest
        found = skill_registry(self.config.root.resolve()).get(chosen.id, chosen.version)
        if found is None:
            return manifest
        skill = found.manifest.budgets
        return Budget(
            tokens=min(manifest.tokens, skill.tokens),
            tool_calls=min(manifest.tool_calls, skill.tool_calls),
            seconds=min(manifest.seconds, skill.wall_clock_seconds),
        )

    async def attempt(
        self, scenario: ScenarioBase, *, run_id: str, model: Model, time_limit: float
    ) -> Attempt:
        investigation = _investigation(scenario)
        recording = investigation.recorded(self.config.root)
        gateway = ReplayGateway(
            gateway_tool_profile(self.config),
            recording,
            now=investigation.evaluated_moment(recording),
            run_id=run_id,
        )
        recorder = RecordingModel(model)
        agent = self.build(gateway, recorder)
        task = self.task(investigation, run_id=run_id)
        return await timed_run(
            lambda: agent.run(task, run_id=run_id, nonce=new_nonce()),
            run_id=run_id,
            recorder=recorder,
            exchanges=gateway.exchanges,
            time_limit=time_limit,
        )

    def evaluate(self, scenario: ScenarioBase, attempt: Attempt) -> Evaluation:
        result = attempt.result
        if result is not None and not isinstance(result, InvestigationResult):
            raise TypeError(f"an Investigation run returned {type(result).__name__}")
        return evaluate_investigation(
            _investigation(scenario),
            result=result,
            messages=attempt.messages,
            exchanges=attempt.exchanges,
            config=self.config,
            tokens=attempt.tokens,
            seconds=attempt.seconds,
        )

    def describe(self, result: AgentResult) -> dict[str, str]:
        if not isinstance(result, InvestigationResult):
            return {}
        return {
            "verdict": result.verdict.value,
            "confidence": result.confidence.value,
            "ai_level": result.ai_level.value,
        }


def cited_evidence(result: InvestigationResult) -> list[str]:
    """Every evidence ID an Investigation result holds: claims, timeline and urgent events."""
    return [
        *(evidence_id for claim in result.claims for evidence_id in claim.evidence_ids),
        *(evidence_id for entry in result.timeline for evidence_id in entry.evidence_ids),
        *(event.evidence_id for event in result.urgent_event_candidates),
    ]


def evaluate_investigation(
    scenario: InvestigationScenario,
    *,
    result: InvestigationResult | None,
    messages: Sequence[ModelMessage],
    exchanges: Sequence[GatewayExchange],
    config: AgentConfig,
    tokens: int = 0,
    seconds: float = 0.0,
) -> Evaluation:
    common = evaluate_common(
        expectation=scenario.expect,
        result=result,
        cited=[] if result is None else cited_evidence(result),
        messages=messages,
        exchanges=exchanges,
        profile=config.profile,
        output_tool=OUTPUT_TOOL,
        tokens=tokens,
        seconds=seconds,
        known_tools=config.gateway_tools,
        handed_evidence={ref.evidence_id for ref in scenario.input.context_evidence},
    )
    if result is None:
        return common
    return Evaluation(
        checks=[*investigation_checks(scenario, result, exchanges), *common.checks],
        metrics=common.metrics,
    )


def investigation_checks(
    scenario: InvestigationScenario,
    result: InvestigationResult,
    exchanges: Sequence[GatewayExchange],
) -> list[Check]:
    expect = scenario.expect
    verdicts = ", ".join(sorted(verdict.value for verdict in expect.verdict_in))
    checks = [
        Check(
            name=VERDICT_IN,
            passed=result.verdict in expect.verdict_in,
            detail=f"{result.verdict.value}, expected one of {verdicts}",
        )
    ]
    if expect.find_events:
        cited = set(cited_claim_evidence(result))
        rows = [
            row
            for exchange in exchanges
            if exchange.executed and exchange.result.evidence_id in cited
            for row in exchange.result.data
        ]
        missing = [
            f"{event.address}/{event.username or '*'}"
            for event in expect.find_events
            if not event_found(event, result.urgent_event_candidates, rows)
        ]
        checks.append(
            Check(
                name=EVENTS_FOUND,
                passed=not missing,
                detail=f"not found: {', '.join(missing)}"
                if missing
                else f"all {len(expect.find_events)} events found",
            )
        )
    if expect.data_gap_reason_in:
        reasons = {gap.reason for gap in result.data_gaps}
        allowed = ", ".join(sorted(reason.value for reason in expect.data_gap_reason_in))
        checks.append(
            Check(
                name=DATA_GAP_REASON_IN,
                passed=bool(reasons & expect.data_gap_reason_in),
                detail=f"found {', '.join(sorted(reason.value for reason in reasons)) or 'none'}; "
                f"expected one of {allowed}",
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


def cited_claim_evidence(result: InvestigationResult) -> list[str]:
    """The evidence a claim or a timeline entry cites."""
    return [
        *(evidence_id for claim in result.claims for evidence_id in claim.evidence_ids),
        *(evidence_id for entry in result.timeline for evidence_id in entry.evidence_ids),
    ]


def event_found(
    expected: ExpectedEvent,
    candidates: Sequence[UrgentEvent],
    rows: Collection[Mapping[str, JsonValue]],
) -> bool:
    """Whether an urgent event candidate or a cited row shows the expected event."""
    for candidate in candidates:
        if expected.address in (candidate.source, candidate.destination) and (
            expected.username is None or expected.username == candidate.username
        ):
            return True
    for row in rows:
        values = {str(value) for value in row.values() if value is not None}
        if expected.address in values and (
            expected.username is None or expected.username in values
        ):
            return True
    return False


def _investigation(scenario: ScenarioBase) -> InvestigationScenario:
    if not isinstance(scenario, InvestigationScenario):
        raise TypeError(f"{scenario.id} is not an Investigation scenario")
    return scenario
