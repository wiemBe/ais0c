"""Running an agent: budgets, the run status and what a run leaves behind.

Budgets are Pydantic AI usage limits: `max_steps` caps model requests, the token and tool call
budgets cap usage, and the smaller of the manifest's and the task's budget applies. Exceeding
one ends the run as `budget_exhausted`. A model that keeps breaking the output schema or the
tool rules, and a gateway that cannot answer, end it as `failed`. Neither leaves a result.
An agent without tools runs the same way, with a tool call budget of 0.

Pydantic AI checks the token budget after a response arrives, so a final answer that crossed
it would be lost. Every run therefore carries FinalAnswer (decision T-52): before the budget
runs out it withdraws the agent's function tools, so the next request can only return the
result, and a run that ends this way completes with a `budget_exhausted` data gap. A model
that calls a tool anyway runs into the tool call or token budget and ends `budget_exhausted`
as before.

What the tools go is decided from what the next request costs at least: the whole conversation
goes again, plus the tool result that came since. TOKEN_RESERVE_FACTOR holds two of those, for
the tool call and the answer after it, because the answer must still fit when the limit is
reached. A tool result bigger than the last request therefore has to end the run's tool calls
even when the last request's own tokens leave room (decision T-52, T-051).

The wall-clock budget is not enforced here: the Temporal activity and workflow timeouts own it
(T-012, agent-harness.md §2).
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from pydantic_ai import Agent, RunContext, capture_run_messages
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import AgentRunError, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage, UsageLimits

from ais0c_agents.gateway import GatewayError
from ais0c_agents.manifest import AgentManifest
from ais0c_agents.prompts import PromptTemplate
from ais0c_agents.toolset import RunDeps
from ais0c_contracts import AgentResult, Budget, DataGap, DataGapReason, RunStatus, Usage

MAX_ERROR_LENGTH: Final = 1000
FINAL_ANSWER_PROMPT: Final = (
    "Your budget is used up and your tools are withdrawn: return your result now, from what "
    "you already have."
)
"""What every request without the agent's tools tells the model (decision T-52)."""
TOKEN_RESERVE_FACTOR: Final = 2
"""The next request must fit twice over: the tool call, and the answer after it."""
TOOL_RESULT_CHARS_PER_TOKEN: Final = 4
"""Characters per token when a tool result's size in tokens is needed (no tokenizer is here)."""
UNNAMED_AGENT: Final = "agent"
"""The data gap's source for an agent without a name; create_agent names every agent."""


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


def budget_spent(
    usage: RunUsage, limits: UsageLimits | None, messages: Sequence[ModelMessage]
) -> bool:
    """Whether the run must answer now instead of calling another tool (decision T-52).

    True when only one model request remains, the tool call budget is used up, or when fewer
    tokens remain than TOKEN_RESERVE_FACTOR times what the next request costs at least (T-051):
    it sends the whole conversation again, so that is the last request's total tokens plus the
    tool results the model has read since. Counting the tool result matters because a result
    bigger than the last request makes the next request cost more than twice the last one,
    which the reserve then no longer covers; the response that crosses the limit loses its
    answer, since Pydantic AI checks the limit after the response.
    """
    if limits is None:
        return False
    if limits.request_limit is not None and limits.request_limit - usage.requests <= 1:
        return True
    if limits.tool_calls_limit is not None and usage.tool_calls >= limits.tool_calls_limit:
        return True
    if limits.total_tokens_limit is None:
        return False
    spent = limits.total_tokens_limit - usage.total_tokens
    return spent < TOKEN_RESERVE_FACTOR * _next_request_tokens(messages)


def _next_request_tokens(messages: Sequence[ModelMessage]) -> int:
    """What the next model request costs at least, in tokens.

    It carries the whole conversation again, so at least the total tokens of the last response
    plus everything that came after it: the tool results that followed the last response. Those
    are measured in characters, since the tokenizer of the model behind the alias is not here.
    Zero when the run has no response yet, which leaves only the other two conditions.
    """
    responses = [message for message in messages if isinstance(message, ModelResponse)]
    if not responses:
        return 0
    last = responses[-1]
    characters = sum(
        len(part.model_response_str())
        for message in messages[messages.index(last) + 1 :]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )
    return last.usage.total_tokens + characters // TOOL_RESULT_CHARS_PER_TOKEN


@dataclass
class FinalAnswer(AbstractCapability[RunDeps]):
    """Withdraws an agent's function tools once its budget is spent (decision T-52).

    From the first request at which budget_spent holds, every request offers only the output
    tool and carries FINAL_ANSWER_PROMPT; the tools do not come back in that run. An agent
    without function tools is left alone. The capability is stateless: a retry sees the prompt
    marker in the run's history, so a replay and another run cannot share withdrawal state.
    """

    enabled: bool
    """Whether the agent has function tools to withdraw."""

    async def prepare_tools(
        self, ctx: RunContext[RunDeps], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        if not self.enabled or not tool_defs:
            return tool_defs
        return [] if self._withdraw_now(ctx) else tool_defs

    async def before_model_request(
        self, ctx: RunContext[RunDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        request = request_context.messages[-1] if request_context.messages else None
        if self.enabled and self._withdraw_now(ctx) and isinstance(request, ModelRequest):
            # The request this step made: the sentence stays in the run's history.
            request.parts = [*request.parts, UserPromptPart(FINAL_ANSWER_PROMPT)]
        return request_context

    def _withdraw_now(self, ctx: RunContext[RunDeps]) -> bool:
        return _tools_were_withdrawn(ctx.messages) or budget_spent(
            ctx.usage, ctx.usage_limits, ctx.messages
        )


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

    `finalize` turns the model's validated output into the agent's result. When FinalAnswer
    withdrew the tools and the model still answered, an AgentResult gets one more data gap:
    its source is the agent (its name, which create_agent sets to the manifest ID), its period
    the task's window and its reason `budget_exhausted`; the model's own data gaps stay.
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
    finalized = None if output is None else finalize(output, run_usage)
    if _tools_were_withdrawn(messages) and isinstance(finalized, AgentResult):
        gap = DataGap(
            source=agent.name or UNNAMED_AGENT,
            period_start=deps.time_window.start,
            period_end=deps.time_window.end,
            reason=DataGapReason.BUDGET_EXHAUSTED,
        )
        finalized = finalized.model_copy(update={"data_gaps": [*finalized.data_gaps, gap]})
    return AgentRun(
        status=status,
        result=finalized,
        usage=run_usage,
        prompt_version=prompt.version,
        prompt_hash=prompt.sha256,
        error=error,
        messages=list(messages),
    )


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]


def _tools_were_withdrawn(messages: Sequence[ModelMessage]) -> bool:
    """Whether FinalAnswer marked any request in this run."""
    return any(
        isinstance(message, ModelRequest)
        and any(
            isinstance(part, UserPromptPart) and part.content == FINAL_ANSWER_PROMPT
            for part in message.parts
        )
        for message in messages
    )
