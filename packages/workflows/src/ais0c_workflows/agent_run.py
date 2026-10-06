"""One agent run in workflow code, bounded by its wall clock budget (architecture §20; decisions
T-02, D-33 and T-30).

TriageWorkflow and AgentWorkflow run their agent here. The agent's model requests and tool calls
are activities of the calling workflow (Pydantic AI's TemporalDurability). When the wall clock
budget runs out first, the run is abandoned and ends `budget_exhausted`; a model or tool
activity that keeps failing ends it `failed`. RunStatus does not say why a run gave no result,
and a case retries only the runs the model's outage ended (D-33), so the end also carries an
`AgentFailure`.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Final, Never

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from ais0c_contracts import RunStatus, Usage
    from ais0c_workflows.agent_runtime import AgentRunReport

MAX_ERROR_LENGTH: Final = 1000
# Pydantic AI's TemporalDurability names an agent's model request activity
# `agent__<agent>__model_request` (`..._stream` when streamed). Its activity names are persisted
# compatibility data that do not change.
MODEL_REQUEST_ACTIVITIES: Final = ("__model_request", "__model_request_stream")


class AgentFailure(StrEnum):
    """Why an agent run ended without a result."""

    # A model request failed for good: its attempts ran out, it timed out or it was rejected.
    MODEL_ERROR = "model_error"
    # The wall clock budget ran out, for example because the model did not answer.
    TIMEOUT = "timeout"
    # Another activity of the agent failed for good, such as a tool call the gateway did not
    # answer.
    TOOL_ERROR = "tool_error"
    # The agent ended `failed` itself: the model kept breaking the output schema or the tool rules.
    INVALID_OUTPUT = "invalid_output"
    # The agent ended `budget_exhausted` itself: its token, tool call or step budget ran out.
    BUDGET_EXHAUSTED = "budget_exhausted"
    # The agent refused the input the workflow built for it before any model request: a fault
    # of the platform, not of the model.
    INVALID_INPUT = "invalid_input"


# The failures of a model that cannot be reached: a case runs these once more (D-33). A model
# that does not answer ends the run on its wall clock budget before its own request times out
# (T-012 open question 5), so a timeout counts too (T-30).
MODEL_ACCESS_FAILURES: Final = frozenset({AgentFailure.MODEL_ERROR, AgentFailure.TIMEOUT})


@dataclass(frozen=True, kw_only=True)
class RunEnd[ResultT]:
    """How an agent run ended."""

    status: RunStatus
    result: ResultT | None
    """Set only when the status is `completed`."""
    usage: Usage
    error: str | None
    """Why the run did not complete; for logs and traces, never shown to a model."""
    failure: AgentFailure | None
    """Why the run gave no result; None when it completed."""


async def run_within_budget[ResultT](
    start: Callable[[], Awaitable[AgentRunReport[ResultT]]],
    *,
    run_id: str,
    seconds: int,
) -> RunEnd[ResultT]:
    """Run the agent `start` starts, for at most `seconds` of workflow time.

    A run out of time is abandoned: its activities are abandoned on cancellation, so nothing is
    asked of the server, and a request still running finishes on its worker and is ignored.
    Cancelling the calling workflow cancels the run and raises CancelledError.
    """
    started = workflow.time()
    agent_run = asyncio.create_task(_await(start))
    out_of_time = False
    try:
        await workflow.wait_condition(
            agent_run.done,
            timeout=timedelta(seconds=seconds),
            timeout_summary="wall clock budget",
        )
    except TimeoutError:
        out_of_time = True
        agent_run.cancel()
        await workflow.wait_condition(agent_run.done)
    report: AgentRunReport[ResultT] | None = None
    failure: ActivityError | None = None
    refused: ValueError | None = None
    if not agent_run.cancelled():
        try:
            report = agent_run.result()
        except ActivityError as error:
            failure = error
        except ValueError as error:
            refused = error
    if report is not None:
        return RunEnd(
            status=report.status,
            result=report.result,
            usage=report.usage,
            error=report.error,
            failure=_reported_failure(report.status),
        )
    elapsed = workflow.time() - started
    if out_of_time:
        workflow.logger.warning("agent run %s ran out of its wall clock budget", run_id)
        return _without_result(
            RunStatus.BUDGET_EXHAUSTED,
            AgentFailure.TIMEOUT,
            f"the wall clock budget of {seconds} seconds ran out",
            seconds=elapsed,
        )
    if refused is not None:
        workflow.logger.warning("agent run %s refused its input: %s", run_id, refused)
        return _without_result(
            RunStatus.FAILED,
            AgentFailure.INVALID_INPUT,
            f"{type(refused).__name__}: {refused}",
            seconds=elapsed,
        )
    if failure is None:
        # Cancelled from outside this function: the workflow itself is being cancelled.
        raise asyncio.CancelledError
    workflow.logger.warning("agent run %s failed: %s", run_id, _describe(failure))
    return _without_result(
        RunStatus.FAILED, _failed_activity(failure), _describe(failure), seconds=elapsed
    )


async def _await[ResultT](
    start: Callable[[], Awaitable[AgentRunReport[ResultT]]],
) -> AgentRunReport[ResultT]:
    return await start()


def _without_result(
    status: RunStatus, failure: AgentFailure, error: str, *, seconds: float
) -> RunEnd[Never]:
    """The end of a run whose agent did not finish: its token and tool call counts are lost with
    it (the gateway's `tool_calls` rows still show the calls)."""
    return RunEnd(
        status=status,
        result=None,
        usage=Usage(tokens=0, tool_calls=0, seconds=max(0.0, seconds)),
        error=error[:MAX_ERROR_LENGTH],
        failure=failure,
    )


def _reported_failure(status: RunStatus) -> AgentFailure | None:
    """What a run status the agent reported itself means. Its model requests and tool calls are
    activities, so a failure the agent catches is the model's output or tool calls."""
    match status:
        case RunStatus.COMPLETED:
            return None
        case RunStatus.BUDGET_EXHAUSTED:
            return AgentFailure.BUDGET_EXHAUSTED
        case RunStatus.FAILED:
            return AgentFailure.INVALID_OUTPUT


def _failed_activity(error: ActivityError) -> AgentFailure:
    """The model request or the other activity of the agent that failed for good."""
    if (error.activity_type or "").endswith(MODEL_REQUEST_ACTIVITIES):
        return AgentFailure.MODEL_ERROR
    return AgentFailure.TOOL_ERROR


def _describe(error: ActivityError) -> str:
    """The failure of the activity that ended the run, e.g. a model request out of retries.

    An application error already names its type, e.g. `GatewayError: ...`.
    """
    cause = error.cause or error
    text = str(cause) if isinstance(cause, ApplicationError) else f"{type(cause).__name__}: {cause}"
    return text[:MAX_ERROR_LENGTH]
