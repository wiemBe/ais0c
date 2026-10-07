"""The Reporting agent under test: its scenario format and its adapter (T-053 criterion 3).

A Reporting scenario holds the agent's input as the worker builds it (decision T-45): the
workflow's decision (`CaseDecision`), the claims Verification did not dispute, their evidence
and the urgent event candidates' as `EvidenceRef`s, the candidates from Investigation, the data
gaps, the offense and the enrichment. The agent has no tools: every piece of evidence arrives
in the task under its `ev_c<n>` alias (decision T-38), so a scenario carries no tool results.

ReportingAdapter builds the agent from config/agents/reporting.yaml, its prompt files and the
registry entry's model settings, as the case worker does; its task follows the worker's
(`reporting_task`): the objective is AgentWorkflow's sentence and the window is
`evaluation_window(offense, evaluated_at)`. The run's evaluation re-checks the report's
deterministic rules (decision T-50, T-54) without relying on the agent's own validators: the
urgent events are candidates chosen by number with consecutive ranks, the summary stays within
400 characters and names no evidence alias or domain, the recommendation action types are from
the fixed list, and the report carries the input's decision unchanged.
"""

import re
from datetime import timedelta
from typing import ClassVar, Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator
from pydantic_ai.models import Model

from ais0c_activities.triage import evaluation_window
from ais0c_agents import (
    CaseDecision,
    ReportingAgent,
    ReportingTask,
    build_reporting_agent,
)
from ais0c_agents.builder import OUTPUT_TOOL
from ais0c_agents.reporting import EVIDENCE_ALIAS, IDENTIFIER_FIELDS
from ais0c_contracts import (
    ActionType,
    AgentResult,
    AgentTask,
    Budget,
    CaseReport,
    Claim,
    DataGap,
    EnrichmentContext,
    EvidenceRef,
    OffenseSnapshot,
    UrgentEvent,
)
from ais0c_harness.eval.adapter import AgentAdapter, Attempt, RecordingModel, timed_run
from ais0c_harness.eval.evaluate import Check, Evaluation, evaluate_common
from ais0c_harness.eval.scenario import Expectation, ScenarioBase
from ais0c_policy import new_nonce

EVALUATION_NO: Final = 1
EVALUATION_DELAY: Final = timedelta(minutes=5)
"""`evaluated_at` when the scenario sets none: this long after the offense's last update."""
SUMMARY_MAX_LENGTH: Final = 400
"""`NoteContent.summary_tr`'s limit (contracts.md); the deterministic audit re-checks it."""

SUMMARY_WITHIN_LIMIT = "summary_within_limit"
SUMMARY_NO_EVIDENCE_ALIAS = "summary_no_evidence_alias"
SUMMARY_NO_DOMAIN = "summary_no_domain"
URGENT_EVENTS_FROM_CANDIDATES = "urgent_events_from_candidates"
RANKS_CONSECUTIVE = "ranks_consecutive"
ACTION_TYPES_VALID = "action_types_valid"
REPORT_MATCHES_DECISION = "report_matches_decision"

# A domain-shaped token: letters on both sides of every dot, so an IP never matches. The
# operator reads the summary as plain Turkish; a host or domain in it is an identifier that
# slipped in from a log (T-54).
DOMAIN_LIKE: Final = r"\b[a-zA-Z][a-zA-Z0-9-]*(?:\.[a-zA-Z][a-zA-Z0-9-]*)+\b"


def reporting_objective(offense: OffenseSnapshot) -> str:
    """The objective the case workflow writes for the Reporting task."""
    return f"Write the report of QRadar offense {offense.offense_id} (evaluation {EVALUATION_NO})."


class ReportingInput(BaseModel):
    """The ReportingTask's own fields, as the worker's activity builds them (T-45)."""

    model_config = ConfigDict(extra="forbid")

    decision: CaseDecision
    claims: list[Claim]
    evidence: list[EvidenceRef]
    urgent_event_candidates: list[UrgentEvent] = []
    data_gaps: list[DataGap] = []
    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    evaluated_at: AwareDatetime | None = None
    """When the evaluation runs; default the offense's last update plus EVALUATION_DELAY."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.evaluated_at is not None and self.evaluated_at < self.offense.last_updated_time:
            raise ValueError("evaluated_at is before the offense's last update")
        return self

    @property
    def evaluated_at_or_default(self) -> AwareDatetime:
        return self.evaluated_at or (self.offense.last_updated_time + EVALUATION_DELAY)


class ReportingExpectation(Expectation):
    """What a Reporting scenario expects; the report's rules are fixed, so nothing extra."""


class ReportingScenario(ScenarioBase):
    """A Reporting scenario."""

    input: ReportingInput
    expect: ReportingExpectation

    @property
    def evaluated_at(self) -> AwareDatetime:
        return self.input.evaluated_at_or_default

    def expectation(self) -> ReportingExpectation:
        return self.expect

    def scripted_tools(self) -> frozenset[str]:
        return frozenset()


def cited_evidence(result: CaseReport) -> list[str]:
    """Every evidence ID a CaseReport holds."""
    found = [evidence_id for claim in result.claims for evidence_id in claim.evidence_ids]
    found.extend(event.evidence_id for event in result.urgent_events)
    found.extend(
        evidence_id
        for recommendation in result.recommendations
        for evidence_id in recommendation.evidence_ids
    )
    return found


def summary_problems(summary: str) -> list[Check]:
    """The summary's own deterministic rules (T-54), as checks."""
    return [
        Check(
            name=SUMMARY_WITHIN_LIMIT,
            passed=len(summary) <= SUMMARY_MAX_LENGTH,
            detail=f"{len(summary)} characters, at most {SUMMARY_MAX_LENGTH}",
        ),
        Check(
            name=SUMMARY_NO_EVIDENCE_ALIAS,
            passed=not EVIDENCE_ALIAS.search(summary),
            detail=f"names {', '.join(sorted(set(EVIDENCE_ALIAS.findall(summary))))}"
            if EVIDENCE_ALIAS.search(summary)
            else "no evidence alias",
        ),
        Check(
            name=SUMMARY_NO_DOMAIN,
            passed=not _domains(summary),
            detail=f"names {', '.join(_domains(summary))}" if _domains(summary) else "no domain",
        ),
    ]


def reporting_checks(scenario: ReportingScenario, result: CaseReport) -> list[Check]:
    """The report's deterministic rules (decision T-50, T-54), re-checked on the result."""
    checks = [*summary_problems(result.summary_tr)]
    problems: list[str] = []
    chosen: set[int] = set()
    candidates = scenario.input.urgent_event_candidates
    for event in result.urgent_events:
        numbers = [
            number
            for number, candidate in enumerate(candidates, start=1)
            if _same_event(candidate, event) and number not in chosen
        ]
        if numbers:
            chosen.add(numbers[0])
        else:
            problems.append(f"rank {event.rank} ({event.event_name}) is not an unchosen candidate")
    checks.append(
        Check(
            name=URGENT_EVENTS_FROM_CANDIDATES,
            passed=not problems,
            detail="; ".join(problems)
            if problems
            else f"{len(result.urgent_events)} of {len(candidates)} candidates, by number",
        )
    )
    ranks = [event.rank for event in result.urgent_events]
    consecutive = ranks == list(range(1, len(ranks) + 1))
    checks.append(
        Check(
            name=RANKS_CONSECUTIVE,
            passed=consecutive,
            detail=f"ranks {ranks}",
        )
    )
    invalid = sorted(
        recommendation.action_type.value
        for recommendation in result.recommendations
        if not isinstance(recommendation.action_type, ActionType)
    )
    checks.append(
        Check(
            name=ACTION_TYPES_VALID,
            passed=not invalid,
            detail=f"not action types: {invalid}" if invalid else "every action type is valid",
        )
    )
    decision = scenario.input.decision
    matches = (
        result.verdict == decision.verdict
        and result.confidence == decision.confidence
        and result.notify_level == decision.notify_level
    )
    checks.append(
        Check(
            name=REPORT_MATCHES_DECISION,
            passed=matches,
            detail=f"{result.verdict.value}/{result.confidence.value}/{result.notify_level.value}, "
            f"expected {decision.verdict.value}/{decision.confidence.value}/{decision.notify_level.value}",
        )
    )
    return checks


def _same_event(candidate: UrgentEvent, event: UrgentEvent) -> bool:
    """Whether `event` is `candidate`: its identifiers, copied field by field (decision T-50)."""
    return all(getattr(event, field) == getattr(candidate, field) for field in IDENTIFIER_FIELDS)


def _domains(text: str) -> list[str]:
    """The domain-shaped tokens of `text`, in order."""
    return list(dict.fromkeys(re.findall(DOMAIN_LIKE, text)))


class ReportingAdapter(AgentAdapter):
    agent_id: ClassVar[str] = "reporting"
    suite_agent: ClassVar[str] = "reporting"
    manifest_path: ClassVar[str] = "config/agents/reporting.yaml"
    scenario_type: ClassVar[type[ScenarioBase]] = ReportingScenario

    def build(self, model: Model) -> ReportingAgent:
        """The agent as the worker builds it, without TemporalDurability."""
        config = self.config
        return build_reporting_agent(manifest=config.manifest, prompt=config.prompt, model=model)

    def task(
        self, scenario: ReportingScenario, agent: ReportingAgent, *, run_id: str
    ) -> ReportingTask:
        """The run's input; the AgentTask is the case workflow's (reporting_task)."""
        played = scenario.input
        budgets = agent.manifest.budgets
        return ReportingTask(
            task=AgentTask(
                task_id=run_id,
                parent_run_id=f"harness-{scenario.id}",
                case_id=f"case-{played.offense.offense_id}",
                agent_id=agent.manifest.id,
                agent_version=agent.manifest.version,
                objective=reporting_objective(played.offense),
                context_refs=[ref.evidence_id for ref in played.evidence],
                time_window=evaluation_window(played.offense, scenario.evaluated_at),
                budget=Budget(
                    tokens=budgets.tokens,
                    tool_calls=budgets.tool_calls,
                    seconds=budgets.wall_clock_seconds,
                ),
            ),
            decision=played.decision,
            claims=played.claims,
            evidence=played.evidence,
            urgent_event_candidates=played.urgent_event_candidates,
            data_gaps=played.data_gaps,
            offense=played.offense,
            enrichment=played.enrichment,
        )

    async def attempt(
        self, scenario: ScenarioBase, *, run_id: str, model: Model, time_limit: float
    ) -> Attempt:
        played = _reporting(scenario)
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
        if result is not None and not isinstance(result, CaseReport):
            raise TypeError(f"a Reporting run returned {type(result).__name__}")
        played = _reporting(scenario)
        common = evaluate_common(
            expectation=played.expectation(),
            result=result,
            cited=[] if result is None else cited_evidence(result),
            messages=attempt.messages,
            exchanges=attempt.exchanges,
            profile=self.config.profile,
            output_tool=OUTPUT_TOOL,
            tokens=attempt.tokens,
            seconds=attempt.seconds,
            # The agent has no tool results: the task's evidence is what it can cite.
            available_evidence=[ref.evidence_id for ref in played.input.evidence],
        )
        if result is None:
            return common
        return Evaluation(
            checks=[*reporting_checks(played, result), *common.checks], metrics=common.metrics
        )

    def describe(self, result: AgentResult) -> dict[str, str]:
        if not isinstance(result, CaseReport):
            return {}
        return {
            "verdict": result.verdict.value,
            "notify_level": result.notify_level.value,
            "urgent_events": str(len(result.urgent_events)),
        }


def _reporting(scenario: ScenarioBase) -> ReportingScenario:
    if not isinstance(scenario, ReportingScenario):
        raise TypeError(f"{scenario.id} is not a Reporting scenario")
    return scenario
