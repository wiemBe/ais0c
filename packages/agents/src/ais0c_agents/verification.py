"""The Verification agent (architecture §7, §22): the independent check of one case decision.

Verification runs after Triage and Investigation and is the second control layer against an
attacker who tries to convince the AI that its own activity is harmless. It sees the reviewed
decision's structural fields (`verdict`, `confidence`, `ai_level`), the claims, the evidence they
rest on and the offense's structural fields, never the earlier agent's free text (decision T-45),
and it re-reads the evidence of the critical claims at the source through its narrow profile
(architecture §11.2: a 2-hour query window, 200 rows, payload and free-text fields dropped).

What reaches the model, each part in its trust layer (architecture §22):

- the reviewed decision, three enum values, as plain text;
- the claim texts, model text that may derive from log data an attacker wrote, each in its own
  `untrusted_*` block as `agent.claim` (decision T-48) together with the evidence aliases the
  claim rests on;
- the offense's structural fields in one `untrusted_*` block, without `description` and
  `rule_names`: free text an attacker can write;
- the evidence of the claims as `ev_c<n>` blocks (decision T-38);
- the plan step's objective, Orchestrator text, in an `agent.objective` block (decisions T-48,
  T-54); the user prompt is the platform's fixed RUN_PROMPT;
- the task's window as AQL parts ready to copy, START and STOP in the QRadar console's time
  zone, widened as far as the profile's 2-hour AQL window allows (decision T-53).

Code decides one thing before the model runs: a claim that cites evidence this case does not
carry does not go to the model. The code puts it in the result as a disagreement with the reason
"evidence not found" and sets `agrees` to false. When no claim survives, no model request is made
at all: the run completes with the code's answer, `agrees=false`, `suspicious` and low confidence,
which is what the shared rules ask for when there is no evidence to judge.

Verification never changes a decision: a disagreement goes to operator review (decision T-42).
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, AgentRetries, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from ais0c_agents.builder import AgentSpec, check_agent_config, create_agent
from ais0c_agents.evidence import context_alias, render_context_evidence
from ais0c_agents.gateway import GatewayClient
from ais0c_agents.investigation import CONSOLE_ZONE, render_time_window
from ais0c_agents.manifest import AgentManifest
from ais0c_agents.prompts import PromptTemplate, wrap_json_lines
from ais0c_agents.runner import AgentRun, prompt_tool_budget, run_agent, usage_limits
from ais0c_agents.toolset import RunDeps, ToolsetProfile, build_gateway_toolset
from ais0c_contracts import (
    AgentTask,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    Disagreement,
    EvidenceId,
    EvidenceRef,
    Level,
    OffenseSnapshot,
    RunStatus,
    Usage,
    VerificationResult,
)
from ais0c_policy import neutralize_tags

INPUT_SCHEMA: Final = "VerificationTask"
OUTPUT_SCHEMA: Final = "VerificationResult"
# Corrections the model gets for invalid tool calls and for invalid output.
TOOL_RETRIES: Final = 2
OUTPUT_RETRIES: Final = 2
RETRIES: Final[AgentRetries] = {"tools": TOOL_RETRIES, "output": OUTPUT_RETRIES}
# The template's inputs besides the shared rules (prompts/verification/v1.md).
PLACEHOLDERS: Final = frozenset(
    {
        "objective",
        "reviewed",
        "claims",
        "offense",
        "evidence",
        "time_window",
        "tools",
        "tool_budget",
    }
)
# The claims one run checks, and the evidence pieces their claims may cite.
MAX_CLAIMS: Final = 20
MAX_EVIDENCE: Final = 40
# The offense's free-text fields never reach this prompt (decision T-45).
OFFENSE_TEXT_FIELDS: Final = frozenset({"description", "rule_names"})
OFFENSE_SOURCE: Final = "qradar.offense"
CLAIM_SOURCE: Final = "agent.claim"
"""Claim texts are another model's words about this case: untrusted, not evidence (T-48)."""
OBJECTIVE_SOURCE: Final = "agent.objective"
"""The plan step's objective is the Orchestrator's text (decisions T-48, T-54)."""
RUN_PROMPT: Final = "Check the decision described in the instructions and return the result."
# The longest START/STOP span the AQL Guard of qradar-verify-read allows (its `aql.max_window` in
# config/policies/qradar.yaml), and the example query's LIMIT within its 200 rows.
MAX_QUERY_WINDOW: Final = timedelta(hours=2)
EXAMPLE_LIMIT: Final = 50
NO_CLAIMS: Final = "No claim was handed over to check."
NO_EVIDENCE: Final = "No evidence was handed over."
# Why code, not the model, contested a claim.
EVIDENCE_NOT_FOUND: Final = "evidence not found in this case"
# How much of a claim text a rejection echoes back to the model.
MAX_QUOTED_TEXT: Final = 120


# Not a ContractModel: contract models are defined only in packages/contracts.
class ReviewedDecision(BaseModel):
    """The decision under review. Its free text is not here (decision T-45)."""

    model_config = ConfigDict(extra="forbid")

    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level


# Not a ContractModel: contract models are defined only in packages/contracts.
class ReviewedClaim(BaseModel):
    """One claim of the reviewed decision, and the workflow's mark that it is critical.

    The mark is the workflow's (T-026): the agent checks every critical claim's evidence at
    the source, and does not decide which claims are critical.
    """

    model_config = ConfigDict(extra="forbid")

    claim: Claim
    critical: bool = False


# Not a ContractModel: contract models are defined only in packages/contracts.
class VerificationTask(BaseModel):
    """Input of the Verification agent; `input_schema: VerificationTask` in its manifest."""

    model_config = ConfigDict(extra="forbid")

    task: AgentTask
    reviewed: ReviewedDecision
    claims: Annotated[list[ReviewedClaim], Field(max_length=MAX_CLAIMS)]
    evidence: Annotated[list[EvidenceRef], Field(max_length=MAX_EVIDENCE)] = []
    """The evidence the claims rest on; the prompt shows it as `ev_c<n>` (decision T-38)."""
    offense: OffenseSnapshot


# What the model returns: a VerificationResult without task_id, status and usage, which the run
# fills in. The model sees this schema under the name VerificationResult, as its prompt says. No
# docstring: Pydantic AI would add it to the output tool's description.
class VerificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", title="VerificationResult")

    agrees: bool
    verdict: CaseVerdict
    confidence: Confidence
    disagreements: list[Disagreement]
    checked_evidence_ids: list[EvidenceId]
    claims: list[Claim]
    data_gaps: list[DataGap]
    injection_suspected: bool


SPEC: Final = AgentSpec(
    name="Verification",
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    placeholders=PLACEHOLDERS,
    output_type=VerificationOutput,
    output_description="Return the VerificationResult for this case.",
    retries=RETRIES,
)


@dataclass(frozen=True)
class ClaimCheck:
    """What code decides about the task's claims before the model sees them."""

    claims: tuple[ReviewedClaim, ...]
    """The claims whose cited evidence this case carries."""
    disagreements: tuple[Disagreement, ...]
    """One per claim whose evidence is not here; these go into the result as they are."""


def check_claims(task: VerificationTask) -> ClaimCheck:
    """Split the task's claims into the ones the model may see and the code's disagreements.

    A claim whose cited evidence is not all in `task.evidence` does not go to the model: the
    code reports it as a disagreement with the reason EVIDENCE_NOT_FOUND, which also makes
    `agrees` false. An evidence ID can only be the one the earlier run cited (decision T-27),
    so naming it in the reason is safe.
    """
    available = {ref.evidence_id for ref in task.evidence}
    claims: list[ReviewedClaim] = []
    disagreements: list[Disagreement] = []
    for item in task.claims:
        missing = [
            evidence_id for evidence_id in item.claim.evidence_ids if evidence_id not in available
        ]
        if missing:
            disagreements.append(
                Disagreement(
                    claim_text=item.claim.text,
                    reason=f"{EVIDENCE_NOT_FOUND}: {', '.join(missing)}",
                )
            )
        else:
            claims.append(item)
    return ClaimCheck(claims=tuple(claims), disagreements=tuple(disagreements))


def check_disagreements(ctx: RunContext[RunDeps], output: VerificationOutput) -> VerificationOutput:
    """Output validator: `agrees` is answerable and every disagreement names a claim of this run.

    Two rules: `agrees=false` needs at least one disagreement, and every `claim_text` is the
    text of one of the claims the prompt showed, so the workflow can tell which claim the
    verifier contested. The run's claims travel in RunDeps.reviewed_claim_texts; the model
    never sees RunDeps. A model that breaks a rule gets the output back (Pydantic AI output
    retries); the message quotes only its own text, tag-neutralized and cut short.
    """
    if not output.agrees and not output.disagreements:
        raise ModelRetry(
            "The result says agrees=false but names no disagreement. Add one disagreement "
            "with its claim_text copied exactly from the claims above, or set agrees=true."
        )
    known = ctx.deps.reviewed_claim_texts
    unknown = [item.claim_text for item in output.disagreements if item.claim_text not in known]
    if unknown:
        quoted = ", ".join(_quoted(text) for text in dict.fromkeys(unknown))
        raise ModelRetry(
            f"These disagreement texts are not among the claims you were shown ({quoted}). "
            "Copy one claim_text exactly from the claims above, or remove the disagreement."
        )
    return output


@dataclass(frozen=True)
class VerificationAgent:
    manifest: AgentManifest
    prompt: PromptTemplate
    profile: ToolsetProfile
    agent: Agent[RunDeps, VerificationOutput]
    console_zone: ZoneInfo = CONSOLE_ZONE
    """The QRadar console's time zone, in which the prompt writes START and STOP (T-53)."""
    max_query_window: timedelta = MAX_QUERY_WINDOW
    """How far apart START and STOP may be in one query of the agent's profile."""

    def render_instructions(self, task: VerificationTask, *, nonce: str, tool_budget: int) -> str:
        """The prompt for one run; `nonce` is that run's `untrusted_*` tag suffix.

        The claims code rejected are left out (check_claims); everything the model may see is
        here, each part inside its trust layer.
        """
        checked = check_claims(task)
        aliases = {
            ref.evidence_id: context_alias(position)
            for position, ref in enumerate(task.evidence, start=1)
        }
        return self.prompt.render(
            {
                "objective": wrap_json_lines(
                    [{"objective": task.task.objective}], source=OBJECTIVE_SOURCE, nonce=nonce
                ),
                "reviewed": render_reviewed(task.reviewed),
                # check_claims keeps a claim only when all its evidence is in aliases.
                "claims": render_claims(checked.claims, aliases, nonce=nonce),
                "offense": wrap_json_lines(
                    [task.offense.model_dump(mode="json", exclude=set(OFFENSE_TEXT_FIELDS))],
                    source=OFFENSE_SOURCE,
                    nonce=nonce,
                ),
                "evidence": render_context_evidence(task.evidence, nonce=nonce) or NO_EVIDENCE,
                "time_window": render_time_window(
                    task.task.time_window,
                    self.console_zone,
                    limit=EXAMPLE_LIMIT,
                    max_span=self.max_query_window,
                ),
                "tools": ", ".join(tool.id for tool in self.profile.tools),
                "tool_budget": str(tool_budget),
            }
        )

    async def run(
        self,
        task: VerificationTask,
        *,
        run_id: str,
        nonce: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> AgentRun[VerificationResult]:
        """Check one decision as the agent run `run_id` (`agent_runs.run_id`).

        Every tool call carries `run_id`, so the gateway records it under the run. `nonce` must
        be fresh for every run (policy.new_nonce()). Raises ValueError when the task is for
        another agent or `run_id` is empty or too long.
        """
        if task.task.agent_id != self.manifest.id:
            raise ValueError(f"task is for agent {task.task.agent_id!r}, not {self.manifest.id!r}")
        checked = check_claims(task)
        if not checked.claims:
            # Nothing the model could check is left: its answer could only be empty, so the
            # code answers and the run completes without a model request.
            return self._checked_by_code(task, checked)
        limits = usage_limits(self.manifest, task.task.budget)
        tool_budget = prompt_tool_budget(self.manifest, task.task.budget)
        deps = RunDeps(
            run_id=run_id,
            case_id=task.task.case_id,
            hunt_id=task.task.hunt_id,
            time_window=task.task.time_window,
            nonce=nonce,
            context_evidence=tuple(ref.evidence_id for ref in task.evidence),
            reviewed_claim_texts=tuple(item.claim.text for item in checked.claims),
        )

        def finalize(output: VerificationOutput, usage: Usage) -> VerificationResult:
            # The code's disagreements come first and decide `agrees` when there is one
            # (check_claims): an answer that contests the decision needs a reason.
            return VerificationResult.model_validate(
                {
                    **output.model_dump(),
                    "agrees": output.agrees and not checked.disagreements,
                    "disagreements": [*checked.disagreements, *output.disagreements],
                    "task_id": task.task.task_id,
                    "status": RunStatus.COMPLETED,
                    "usage": usage,
                }
            )

        return await run_agent(
            self.agent,
            user_prompt=RUN_PROMPT,
            instructions=self.render_instructions(task, nonce=nonce, tool_budget=tool_budget),
            deps=deps,
            limits=limits,
            prompt=self.prompt,
            finalize=finalize,
            clock=clock,
        )

    def _checked_by_code(
        self, task: VerificationTask, checked: ClaimCheck
    ) -> AgentRun[VerificationResult]:
        """The result of a run whose every claim cites evidence this case does not have."""
        # A run that made no model request and no tool call used nothing.
        usage = Usage(tokens=0, tool_calls=0, seconds=0.0)
        result = VerificationResult.model_validate(
            {
                "agrees": False,
                # Nothing could be read, so the agent states no verdict of its own and lowers
                # its confidence: the shared rules ask for suspicious when evidence is
                # insufficient, and suspicious is the closest value the contract has.
                "verdict": CaseVerdict.SUSPICIOUS,
                "confidence": Confidence.LOW,
                "disagreements": list(checked.disagreements),
                "checked_evidence_ids": [],
                "claims": [],
                "data_gaps": [],
                "injection_suspected": False,
                "task_id": task.task.task_id,
                "status": RunStatus.COMPLETED,
                "usage": usage,
            }
        )
        return AgentRun(
            status=RunStatus.COMPLETED,
            result=result,
            usage=result.usage,
            prompt_version=self.prompt.version,
            prompt_hash=self.prompt.sha256,
            error=None,
            messages=[],
        )


def render_reviewed(reviewed: ReviewedDecision) -> str:
    """The decision under review, one field per line: enum values only, nothing untrusted."""
    return (
        f"verdict: {reviewed.verdict.value}\n"
        f"confidence: {reviewed.confidence.value}\n"
        f"ai_level: {reviewed.ai_level.value}"
    )


def render_claims(
    claims: Sequence[ReviewedClaim], aliases: Mapping[str, str], *, nonce: str
) -> str:
    """The claims under review as untrusted case data: one block per claim.

    A block holds one JSON line with the claim's text, the workflow's critical mark and the
    aliases of the evidence the claim rests on; the gateway's evidence IDs stay out. Returns
    NO_CLAIMS when there is nothing to check.
    """
    return (
        "\n\n".join(
            wrap_json_lines(
                [
                    {
                        "claim": item.claim.text,
                        "critical": item.critical,
                        # check_claims keeps a claim only when all its evidence is in aliases.
                        "evidence": [
                            aliases[evidence_id] for evidence_id in item.claim.evidence_ids
                        ],
                    }
                ],
                source=CLAIM_SOURCE,
                nonce=nonce,
            )
            for item in claims
        )
        or NO_CLAIMS
    )


def build_verification_agent(
    *,
    manifest: AgentManifest,
    prompt: PromptTemplate,
    profiles: Mapping[str, ToolsetProfile],
    gateway: GatewayClient,
    model: Model,
    capabilities: Sequence[AbstractCapability[RunDeps]] = (),
    console_zone: ZoneInfo = CONSOLE_ZONE,
    max_query_window: timedelta = MAX_QUERY_WINDOW,
) -> VerificationAgent:
    """Build the agent once, outside any workflow (TemporalDurability requires it).

    The agent gets only the tools of its manifest's profile. `capabilities` are attached when
    the agent is built, the only time Pydantic AI binds them; a workflow passes
    TemporalDurability here. The output holds no query of its own, so no AQL rules are needed:
    the gateway guards the queries the model runs. Raises ValueError when the manifest does not
    describe a verification agent, its prompt, shared rules or profile is not the one given, or
    the prompt does not take this agent's inputs.

    `console_zone` is the QRadar console's time zone and `max_query_window` the profile's longest
    AQL window: the prompt writes the task's START and STOP in that zone, widened by at most an
    hour on each side and never further apart than that window allows (decision T-53).
    """
    profile = check_agent_config(SPEC, manifest, prompt, profiles)
    agent = create_agent(
        SPEC,
        manifest=manifest,
        model=model,
        toolsets=[build_gateway_toolset(profile, gateway, agent_id=manifest.id)],
        aql=None,
        capabilities=capabilities,
    )
    agent.output_validator(check_disagreements)
    return VerificationAgent(
        manifest=manifest,
        prompt=prompt,
        profile=profile,
        agent=agent,
        console_zone=console_zone,
        max_query_window=max_query_window,
    )


def _quoted(text: str) -> str:
    """`text` in double quotes, tag-neutralized and cut short, for a rejection message."""
    quoted = " ".join(neutralize_tags(text).split())
    if len(quoted) > MAX_QUOTED_TEXT:
        quoted = f"{quoted[: MAX_QUOTED_TEXT - 1]}…"
    return f'"{quoted}"'
