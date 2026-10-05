"""Running an agent: budgets, the run status and what a run leaves behind.

Budgets are Pydantic AI usage limits: `max_steps` caps model requests, the token and tool call
budgets cap usage, and the smaller of the manifest's and the task's budget applies. Exceeding
one ends the run as `budget_exhausted`. A model that keeps breaking the output schema or the
tool rules, and a gateway that cannot answer, end it as `failed`. Neither leaves a result.

The wall-clock budget is not enforced here: the Temporal activity and workflow timeouts own it
(T-012, agent-harness.md §2).
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, Self

from pydantic_ai import Agent, ModelRetry, RunContext, capture_run_messages
from pydantic_ai.exceptions import AgentRunError, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import RunUsage, UsageLimits

from ais0c_agents.gateway import GatewayError
from ais0c_agents.manifest import AgentManifest
from ais0c_agents.prompts import PromptTemplate
from ais0c_agents.toolset import RunDeps, evidence_aliases
from ais0c_contracts import Budget, Claim, RunStatus, Usage
from ais0c_policy import neutralize_tags

MAX_ERROR_LENGTH: Final = 1000
# How much of the evidence IDs a rejected output cited goes back to the model.
MAX_SHOWN_CITATIONS: Final = 5
MAX_SHOWN_CITATION_LENGTH: Final = 80


@dataclass(frozen=True, kw_only=True)
class AgentRun[ResultT]:
    status: RunStatus
    result: ResultT | None
    """Set only when the status is `completed`."""
    usage: Usage
    prompt_version: str
    prompt_hash: str
    error: str | None
    """Why the run did not complete; for traces, never shown to a model."""
    messages: list[ModelMessage]


class _HasClaims(Protocol):
    @property
    def claims(self) -> Sequence[Claim]: ...

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self: ...


def usage_limits(manifest: AgentManifest, budget: Budget) -> UsageLimits:
    return UsageLimits(
        request_limit=manifest.max_steps,
        tool_calls_limit=min(manifest.budgets.tool_calls, budget.tool_calls),
        total_tokens_limit=min(manifest.budgets.tokens, budget.tokens),
    )


def prompt_tool_budget(manifest: AgentManifest, budget: Budget) -> int:
    """The tool call budget the prompt states.

    One model request is left for the final answer, and with sequential tool calls each other
    request makes at most one call, so the step limit can bind before the tool call limit.
    """
    return max(0, min(manifest.budgets.tool_calls, budget.tool_calls, manifest.max_steps - 1))


def check_cited_evidence[OutputT: _HasClaims](ctx: RunContext[RunDeps], output: OutputT) -> OutputT:
    """Output validator: every claim cites only evidence the gateway returned in this run.

    The model cites the evidence aliases it saw on its tool results (T-27); the validated
    output carries the gateway's evidence IDs instead. A rejected output goes back to the
    model for another try (Pydantic AI output retries).
    """
    aliases = evidence_aliases(ctx.messages)
    cited = {alias for claim in output.claims for alias in claim.evidence_ids}
    if unknown := sorted(cited - aliases.keys()):
        raise ModelRetry(_rejection(unknown, aliases))
    claims = [
        Claim(text=claim.text, evidence_ids=[aliases[alias] for alias in claim.evidence_ids])
        for claim in output.claims
    ]
    return output.model_copy(update={"claims": claims})


def _rejection(unknown: Sequence[str], aliases: Mapping[str, str]) -> str:
    """What the model is told about citations it may not make: its own rejected citations,
    tag-neutralized and cut short, and the aliases it may cite. No gateway evidence ID."""
    shown = ", ".join(cited[:MAX_SHOWN_CITATION_LENGTH] for cited in unknown[:MAX_SHOWN_CITATIONS])
    rejected = (
        "These evidence_ids were not returned by your tool calls in this run: "
        f"{neutralize_tags(shown)}."
    )
    if not aliases:
        return f"{rejected} Your tool calls returned no evidence you can cite: remove the claim."
    citable = ", ".join(sorted(aliases, key=lambda alias: int(alias.removeprefix("ev_"))))
    return f"{rejected} You can cite only {citable}. Cite one of them or remove the claim."


async def run_agent[OutputT, ResultT](
    agent: Agent[RunDeps, OutputT],
    *,
    user_prompt: str,
    instructions: str,
    deps: RunDeps,
    limits: UsageLimits,
    prompt: PromptTemplate,
    finalize: Callable[[OutputT, Usage], ResultT],
    clock: Callable[[], float] = time.monotonic,
) -> AgentRun[ResultT]:
    """Run `agent` once and map the outcome to a RunStatus; never raises for a failed run.

    `finalize` turns the model's validated output into the agent's result.
    """
    usage = RunUsage()
    started = clock()
    output: OutputT | None = None
    status = RunStatus.COMPLETED
    error: str | None = None
    with capture_run_messages() as messages:
        try:
            result = await agent.run(
                user_prompt,
                deps=deps,
                instructions=instructions,
                usage_limits=limits,
                usage=usage,
            )
        except UsageLimitExceeded as exc:
            status, error = RunStatus.BUDGET_EXHAUSTED, _describe(exc)
        except (AgentRunError, GatewayError) as exc:
            status, error = RunStatus.FAILED, _describe(exc)
        else:
            output = result.output
    run_usage = Usage(
        tokens=usage.total_tokens,
        tool_calls=usage.tool_calls,
        seconds=max(0.0, clock() - started),
    )
    return AgentRun(
        status=status,
        result=None if output is None else finalize(output, run_usage),
        usage=run_usage,
        prompt_version=prompt.version,
        prompt_hash=prompt.sha256,
        error=error,
        messages=list(messages),
    )


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]
