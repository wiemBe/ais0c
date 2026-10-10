"""The Verification agent under test: its scenario format and its adapter (T-052 criterion 8).

A Verification scenario names a recording (replay_scenario.py), holds the decision under review
(its enums and claims, each claim with the evidence it cites, and whether the workflow marks the
claims critical) and says which claims the verifier must contest. The adapter builds the agent as
the worker does (`build_verification_agent`, the manifest's profile `qradar-verify-read` with its
AQL window and output filter, the real prompt) and its task with the worker's own function
(`ais0c_activities.agent_runtimes.verification_task`), so the task's window is the union of the
claims' evidence windows (decision T-56).

The checks, besides the shared ones (evaluate.py):

- `agrees`: the verdict of the verifier on the whole decision;
- `disputed_claims`: the claims (by position in the scenario) the verifier contests are exactly
  `expect.disputed_claims`. A disagreement names a claim by its exact text (the agent's output
  validator enforces that), so each maps to one claim. A claim a scenario expects to be accepted
  is any claim not in the list.

The evidence the claims cite is handed over with the task; the verifier may name it in
`checked_evidence_ids` without a tool result of the run.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Final

from pydantic import Field
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from ais0c_activities.agent_runtimes import verification_task
from ais0c_activities.chain import manifest_budget
from ais0c_activities.names import case_workflow_id
from ais0c_activities.triage import evaluation_window
from ais0c_agents import (
    ReviewedDecision,
    VerificationAgent,
    VerificationTask,
    build_verification_agent,
)
from ais0c_agents.builder import OUTPUT_TOOL
from ais0c_agents.verification import MAX_QUERY_WINDOW
from ais0c_contracts import (
    AgentResult,
    AgentTask,
    CaseVerdict,
    Claim,
    Confidence,
    EnrichmentContext,
    EvidenceRef,
    Level,
    OffenseSnapshot,
    VerificationResult,
)
from ais0c_harness.eval.adapter import AgentAdapter, Attempt, RecordingModel, timed_run
from ais0c_harness.eval.config import AgentConfig, gateway_tool_profile, tool_profile
from ais0c_harness.eval.evaluate import Check, Evaluation, evaluate_common
from ais0c_harness.eval.fixture_gateway import GatewayExchange
from ais0c_harness.eval.replay_scenario import ReplayInput, ReplayScenario
from ais0c_harness.eval.scenario import Expectation, ScenarioBase
from ais0c_harness.replay.gateway import ReplayGateway
from ais0c_policy import new_nonce

AGREES: Final = "agrees"
DISPUTED_CLAIMS: Final = "disputed_claims"
VERDICT_IN: Final = "verdict_in"


class VerificationInput(ReplayInput):
    reviewed: ReviewedDecision
    claims: Annotated[list[Claim], Field(min_length=1)]
    evidence: list[EvidenceRef]
    critical: bool = True
    """Whether the workflow marks the claims critical (a tp or suspicious decision of high
    notification level, `claims_are_critical`)."""


class VerificationExpectation(Expectation):
    agrees: bool
    disputed_claims: list[Annotated[int, Field(ge=0)]]
    """Positions in `input.claims` of the claims the verifier must contest, none for a decision
    it must accept."""
    verdict_in: frozenset[CaseVerdict] = frozenset()
    """Verdicts the verifier may return; empty: the verdict is not scored."""


class VerificationScenario(ReplayScenario[VerificationInput]):
    expect: VerificationExpectation

    def expectation(self) -> VerificationExpectation:
        return self.expect

    def check_files(self, root: Path) -> None:
        super().check_files(root)
        known = {ref.evidence_id for ref in self.input.evidence}
        missing = {
            evidence_id
            for claim in self.input.claims
            for evidence_id in claim.evidence_ids
            if evidence_id not in known
        }
        if missing:
            raise ValueError(f"claims cite evidence the scenario does not carry: {sorted(missing)}")
        if any(position >= len(self.input.claims) for position in self.expect.disputed_claims):
            raise ValueError("disputed_claims names a claim the scenario does not have")
        if self.expect.agrees == bool(self.expect.disputed_claims):
            raise ValueError("agrees is true exactly when no claim is disputed")
        if len({claim.text for claim in self.input.claims}) != len(self.input.claims):
            raise ValueError("two claims have the same text; a disagreement names a claim by text")


@dataclass(frozen=True)
class _Inputs:
    """The workflow's VerificationInput, as `verification_task` reads it."""

    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    claims: Sequence[Claim]
    critical: bool


class VerificationAdapter(AgentAdapter):
    agent_id: ClassVar[str] = "verification"
    suite_agent: ClassVar[str] = "verification"
    manifest_path: ClassVar[str] = "config/agents/verification.yaml"
    scenario_type: ClassVar[type[ScenarioBase]] = VerificationScenario

    def build(self, gateway: ReplayGateway, model: Model) -> VerificationAgent:
        """The agent as the worker builds it, without TemporalDurability. The AQL window of its
        queries is the profile's, as the gateway's registry has it."""
        config = self.config
        aql = gateway_tool_profile(config).aql
        return build_verification_agent(
            manifest=config.manifest,
            prompt=config.prompt,
            profiles={tool_profile(config).name: tool_profile(config)},
            gateway=gateway,
            model=model,
            max_query_window=MAX_QUERY_WINDOW if aql is None else aql.max_window,
        )

    def task(self, scenario: VerificationScenario, *, run_id: str) -> VerificationTask:
        """The run's input; built by the worker's `verification_task`."""
        recording = scenario.recorded(self.config.root)
        offense = recording.offense
        reviewed = scenario.input.reviewed
        agent_task = AgentTask(
            task_id=run_id,
            parent_run_id=f"harness-{scenario.id}",
            case_id=case_workflow_id(offense.offense_id),
            agent_id=self.config.manifest.id,
            agent_version=self.config.manifest.version,
            objective=scenario.input.objective,
            context_refs=[],
            time_window=evaluation_window(offense, scenario.evaluated_moment(recording)),
            budget=manifest_budget(self.config.manifest),
        )
        return verification_task(
            agent_task,
            _Inputs(
                offense=offense,
                enrichment=recording.enrichment,
                verdict=reviewed.verdict,
                confidence=reviewed.confidence,
                ai_level=reviewed.ai_level,
                claims=scenario.input.claims,
                critical=scenario.input.critical,
            ),
            scenario.input.evidence,
        )

    async def attempt(
        self, scenario: ScenarioBase, *, run_id: str, model: Model, time_limit: float
    ) -> Attempt:
        verification = _verification(scenario)
        recording = verification.recorded(self.config.root)
        gateway = ReplayGateway(
            gateway_tool_profile(self.config),
            recording,
            now=verification.evaluated_moment(recording),
            run_id=run_id,
        )
        recorder = RecordingModel(model)
        agent = self.build(gateway, recorder)
        task = self.task(verification, run_id=run_id)
        return await timed_run(
            lambda: agent.run(task, run_id=run_id, nonce=new_nonce()),
            run_id=run_id,
            recorder=recorder,
            exchanges=gateway.exchanges,
            time_limit=time_limit,
        )

    def evaluate(self, scenario: ScenarioBase, attempt: Attempt) -> Evaluation:
        result = attempt.result
        if result is not None and not isinstance(result, VerificationResult):
            raise TypeError(f"a Verification run returned {type(result).__name__}")
        return evaluate_verification(
            _verification(scenario),
            result=result,
            messages=attempt.messages,
            exchanges=attempt.exchanges,
            config=self.config,
            tokens=attempt.tokens,
            seconds=attempt.seconds,
        )

    def describe(self, result: AgentResult) -> dict[str, str]:
        if not isinstance(result, VerificationResult):
            return {}
        return {
            "agrees": str(result.agrees).lower(),
            "verdict": result.verdict.value,
            "confidence": result.confidence.value,
        }


def cited_evidence(result: VerificationResult) -> list[str]:
    """Every evidence ID a Verification result holds: what it checked and its own claims'."""
    return [
        *result.checked_evidence_ids,
        *(evidence_id for claim in result.claims for evidence_id in claim.evidence_ids),
    ]


def evaluate_verification(
    scenario: VerificationScenario,
    *,
    result: VerificationResult | None,
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
        handed_evidence={ref.evidence_id for ref in scenario.input.evidence},
    )
    if result is None:
        return common
    return Evaluation(
        checks=[*verification_checks(scenario, result), *common.checks], metrics=common.metrics
    )


def verification_checks(scenario: VerificationScenario, result: VerificationResult) -> list[Check]:
    expect = scenario.expect
    texts = [claim.text for claim in scenario.input.claims]
    contested = sorted(
        {texts.index(item.claim_text) for item in result.disagreements if item.claim_text in texts}
    )
    checks = [
        Check(
            name=AGREES,
            passed=result.agrees is expect.agrees,
            detail=f"{str(result.agrees).lower()}, expected {str(expect.agrees).lower()}",
        ),
        Check(
            name=DISPUTED_CLAIMS,
            passed=contested == sorted(set(expect.disputed_claims)),
            detail=f"claims {contested or 'none'} contested, expected "
            f"{sorted(set(expect.disputed_claims)) or 'none'}",
        ),
    ]
    if expect.verdict_in:
        verdicts = ", ".join(sorted(verdict.value for verdict in expect.verdict_in))
        checks.append(
            Check(
                name=VERDICT_IN,
                passed=result.verdict in expect.verdict_in,
                detail=f"{result.verdict.value}, expected one of {verdicts}",
            )
        )
    return checks


def _verification(scenario: ScenarioBase) -> VerificationScenario:
    if not isinstance(scenario, VerificationScenario):
        raise TypeError(f"{scenario.id} is not a Verification scenario")
    return scenario
