"""The Reporting agent (architecture §7, §9): Turkish report, urgent events and recommendations.

Input is a ReportingTask (decision T-45): the AgentTask, the workflow's decision, the claims
Verification did not dispute, the evidence behind them and behind the urgent event candidates,
the candidates from Investigation, data gaps, the offense snapshot and the enrichment.

What the model returns is narrower than CaseReport, and that is the point of the agent: the
decision (criterion 3) and the data gaps (criterion 6) are the input's, never the model's, and
an urgent event carries the model's `rank`, `reason` and `checklist` on top of the candidate's
identifiers copied verbatim (criterion 5). Three output validators hold those rules:
`check_evidence` (which every agent has), `CandidatesUnchanged` and `NoLogText`. The run fills
the rest of CaseReport from the input.

The agent has no tools (T-043): all evidence arrives in the input, under the `ev_c<n>` aliases
of decision T-38. The prompt carries the Turkish report rules of docs/impl/prompts.md, and no
log text may reach the report (criterion 7).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import Agent, AgentRetries, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from ais0c_agents.builder import AgentSpec, check_agent_config, create_agent
from ais0c_agents.evidence import render_context_evidence
from ais0c_agents.manifest import AgentManifest
from ais0c_agents.prompts import PromptTemplate, render_org_context, wrap_json_lines
from ais0c_agents.runner import AgentRun, run_agent, usage_limits
from ais0c_agents.toolset import RunDeps
from ais0c_contracts import (
    AgentTask,
    CaseReport,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    EnrichmentContext,
    EvidenceRef,
    Level,
    OffenseSnapshot,
    Recommendation,
    RunStatus,
    ShortText,
    UrgentEvent,
    Usage,
    UtcDatetime,
)
from ais0c_policy import neutralize_tags

INPUT_SCHEMA: Final = "ReportingTask"
OUTPUT_SCHEMA: Final = "CaseReport"
SUMMARY_MAX_LENGTH: Final = 400
"""`NoteContent.summary_tr`'s limit (contracts.md); the same summary goes into the note."""
OUTPUT_RETRIES: Final = 3
"""Three tries: a model that answers freely miscopies an identifier or quotes an excerpt."""
RETRIES: Final[AgentRetries] = {"tools": 0, "output": OUTPUT_RETRIES}
# The template's inputs besides the shared rules (prompts/reporting/v1.md).
PLACEHOLDERS: Final = frozenset(
    {
        "decision",
        "claims",
        "evidence",
        "urgent_event_candidates",
        "data_gaps",
        "offense",
        "org_context",
    }
)
MAX_CLAIMS: Final = 30
MAX_EVIDENCE: Final = 50
MAX_DATA_GAPS: Final = 20

# Sources of the untrusted blocks; all are `qradar.<x>` (ais0c_policy.untrusted).
DECISION_NOTE: Final = "The workflow decided this case; it is a fact, not evidence."
NO_CLAIMS: Final = "Verification left no undisputed claim."
NO_EVIDENCE: Final = "No evidence is available for this case."
NO_CANDIDATES: Final = (
    "There is no urgent event candidate for this case. Return an empty urgent_events list."
)
NO_DATA_GAPS: Final = "There is no data gap for this case."
CLAIM_SOURCE: Final = "qradar.claim"
CANDIDATE_SOURCE: Final = "qradar.urgent_event"
DATA_GAP_SOURCE: Final = "qradar.data_gap"
OFFENSE_SOURCE: Final = "qradar.offense"

# The urgent event fields the model copies from a candidate, not writes (criterion 5).
IDENTIFIER_FIELDS: Final[tuple[str, ...]] = (
    "time",
    "log_source",
    "event_name",
    "qid",
    "source",
    "destination",
    "username",
    "aql",
    "evidence_id",
)
# The report's free text fields: no log text may be quoted in them (criterion 7).
FREE_TEXT_FIELDS: Final[tuple[str, ...]] = ("summary_tr", "reason", "rationale", "checklist")
MIN_QUOTE: Final = 20
"""A piece of this many characters or more is a copy of log text, not a summary."""
MAX_REJECTION_VALUE: Final = 60


# Not a ContractModel: contract models are defined only in packages/contracts.
class CaseDecision(BaseModel):
    """The workflow's decision and notification level (architecture §9, decision T-42)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: CaseVerdict
    confidence: Confidence
    notify_level: Level


class ReportingTask(BaseModel):
    """Input of the Reporting agent; `input_schema: ReportingTask` in its manifest (T-45)."""

    model_config = ConfigDict(extra="forbid")

    task: AgentTask
    decision: CaseDecision
    """Not the model's to compute or to change (criterion 3)."""
    claims: Annotated[list[Claim], Field(max_length=MAX_CLAIMS)]
    """The claims Verification did not dispute (decision T-45)."""
    evidence: Annotated[list[EvidenceRef], Field(max_length=MAX_EVIDENCE)]
    """The claims' and candidates' evidence. The prompt shows each as `ev_c<n>`, `n` its
    place here from 1 (decision T-38), and the run cites them under those aliases."""
    urgent_event_candidates: Annotated[list[UrgentEvent], Field(max_length=15)]
    data_gaps: Annotated[list[DataGap], Field(max_length=MAX_DATA_GAPS)]
    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    """Organization context; only its catalog and critical assets reach the prompt."""


class ReportedEvent(BaseModel):
    """An urgent event as the model returns it: a candidate's identifiers, its own Turkish text.

    `rank`, `reason` and `checklist` are the model's (criterion 5); every other field is the
    candidate's and must be copied verbatim, which `CandidatesUnchanged` checks.
    """

    model_config = ConfigDict(extra="forbid")

    rank: Annotated[int, Field(ge=1)]
    time: UtcDatetime
    """Typed as the candidate's, so a model that writes a local or offset time is converted to
    UTC and compared as the same instant instead of as different text."""
    log_source: Annotated[str, Field(max_length=120)]
    event_name: Annotated[str, Field(max_length=200)]
    qid: int | None = None
    source: Annotated[str, Field(max_length=100)] | None = None
    destination: Annotated[str, Field(max_length=100)] | None = None
    username: Annotated[str, Field(max_length=100)] | None = None
    reason: ShortText
    checklist: Annotated[list[ShortText], Field(max_length=5)]
    aql: Annotated[str, Field(max_length=2000)] | None = None
    evidence_id: str

    def identifiers(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in IDENTIFIER_FIELDS}


class ReportingOutput(BaseModel):
    """What the model returns: the Turkish text of the report, and nothing else.

    It names itself `CaseReport` for the model, as the prompt says (docs/impl/prompts.md,
    "Output"), and holds only the fields the model owns: the decision (criterion 3) and the
    data gaps (criterion 6) are the input's, and `task_id`, `status`, `claims` and `usage` are
    the run's. No docstring: Pydantic AI would add it to the output tool's description.
    """

    model_config = ConfigDict(extra="forbid", title="CaseReport")

    summary_tr: Annotated[str, Field(min_length=1, max_length=SUMMARY_MAX_LENGTH)]
    urgent_events: Annotated[list[ReportedEvent], Field(max_length=15)]
    recommendations: Annotated[list[Recommendation], Field(max_length=8)] = []
    injection_suspected: bool = False

    @model_validator(mode="after")
    def _ranks_consecutive(self) -> ReportingOutput:
        """`rank` starts at 1 and is consecutive and unique, in the order returned."""
        ranks = [event.rank for event in self.urgent_events]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("urgent_events must be ranked 1, 2, 3, ... in the order returned")
        return self


SPEC: Final = AgentSpec(
    name="Reporting",
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    placeholders=PLACEHOLDERS,
    output_type=ReportingOutput,
    output_description="Return the CaseReport for this case.",
    retries=RETRIES,
)


# --- output validators ------------------------------------------------------------------------


def _short(value: object) -> str:
    """`value` quoted and cut short: it may hold log text (architecture §9)."""
    text = " ".join(str(value).split())
    return repr(text if len(text) <= MAX_REJECTION_VALUE else text[:MAX_REJECTION_VALUE] + "…")


def _piece_is_copied(candidate: UrgentEvent, identifiers: dict[str, object]) -> bool:
    return all(getattr(candidate, name) == identifiers[name] for name in IDENTIFIER_FIELDS)


class CandidatesUnchanged[OutputT: BaseModel]:
    """Output validator: every urgent event is one candidate, identifiers unchanged.

    The model may pick candidates, drop some and reorder them, and it ranks what it keeps
    from 1; it may not invent an event or change an identifier of one it keeps (criterion 5).
    Anything else goes back to the model with the candidate's own values, so it corrects
    itself from them instead of from memory (decision T-27's copy failures).
    """

    def __init__(self, candidates: Sequence[UrgentEvent]) -> None:
        self.candidates = tuple(candidates)

    def __call__(self, ctx: RunContext[RunDeps], output: OutputT) -> OutputT:
        unused = list(self.candidates)
        for event in getattr(output, "urgent_events", ()):
            found = next((c for c in unused if _piece_is_copied(c, event.identifiers())), None)
            if found is None:
                raise ModelRetry(_rejection(event, self.candidates))
            unused.remove(found)
        return output


def _rejection(event: ReportedEvent, candidates: Sequence[UrgentEvent]) -> str:
    """Why one urgent event was rejected, and the candidate to copy instead."""
    same_name = [c for c in candidates if c.event_name == event.event_name]
    if not same_name:
        available = ", ".join(_short(c.event_name) for c in candidates) or "none"
        return (
            f"No urgent event candidate has these identifiers. Copy one of the candidates you "
            f"were given ({available}) and change nothing but its rank, reason and checklist. "
            "Add no event of your own."
        )
    candidate = same_name[0]
    changed = [
        name for name in IDENTIFIER_FIELDS if getattr(candidate, name) != event.identifiers()[name]
    ]
    corrected = ", ".join(f"{name}={_short(getattr(candidate, name))}" for name in changed)
    return (
        f"The identifiers of this urgent event differ from its candidate in {', '.join(changed)}. "
        f"Copy the candidate's own value: {corrected}."
    )


class NoLogText[OutputT: BaseModel]:
    """Output validator: no evidence excerpt is quoted in the report (criterion 7).

    A piece of MIN_QUOTE characters or more of an excerpt in `summary_tr`, a `reason`, a
    `rationale` or a `checklist` item is a copy of log text, which may carry what an attacker
    wrote into the log (architecture §22). The output goes back to the model naming the
    evidence and the field, without repeating the log.
    """

    def __init__(self, evidence: Sequence[EvidenceRef]) -> None:
        self.excerpts = tuple(
            (f"ev_c{position}", ref.excerpt)
            for position, ref in enumerate(evidence, start=1)
            if ref.excerpt
        )

    def __call__(self, ctx: RunContext[RunDeps], output: OutputT) -> OutputT:
        for alias, excerpt in self.excerpts:
            for piece in _quoted_pieces(excerpt):
                for field_name in _fields_holding(output, piece):
                    raise ModelRetry(
                        f"Your {field_name} copies {MIN_QUOTE} or more characters of the "
                        f"evidence excerpt of {alias}. Summarize it in your own Turkish words; "
                        "only structural fields (time, IP, user, event name) may be repeated."
                    )
        return output


def _quoted_pieces(excerpt: str) -> list[str]:
    """Every window of MIN_QUOTE characters inside the excerpt, whitespace collapsed.

    A window that spans a space is a piece too, so the comparison runs on the excerpt and the
    field text with their whitespace collapsed to single spaces: quoting words the model put
    on two lines is as much a copy as quoting them on one.
    """
    normalized = " ".join(excerpt.split())
    return [
        normalized[start : start + MIN_QUOTE] for start in range(len(normalized) - MIN_QUOTE + 1)
    ]


def _fields_holding(output: BaseModel, piece: str) -> list[str]:
    """The report fields `piece` appears in, by name.

    The fields are reached through the output's own models: an event's `reason` and
    `checklist` live under `urgent_events`, a recommendation's `rationale` under
    `recommendations`, so the fields are walked rather than read off the output itself. The
    text is compared with its whitespace collapsed, as in _quoted_pieces.
    """
    found: list[str] = []
    pending: list[BaseModel] = [output]
    while pending:
        model = pending.pop()
        for name in FREE_TEXT_FIELDS:
            value = getattr(model, name, None)
            if value is None:
                continue
            values = value if isinstance(value, list) else [value]
            if any(isinstance(item, str) and piece in " ".join(item.split()) for item in values):
                found.append(name)
        pending.extend(_nested_models(model))
    return found


def _nested_models(model: BaseModel) -> list[BaseModel]:
    """`model`'s own values that are models, e.g. the events and the recommendations."""
    found: list[BaseModel] = []
    pending: list[object] = [getattr(model, name, None) for name in type(model).model_fields]
    while pending:
        value = pending.pop()
        if isinstance(value, BaseModel):
            found.append(value)
        elif isinstance(value, list | tuple):
            pending.extend(value)
    return found


# --- the agent --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportingAgent:
    """The Reporting agent, built once and run once per evaluation (architecture §7)."""

    manifest: AgentManifest
    prompt: PromptTemplate
    model: Model
    capabilities: Sequence[AbstractCapability[RunDeps]] = field(default_factory=tuple)

    def bind(self, task: ReportingTask) -> Agent[RunDeps, ReportingOutput]:
        """The Pydantic AI agent for one run: the output validators of this run's input.

        `create_agent` gives every agent `check_evidence`; Reporting adds the two checks that
        need the run's candidates and evidence. Building them here keeps them out of workflow
        code and off every other run of the same ReportingAgent.
        """
        agent = create_agent(
            SPEC,
            manifest=self.manifest,
            model=self.model,
            toolsets=[],
            aql=None,
            capabilities=self.capabilities,
        )
        agent.output_validator(CandidatesUnchanged(task.urgent_event_candidates))
        agent.output_validator(NoLogText(task.evidence))
        return agent

    def render_instructions(self, task: ReportingTask, *, nonce: str) -> str:
        """The prompt for one run; `nonce` is that run's `untrusted_*` tag suffix."""
        return self.prompt.render(
            {
                "decision": _render_decision(task.decision),
                "claims": _render_claims(task.claims, nonce=nonce),
                "evidence": render_context_evidence(task.evidence, nonce=nonce) or NO_EVIDENCE,
                "urgent_event_candidates": _render_candidates(
                    task.urgent_event_candidates, nonce=nonce
                ),
                "data_gaps": _render_data_gaps(task.data_gaps, nonce=nonce),
                "offense": wrap_json_lines(
                    [task.offense.model_dump(mode="json")],
                    source=OFFENSE_SOURCE,
                    nonce=nonce,
                ),
                "org_context": render_org_context(
                    task.enrichment.catalog, critical_assets=task.enrichment.critical_asset_hits
                ),
            }
        )

    async def run(
        self,
        task: ReportingTask,
        *,
        run_id: str,
        nonce: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> AgentRun[CaseReport]:
        """Produce the Turkish case report as the agent run `run_id` (`agent_runs.run_id`).

        The decision, the data gaps and the claims come from `task`, never from the model
        (criteria 3 and 6); only the Turkish text is the model's. `nonce` must be fresh for
        every run (policy.new_nonce()). Raises ValueError when the task is for another agent.
        """
        if task.task.agent_id != self.manifest.id:
            raise ValueError(f"task is for agent {task.task.agent_id!r}, not {self.manifest.id!r}")
        limits = usage_limits(self.manifest, task.task.budget)
        deps = RunDeps(
            run_id=run_id,
            case_id=task.task.case_id,
            hunt_id=task.task.hunt_id,
            time_window=task.task.time_window,
            nonce=nonce,
            context_evidence=tuple(ref.evidence_id for ref in task.evidence),
        )

        def finalize(output: ReportingOutput, usage: Usage) -> CaseReport:
            events = [
                UrgentEvent.model_validate(
                    {
                        **event.identifiers(),
                        "rank": event.rank,
                        "reason": event.reason,
                        "checklist": event.checklist,
                    }
                )
                for event in output.urgent_events
            ]
            return CaseReport.model_validate(
                {
                    "task_id": task.task.task_id,
                    "status": RunStatus.COMPLETED,
                    "usage": usage,
                    "claims": task.claims,
                    "injection_suspected": output.injection_suspected,
                    "data_gaps": task.data_gaps,
                    "verdict": task.decision.verdict,
                    "confidence": task.decision.confidence,
                    "notify_level": task.decision.notify_level,
                    "summary_tr": output.summary_tr,
                    "urgent_events": events,
                    "recommendations": output.recommendations,
                }
            )

        return await run_agent(
            self.bind(task),
            user_prompt=neutralize_tags(task.task.objective),
            instructions=self.render_instructions(task, nonce=nonce),
            deps=deps,
            limits=limits,
            prompt=self.prompt,
            finalize=finalize,
            clock=clock,
        )


def _render_decision(decision: CaseDecision) -> str:
    return (
        f"{DECISION_NOTE}\n"
        f"verdict: {decision.verdict.value}\n"
        f"confidence: {decision.confidence.value}\n"
        f"notify_level: {decision.notify_level.value}"
    )


def _render_claims(claims: Sequence[Claim], *, nonce: str) -> str:
    """The claims Verification did not dispute, as untrusted data (decision T-45).

    A claim's text is model text that an attacker may have shaped, so it is wrapped. Its
    `evidence_ids` are the run's evidence IDs, not the aliases the model sees, so they are
    left out of the text: the evidence blocks carry the aliases.
    """
    if not claims:
        return NO_CLAIMS
    return wrap_json_lines(
        [claim.model_dump(mode="json", exclude={"evidence_ids"}) for claim in claims],
        source=CLAIM_SOURCE,
        nonce=nonce,
    )


def _render_candidates(candidates: Sequence[UrgentEvent], *, nonce: str) -> str:
    """The urgent event candidates, one untrusted block each, for the model to copy."""
    if not candidates:
        return NO_CANDIDATES
    return "\n\n".join(
        wrap_json_lines([candidate.model_dump(mode="json")], source=CANDIDATE_SOURCE, nonce=nonce)
        for candidate in candidates
    )


def _render_data_gaps(gaps: Sequence[DataGap], *, nonce: str) -> str:
    if not gaps:
        return NO_DATA_GAPS
    return wrap_json_lines(
        [gap.model_dump(mode="json") for gap in gaps], source=DATA_GAP_SOURCE, nonce=nonce
    )


def build_reporting_agent(
    *,
    manifest: AgentManifest,
    prompt: PromptTemplate,
    model: Model,
    capabilities: Sequence[AbstractCapability[RunDeps]] = (),
) -> ReportingAgent:
    """Build the agent once, outside any workflow (TemporalDurability requires it).

    Raises ValueError when the manifest does not describe a reporting agent, its prompt or
    shared rules is not the one given, or the prompt does not take this agent's inputs.
    """
    check_agent_config(SPEC, manifest, prompt)
    return ReportingAgent(manifest=manifest, prompt=prompt, model=model, capabilities=capabilities)
