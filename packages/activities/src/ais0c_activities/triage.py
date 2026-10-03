"""The Triage agent under Temporal (architecture §7, §20; decision T-02).

`TriageRuntime` holds the T-009 Triage agent with Pydantic AI's TemporalDurability. Its `run` is
called from TriageWorkflow's workflow code, where each model request and each tool call becomes
an activity of that workflow; the worker registers those activities with the rest. The run's
ID is the workflow's ID (`<case_id>-triage-<n>`); every tool call carries it in its ToolIntent,
so the gateway records the call under the run (T-19).

`TriageRunActivities` record the run in `agent_runs`: `begin_triage_run` before the agent
starts, because the gateway takes calls only for a recorded run in progress, and
`finish_triage_run` when it has ended.

Activity settings of the agent's own activities:

- model requests: 3 minutes per attempt and 4 attempts with backoff, so a short LiteLLM or
  model outage is ridden out within the run's wall clock budget;
- tool calls: 200 seconds per attempt, above the gateway client's 180-second wait, and 3
  attempts. The gateway itself never retries an MCP call.

Both heartbeat (Pydantic AI beats in the background) with a 30-second timeout, so the attempt of
a worker that died is retried on another within the run's budget instead of after its timeout.

Both are abandoned, not cancelled, when the run stops early, so no cancel request can cross an
activity's completion (see CaseWorkflow).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Self

from pydantic_ai.durable_exec.temporal import TemporalDurability
from pydantic_ai.models import Model
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.workflow import ActivityCancellationType, ActivityConfig

from ais0c_activities.db import SessionFactory
from ais0c_activities.gateway import utc_now
from ais0c_activities.names import BEGIN_TRIAGE_RUN, FINISH_TRIAGE_RUN
from ais0c_agents import (
    AgentManifest,
    AgentRun,
    GatewayClient,
    PromptTemplate,
    RunDeps,
    ToolsetProfile,
    TriageAgent,
    TriageTask,
    build_triage_agent,
)
from ais0c_contracts import (
    AgentTask,
    Budget,
    EnrichmentContext,
    OffenseSnapshot,
    RunStatus,
    TimeWindow,
    TriageResult,
    Usage,
)
from ais0c_policy import new_nonce
from ais0c_storage.repositories import finish_agent_run, get_agent_run, start_agent_run

MODEL_ACTIVITY: Final = ActivityConfig(
    start_to_close_timeout=timedelta(minutes=3),
    heartbeat_timeout=timedelta(seconds=30),
    retry_policy=RetryPolicy(
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=1),
        maximum_attempts=4,
    ),
    cancellation_type=ActivityCancellationType.ABANDON,
)
TOOL_ACTIVITY: Final = ActivityConfig(
    start_to_close_timeout=timedelta(seconds=200),
    heartbeat_timeout=timedelta(seconds=30),
    retry_policy=RetryPolicy(
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=3,
    ),
    cancellation_type=ActivityCancellationType.ABANDON,
)
# The time window of a Triage run's tool calls: the offense's life, at most this long (the
# Triage profile allows 31 days).
TRIAGE_WINDOW: Final = timedelta(days=30)
MIN_WINDOW: Final = timedelta(minutes=1)


@dataclass(frozen=True)
class TriageRuntime:
    agent: TriageAgent
    model_target: str
    """What the model alias points to, from the model registry; recorded with each run."""
    temporal_activities: tuple[Callable[..., object], ...]
    """The agent's model and tool activities, as TemporalDurability registered them."""

    @classmethod
    def build(
        cls,
        *,
        manifest: AgentManifest,
        prompt: PromptTemplate,
        profile: ToolsetProfile,
        gateway: GatewayClient,
        model: Model,
        model_target: str,
    ) -> Self:
        """Build the durable agent; outside any workflow, before the worker starts."""
        agent = build_triage_agent(
            manifest=manifest,
            prompt=prompt,
            profiles={profile.name: profile},
            gateway=gateway,
            model=model,
            capabilities=[
                TemporalDurability[RunDeps](
                    activity_config=TOOL_ACTIVITY, model_activity_config=MODEL_ACTIVITY
                )
            ],
        )
        durability = TemporalDurability.from_agent(agent.agent)
        if durability is None:  # pragma: no cover - build_triage_agent attached it
            raise RuntimeError("the Triage agent has no TemporalDurability capability")
        return cls(
            agent=agent,
            model_target=model_target,
            temporal_activities=tuple(durability.temporal_activities),
        )

    async def run(
        self,
        task: AgentTask,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
        *,
        nonce: str,
    ) -> AgentRun[TriageResult]:
        """One Triage run, in TriageWorkflow's workflow code (`TriageAgentRun`).

        The run's ID is the workflow's ID, under which `begin_triage_run` recorded the run; the
        agent writes it into every ToolIntent. Durations come from the workflow clock, so a
        replay measures what the run measured.
        """
        return await self.agent.run(
            TriageTask(task=task, offense=offense, enrichment=enrichment),
            run_id=workflow.info().workflow_id,
            nonce=nonce,
            clock=workflow.time,
        )

    def task(
        self,
        *,
        run_id: str,
        case_id: str,
        evaluation_no: int,
        parent_run_id: str,
        offense: OffenseSnapshot,
        now: datetime,
    ) -> AgentTask:
        """The AgentTask of a case evaluation's Triage run; its budget is the manifest's."""
        manifest = self.agent.manifest
        start = min(max(offense.start_time, now - TRIAGE_WINDOW), now - MIN_WINDOW)
        return AgentTask(
            task_id=run_id,
            parent_run_id=parent_run_id,
            case_id=case_id,
            agent_id=manifest.id,
            agent_version=manifest.version,
            objective=f"Triage QRadar offense {offense.offense_id} (evaluation {evaluation_no}).",
            context_refs=[],
            time_window=TimeWindow(start=start, end=now),
            budget=Budget(
                tokens=manifest.budgets.tokens,
                tool_calls=manifest.budgets.tool_calls,
                seconds=manifest.budgets.wall_clock_seconds,
            ),
        )


class TriageRunActivities:
    def __init__(
        self,
        *,
        sessions: SessionFactory,
        runtime: TriageRuntime,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._runtime = runtime
        self._clock = clock

    def activities(self) -> list[Callable[..., object]]:
        return [self.begin_triage_run, self.finish_triage_run]

    @activity.defn(name=BEGIN_TRIAGE_RUN)
    async def begin_triage_run(
        self,
        run_id: str,
        case_id: str,
        evaluation_no: int,
        parent_run_id: str,
        offense: OffenseSnapshot,
    ) -> tuple[AgentTask, str]:
        """Record the run as started; returns its AgentTask and a fresh `untrusted_*` nonce.

        A retry finds the run it recorded and returns the same task.
        """
        manifest = self._runtime.agent.manifest
        async with self._sessions.begin() as session:
            existing = await get_agent_run(session, run_id)
            if existing is not None:
                if existing.status is not None or existing.agent_id != manifest.id:
                    raise ApplicationError(
                        f"agent run {run_id} exists and is not a Triage run in progress",
                        type="AgentRunConflict",
                        non_retryable=True,
                    )
                return existing.task, new_nonce()
            now = self._clock()
            task = self._runtime.task(
                run_id=run_id,
                case_id=case_id,
                evaluation_no=evaluation_no,
                parent_run_id=parent_run_id,
                offense=offense,
                now=now,
            )
            await start_agent_run(
                session,
                run_id=run_id,
                task=task,
                prompt_version=self._runtime.agent.prompt.version,
                model_alias=manifest.model_alias,
                model_target=self._runtime.model_target,
                toolset_profile=self._runtime.agent.profile.name,
                started_at=now,
            )
        return task, new_nonce()

    @activity.defn(name=FINISH_TRIAGE_RUN)
    async def finish_triage_run(
        self,
        run_id: str,
        status: RunStatus,
        result: TriageResult | None,
        usage: Usage,
        error: str | None,
    ) -> None:
        """Record how the run ended; its TriageResult when it completed."""
        if error is not None:
            activity.logger.warning("triage run %s ended %s: %s", run_id, status.value, error)
        async with self._sessions.begin() as session:
            await finish_agent_run(
                session,
                run_id,
                status=status,
                result=result,
                tokens=usage.tokens,
                tool_calls=usage.tool_calls,
                ended_at=self._clock(),
            )
