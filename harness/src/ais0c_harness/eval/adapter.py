"""What the runner needs from an agent: one attempt at a scenario and its evaluation.

Each agent the harness can measure has an adapter (T-030: Triage; T-052 and T-053 add the
others). An adapter builds the agent as the worker does (config.py), answers its tool calls from
the scenario (fixture_gateway.py) and runs it once with `run_agent`, without Temporal: the
budgets and the final-answer rule (decision T-52) are the runner's own. The run is limited to
the manifest's wall clock budget.

RecordingModel wraps the model of each attempt. It keeps the messages the run had sent and
received when its last request started, so a run that timed out still leaves its messages, and
the exception that ended a model request, so the runner can tell an infrastructure failure from
the model's own: HTTP 429 or 5xx from LiteLLM, or a request that got no HTTP answer at all
(Pydantic AI's ModelAPIError: no connection, a timeout, a garbled response).
"""

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Final

from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from ais0c_agents import AgentRun
from ais0c_contracts import AgentResult, RunStatus
from ais0c_harness.eval.config import AgentConfig
from ais0c_harness.eval.evaluate import Evaluation
from ais0c_harness.eval.fixture_gateway import GatewayExchange
from ais0c_harness.eval.scenario import ScenarioBase

TOO_MANY_REQUESTS: Final = 429
SERVER_ERROR: Final = 500


@dataclass(frozen=True, kw_only=True)
class Attempt:
    """One run of the agent on a scenario."""

    run_id: str
    started_at: datetime
    ended_at: datetime
    status: RunStatus | None
    """The agent run's status; None when it ran out of wall clock time."""
    result: AgentResult | None
    error: str | None
    infra_error: str | None
    """Set when the run ended on an infrastructure failure (timeout included): retried once."""
    messages: list[ModelMessage]
    exchanges: list[GatewayExchange]
    tokens: int
    seconds: float


class RecordingModel(WrapperModel):
    """Keeps a run's messages and the failure of its last model request."""

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        self.history: list[ModelMessage] = []
        self.failure: Exception | None = None

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.history = list(messages)
        try:
            response = await super().request(messages, model_settings, model_request_parameters)
        except Exception as error:
            self.failure = error
            raise
        self.history = [*self.history, response]
        return response

    @property
    def tokens(self) -> int:
        return sum(
            message.usage.total_tokens
            for message in self.history
            if isinstance(message, ModelResponse)
        )


def infra_failure(error: Exception | None) -> str | None:
    """Why a model request failed when the infrastructure failed it, else None."""
    if isinstance(error, ModelHTTPError):
        code = error.status_code
        return f"HTTP {code}" if code == TOO_MANY_REQUESTS or code >= SERVER_ERROR else None
    if isinstance(error, ModelAPIError):
        return f"no HTTP answer: {error.message}"[:300]
    return None


class AgentAdapter(ABC):
    """One agent under test: builds it, runs it on a scenario and evaluates the run."""

    agent_id: ClassVar[str]
    manifest_path: ClassVar[str]
    """The agent's manifest under the repository root, e.g. config/agents/triage.yaml."""
    scenario_type: ClassVar[type[ScenarioBase]]

    def __init__(self, config: AgentConfig) -> None:
        if config.manifest.id != self.agent_id:
            raise ValueError(f"the manifest is for {config.manifest.id}, not {self.agent_id}")
        self.config = config

    @property
    def wall_clock_seconds(self) -> float:
        return float(self.config.manifest.budgets.wall_clock_seconds)

    @abstractmethod
    async def attempt(
        self, scenario: ScenarioBase, *, run_id: str, model: Model, time_limit: float
    ) -> Attempt:
        """Run the agent once on `scenario` as the run `run_id`."""

    @abstractmethod
    def evaluate(self, scenario: ScenarioBase, attempt: Attempt) -> Evaluation:
        """The deterministic checks and metrics of `attempt`."""

    @abstractmethod
    def describe(self, result: AgentResult) -> dict[str, str]:
        """The result's values the report counts per scenario, e.g. {"verdict": "tp"}."""


async def timed_run[ResultT: AgentResult](
    run: Callable[[], Awaitable[AgentRun[ResultT]]],
    *,
    run_id: str,
    recorder: RecordingModel,
    exchanges: list[GatewayExchange],
    time_limit: float,
) -> Attempt:
    """Await `run()` within `time_limit` seconds and turn its outcome into an Attempt."""
    started_at = datetime.now(UTC)
    started = time.monotonic()
    deadline = asyncio.timeout(time_limit)
    try:
        async with deadline:
            agent_run = await run()
    except TimeoutError:
        if not deadline.expired():
            raise
        return Attempt(
            run_id=run_id,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            status=None,
            result=None,
            error=f"timeout: the run took more than {time_limit:g} s",
            infra_error="timeout",
            messages=list(recorder.history),
            exchanges=list(exchanges),
            tokens=recorder.tokens,
            seconds=time.monotonic() - started,
        )
    infra = infra_failure(recorder.failure) if agent_run.status is RunStatus.FAILED else None
    return Attempt(
        run_id=run_id,
        started_at=started_at,
        ended_at=datetime.now(UTC),
        status=agent_run.status,
        result=agent_run.result,
        error=agent_run.error,
        infra_error=infra,
        messages=list(agent_run.messages),
        exchanges=list(exchanges),
        tokens=agent_run.usage.tokens,
        seconds=agent_run.usage.seconds,
    )
