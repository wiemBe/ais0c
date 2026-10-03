"""The "Ajan girdi ve çıktıları" models of docs/impl/contracts.md."""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from ais0c_contracts.common import (
    Budget,
    Claim,
    ContractModel,
    DataGap,
    EvidenceId,
    Recommendation,
    ShortText,
    Summary,
    TimeWindow,
    UrgentEvent,
    Usage,
    UtcDatetime,
    check_case_or_hunt,
)
from ais0c_contracts.enums import CaseVerdict, Confidence, Level, RunStatus


class AgentTask(ContractModel):
    task_id: str
    parent_run_id: str
    case_id: str | None = None
    hunt_id: str | None = None
    # An agent in the registry.
    agent_id: str
    agent_version: str
    objective: ShortText
    # Evidence IDs, never conversation text.
    context_refs: list[EvidenceId]
    time_window: TimeWindow
    budget: Budget

    @model_validator(mode="after")
    def _case_or_hunt(self) -> Self:
        check_case_or_hunt(self.case_id, self.hunt_id)
        return self


class AgentResult(ContractModel):
    """Fields every agent result carries."""

    task_id: str
    status: RunStatus
    claims: list[Claim]
    data_gaps: list[DataGap]
    injection_suspected: bool
    usage: Usage


class TriageResult(AgentResult):
    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    rationale: Summary
    needs_investigation: bool
    investigation_focus: Annotated[list[ShortText], Field(max_length=5)]


class PlanStep(ContractModel):
    # Registry and workflow-type checks happen in the workflow, not here.
    agent_id: str
    # Only from the router's candidate list; the workflow checks the skill's status, version
    # and budget (v0.2, T-21). Set both or neither; an empty string counts as not set.
    skill_id: str | None = None
    skill_version: str | None = None
    objective: ShortText
    time_window: TimeWindow
    budget: Budget

    @model_validator(mode="after")
    def _skill_and_version_together(self) -> Self:
        if bool(self.skill_id) != bool(self.skill_version):
            raise ValueError("skill_id and skill_version must be set together")
        return self


class CasePlan(AgentResult):
    """Orchestrator output. The workflow adds Verification, rejects repeated agents and
    trims the plan to the case budget."""

    steps: Annotated[list[PlanStep], Field(min_length=1, max_length=4)]


class TimelineEntry(ContractModel):
    time: UtcDatetime
    description: Annotated[str, StringConstraints(max_length=200)]
    evidence_ids: list[EvidenceId]


class InvestigationHypothesis(ContractModel):
    text: ShortText
    status: Literal["supported", "refuted", "open"]


class InvestigationResult(AgentResult):
    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    timeline: Annotated[list[TimelineEntry], Field(max_length=30)]
    hypotheses: Annotated[list[InvestigationHypothesis], Field(max_length=5)]
    urgent_event_candidates: Annotated[list[UrgentEvent], Field(max_length=15)]


class Disagreement(ContractModel):
    claim_text: ShortText
    reason: ShortText


class VerificationResult(AgentResult):
    agrees: bool
    verdict: CaseVerdict
    confidence: Confidence
    disagreements: list[Disagreement]
    checked_evidence_ids: list[EvidenceId]


class CaseReport(AgentResult):
    """Case output of the Reporting agent. Turkish fields end in `_tr`.

    `data_gaps` comes from `AgentResult`.
    """

    summary_tr: Summary
    verdict: CaseVerdict
    confidence: Confidence
    # Computed by the workflow, not by the agent.
    notify_level: Level
    urgent_events: Annotated[list[UrgentEvent], Field(max_length=15)]
    recommendations: Annotated[list[Recommendation], Field(max_length=8)]

    @field_validator("urgent_events")
    @classmethod
    def _sorted_by_rank(cls, value: list[UrgentEvent]) -> list[UrgentEvent]:
        ranks = [event.rank for event in value]
        if ranks != sorted(ranks):
            raise ValueError("urgent_events must be sorted by rank")
        return value
