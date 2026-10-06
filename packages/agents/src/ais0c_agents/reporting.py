"""The Reporting agent (architecture §7, §9): Turkish report, urgent events and recommendations.

Input is a ReportingTask (decision T-45): the AgentTask, the workflow's decision, the claims
Verification did not dispute, the evidence behind them and behind the urgent event candidates,
the candidates from Investigation, data gaps, the offense snapshot and the enrichment.

What the model returns is narrower than CaseReport, and that is the point of the agent: the
decision and the data gaps are the input's, never the model's. An urgent event is the number
of a candidate plus the model's `rank`, `reason` and `checklist`; the run copies the
candidate's nine identifiers into the report, so the model never copies an evidence ID or a
query (decision T-50). Four output validators hold the rules: `check_evidence` (which every
agent has), `check_candidates`, `check_log_text` and `check_summary_aliases`. The run fills the
rest of CaseReport from the input.

The agent has no tools (T-043): all evidence arrives in the input, under the `ev_c<n>` aliases
of decision T-38, and no gateway evidence ID reaches the model (decision T-27). The agent is
built once, as TemporalDurability requires; what its validators need of one run travels in
RunDeps, which the model never sees. The prompt carries the Turkish report rules of
docs/impl/prompts.md, and no log text may reach the report.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import Agent, AgentRetries, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from ais0c_agents.builder import AgentSpec, check_agent_config, create_agent
from ais0c_agents.evidence import context_alias, render_context_evidence
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
)
from ais0c_policy import neutralize_tags

INPUT_SCHEMA: Final = "ReportingTask"
OUTPUT_SCHEMA: Final = "CaseReport"
SUMMARY_MAX_LENGTH: Final = 400
"""`NoteContent.summary_tr`'s limit (contracts.md); the same summary goes into the note."""
OUTPUT_RETRIES: Final = 3
"""Three tries: a model that answers freely names a wrong candidate, cites an alias it was not
given or quotes an excerpt, and each is a correction of its own."""
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
MAX_URGENT_EVENTS: Final = 15

DECISION_NOTE: Final = "The workflow decided this case; it is a fact, not evidence."
NO_CLAIMS: Final = "Verification left no undisputed claim."
NO_EVIDENCE: Final = "No evidence is available for this case."
NO_CANDIDATES: Final = (
    "There is no urgent event candidate for this case. Return an empty urgent_events list."
)
NO_DATA_GAPS: Final = "There is no data gap for this case."
# Sources of the untrusted blocks: earlier agents' model text is `agent.<kind>` (decision T-48),
# the offense is QRadar's own data.
CLAIM_SOURCE: Final = "agent.claim"
CANDIDATE_SOURCE: Final = "agent.urgent_event"
DATA_GAP_SOURCE: Final = "agent.data_gap"
OFFENSE_SOURCE: Final = "qradar.offense"

# The urgent event fields the run copies from the chosen candidate (decision T-50).
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
# What a candidate block leaves out: Investigation's rank, which the model would confuse with
# its own; the evidence ID, which the block shows as its alias (decision T-27); and the AQL,
# which the run copies and the model neither reads nor writes.
CANDIDATE_HIDDEN_FIELDS: Final = frozenset({"rank", "evidence_id", "aql"})
# The report's free text fields: no log text may be quoted in them.
FREE_TEXT_FIELDS: Final[tuple[str, ...]] = ("summary_tr", "reason", "rationale", "checklist")
MIN_QUOTE: Final = 20
"""A piece of this many characters or more is a copy of log text, not a summary."""
# An evidence alias the model may see in this run: `ev_c<n>`, `ev_<n>` or `ev_none` (T-54 (2)).
EVIDENCE_ALIAS: Final = re.compile(r"\bev_(?:c[0-9]+|[0-9]+|none)\b", re.IGNORECASE)


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
    """Not the model's to compute or to change."""
    claims: Annotated[list[Claim], Field(max_length=MAX_CLAIMS)]
    """The claims Verification did not dispute (decision T-45)."""
    evidence: Annotated[list[EvidenceRef], Field(max_length=MAX_EVIDENCE)]
    """The claims' and candidates' evidence. The prompt shows each as `ev_c<n>`, `n` its
    place here from 1 (decision T-38), and the run cites them under those aliases."""
    urgent_event_candidates: Annotated[list[UrgentEvent], Field(max_length=MAX_URGENT_EVENTS)]
    """The model names the n-th as candidate n; each one's evidence is in `evidence`."""
    data_gaps: Annotated[list[DataGap], Field(max_length=MAX_DATA_GAPS)]
    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    """Organization context; only its catalog and critical assets reach the prompt."""

    @model_validator(mode="after")
    def _evidence_given(self) -> Self:
        """Every claim's and every candidate's evidence is in `evidence` (T-047, T-54 (4)).

        The prompt can show a candidate's evidence only as an alias of the task's evidence, and
        the report's claims must rest on evidence the case carries.
        """
        given = {ref.evidence_id for ref in self.evidence}
        claims = [
            number
            for number, claim in enumerate(self.claims, start=1)
            if not set(claim.evidence_ids) <= given
        ]
        candidates = [
            number
            for number, candidate in enumerate(self.urgent_event_candidates, start=1)
            if candidate.evidence_id not in given
        ]
        problems = [
            f"{kind} {', '.join(map(str, numbers))} cite evidence that is not in the task's "
            "evidence"
            for kind, numbers in (("claims", claims), ("urgent event candidates", candidates))
            if numbers
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return self


# An urgent event as the model returns it: the candidate it chose and its own Turkish text.
# `candidate` is the number of the candidate's block, from 1, and is not bounded in the schema:
# check_candidates sends a wrong number back with the numbers the model may use, which a schema
# error would not name. No docstring: Pydantic AI would put it into the output tool's schema.
class ReportedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: int
    rank: Annotated[int, Field(ge=1)]
    reason: ShortText
    checklist: Annotated[list[ShortText], Field(max_length=5)]


# What the model returns: the Turkish text of the report, and nothing else. It names itself
# `CaseReport` for the model, as the prompt says (docs/impl/prompts.md, "Output"), and holds
# only the fields the model owns: the decision and the data gaps are the input's, and
# `task_id`, `status`, `claims` and `usage` are the run's. No docstring: Pydantic AI would add
# it to the output tool's description.
class ReportingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", title="CaseReport")

    summary_tr: Annotated[str, Field(min_length=1, max_length=SUMMARY_MAX_LENGTH)]
    urgent_events: Annotated[list[ReportedEvent], Field(max_length=MAX_URGENT_EVENTS)]
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


def check_candidates(ctx: RunContext[RunDeps], output: ReportingOutput) -> ReportingOutput:
    """Output validator: every urgent event names a candidate of this run, each at most once.

    The model may pick candidates, drop some and reorder them; it may not name a number the
    prompt did not show or one it already used (decision T-50). The run's candidate count
    travels in RunDeps.urgent_event_candidate_count; the model never sees RunDeps. A wrong
    output goes back to the model with the numbers it may use.
    """
    count = ctx.deps.urgent_event_candidate_count
    numbers = [event.candidate for event in output.urgent_events]
    unknown = sorted({number for number in numbers if not 1 <= number <= count})
    repeated = sorted(
        {number for number in numbers if 1 <= number <= count and numbers.count(number) > 1}
    )
    if not unknown and not repeated:
        return output
    problems: list[str] = []
    if unknown:
        problems.append(f"there is no candidate {_listed(unknown)}")
    if repeated:
        problems.append(f"candidate {_listed(repeated)} is chosen more than once")
    if count == 0:
        allowed = "This case has no urgent event candidate: return an empty urgent_events list."
    else:
        allowed = (
            f"The candidates are {_listed(range(1, count + 1))}: name each one at most once, "
            "by its number."
        )
    raise ModelRetry(f"In urgent_events, {'; '.join(problems)}. {allowed}")


def _listed(numbers: Sequence[int]) -> str:
    return ", ".join(str(number) for number in numbers)


def check_log_text(ctx: RunContext[RunDeps], output: ReportingOutput) -> ReportingOutput:
    """Output validator: no evidence excerpt is quoted in the report.

    A piece of MIN_QUOTE characters or more of an excerpt in `summary_tr`, a `reason`, a
    `rationale` or a `checklist` item is a copy of log text, which may carry what an attacker
    wrote into the log (architecture §22). The excerpts travel in RunDeps.context_excerpts, in
    the order of the context evidence, so the n-th is `ev_c<n>`'s. The output goes back to the
    model naming the evidence and the field, without repeating the log.
    """
    for position, excerpt in enumerate(ctx.deps.context_excerpts, start=1):
        for piece in _quoted_pieces(excerpt):
            for field_name in _fields_holding(output, piece):
                raise ModelRetry(
                    f"Your {field_name} copies {MIN_QUOTE} or more characters of the "
                    f"evidence excerpt of {context_alias(position)}. Summarize it in your own "
                    "Turkish words; only structural fields (time, IP, user, event name) may be "
                    "repeated."
                )
    return output


def check_summary_aliases(ctx: RunContext[RunDeps], output: ReportingOutput) -> ReportingOutput:
    """Output validator: `summary_tr` names no evidence alias (decision T-54 (2)).

    The operator reads the summary as plain Turkish, in the note and the e-mail, and cannot look
    an alias such as `ev_c1` up. The output goes back to the model naming the aliases it wrote.
    """
    del ctx
    found = list(
        dict.fromkeys(match.group(0) for match in EVIDENCE_ALIAS.finditer(output.summary_tr))
    )
    if found:
        raise ModelRetry(
            f"Your summary_tr names evidence aliases ({', '.join(found)}). The operator cannot "
            "look an alias up: write summary_tr without them. Evidence is cited only in a "
            "recommendation's evidence_ids."
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
    agent: Agent[RunDeps, ReportingOutput]

    def render_instructions(self, task: ReportingTask, *, nonce: str) -> str:
        """The prompt for one run; `nonce` is that run's `untrusted_*` tag suffix."""
        return self.prompt.render(
            {
                "decision": _render_decision(task.decision),
                "claims": _render_claims(task.claims, nonce=nonce),
                "evidence": render_context_evidence(task.evidence, nonce=nonce) or NO_EVIDENCE,
                "urgent_event_candidates": render_candidates(task, nonce=nonce),
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

        The decision, the data gaps, the claims and the urgent events' identifiers come from
        `task`, never from the model; only the Turkish text and the choice of candidates are
        the model's. `nonce` must be fresh for every run (policy.new_nonce()). Raises
        ValueError when the task is for another agent.
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
            context_excerpts=tuple(ref.excerpt for ref in task.evidence),
            urgent_event_candidate_count=len(task.urgent_event_candidates),
        )

        def finalize(output: ReportingOutput, usage: Usage) -> CaseReport:
            # check_candidates let only numbers 1..len(candidates) through, each once.
            candidates = task.urgent_event_candidates
            events = [
                UrgentEvent.model_validate(
                    {
                        **candidates[event.candidate - 1].model_dump(
                            include=set(IDENTIFIER_FIELDS)
                        ),
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
            self.agent,
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


def render_candidates(task: ReportingTask, *, nonce: str) -> str:
    """The urgent event candidates, one untrusted block each, numbered for the model to choose.

    A block holds one JSON line: the candidate's number (`candidate`, from 1), what describes
    the event, Investigation's reason and checklist, and the alias of its evidence as
    `evidence` (`ev_c<n>`, decision T-38). CANDIDATE_HIDDEN_FIELDS stay out, so no gateway
    evidence ID reaches the model (decision T-27). Returns NO_CANDIDATES when there is none.
    """
    if not task.urgent_event_candidates:
        return NO_CANDIDATES
    aliases = {
        ref.evidence_id: context_alias(position)
        for position, ref in enumerate(task.evidence, start=1)
    }
    return "\n\n".join(
        wrap_json_lines(
            [
                {
                    "candidate": number,
                    **candidate.model_dump(mode="json", exclude=set(CANDIDATE_HIDDEN_FIELDS)),
                    # ReportingTask holds every candidate's evidence in `evidence`.
                    "evidence": aliases[candidate.evidence_id],
                }
            ],
            source=CANDIDATE_SOURCE,
            nonce=nonce,
        )
        for number, candidate in enumerate(task.urgent_event_candidates, start=1)
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

    The agent has no tools. `capabilities` are attached when the agent is built, the only time
    Pydantic AI binds them; a workflow passes TemporalDurability here. Every run uses the same
    Pydantic AI agent: its validators read the run's candidates and excerpts from RunDeps. The
    output holds no query of its own (the run copies the candidates' checked AQL), so no AQL
    rules are needed. Raises ValueError when the manifest does not describe a reporting agent,
    names a toolset profile, its prompt or shared rules is not the one given, or the prompt
    does not take this agent's inputs.
    """
    check_agent_config(SPEC, manifest, prompt)
    agent = create_agent(
        SPEC, manifest=manifest, model=model, toolsets=[], aql=None, capabilities=capabilities
    )
    agent.output_validator(check_candidates)
    agent.output_validator(check_log_text)
    agent.output_validator(check_summary_aliases)
    return ReportingAgent(manifest=manifest, prompt=prompt, agent=agent)
