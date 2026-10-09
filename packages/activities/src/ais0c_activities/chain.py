"""Activities of an evaluation's agent chain (T-026): what CaseWorkflow plans with, and the run
records of AgentWorkflow.

CaseWorkflow, after Triage:

- `evaluation_window`: the evaluation's window, by Triage's rule (`triage.evaluation_window`);
- `candidate_skills`: the router's candidates of each plan agent's role, with each skill's
  budget (architecture §7, "Seçim");
- `plan_budgets`: the plan budget and the plan agents' manifest budgets (T-41);
- `record_plan`: why the Orchestrator's plan was replaced or a step dropped, in the audit log
  under the Orchestrator's run (T-41: "Red nedeni Orchestrator çalışmasının kaydında kalır").

AgentWorkflow, for each chain agent run:

- `begin_agent_run` records the run in `agent_runs` before the agent starts, as
  `begin_triage_run` does: its AgentTask, the release of its model (T-24) and the skill it uses
  (T-21). A plan step's skill is checked first (`skills.check_skill`); one that fails is left
  out, the step runs without it and the reason goes to the audit log under the run. A skill
  reaches only an agent whose prompt has a Skill section (Investigation, T-49).
- `load_evidence` reads the EvidenceRefs the run's task cites.
- `finish_agent_run` records how the run ended.

The audit entries are the system's (`actor_kind` system, `actor_id` the case workflow).
"""

import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Self

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ais0c_activities.agent_runtimes import ChainAgent, ChainRuntime
from ais0c_activities.db import SessionFactory
from ais0c_activities.gateway import utc_now
from ais0c_activities.names import (
    BEGIN_AGENT_RUN,
    CANDIDATE_SKILLS,
    EVALUATION_WINDOW,
    FINISH_AGENT_RUN,
    LOAD_EVIDENCE,
    PLAN_BUDGETS,
    RECORD_PLAN,
    SKILL_TELEMETRY,
)
from ais0c_activities.settings import CaseSettings
from ais0c_activities.skills import candidates, check_skill
from ais0c_activities.triage import evaluation_window
from ais0c_agents import AgentManifest, SkillTelemetrySource
from ais0c_contracts import (
    AgentTask,
    Budget,
    CasePlan,
    CaseReport,
    EnrichmentContext,
    EvidenceRef,
    InvestigationResult,
    OffenseSnapshot,
    RunStatus,
    SkillRef,
    TimeWindow,
    Usage,
    VerificationResult,
)
from ais0c_knowledge.skills import Mode, SkillRegistry, scan_text
from ais0c_policy import new_nonce
from ais0c_storage import ActorKind
from ais0c_storage.repositories import (
    append_audit,
    finish_agent_run,
    get_agent_run,
    get_evidence_refs,
    list_catalog_log_sources,
    start_agent_run,
)

# The audit log's actor and actions of the chain.
CHAIN_ACTOR = "case-workflow"
PLAN_REPLACED = "case.plan.replaced"
PLAN_STEPS_DROPPED = "case.plan.steps_dropped"
SKILL_REJECTED = "case.skill.rejected"
AGENT_RUN_OBJECT = "agent_run"
SAFE_TYPE_NAME = re.compile(r"^[A-Za-z0-9 ._()/-]{1,255}$")
# The agents a plan step can run, whose manifest budgets the plan is cut to (T-41).
PLAN_AGENTS = ("investigation", "verification")

type ChainResult = CasePlan | InvestigationResult | VerificationResult | CaseReport


def manifest_budget(manifest: AgentManifest) -> Budget:
    budgets = manifest.budgets
    return Budget(
        tokens=budgets.tokens, tool_calls=budgets.tool_calls, seconds=budgets.wall_clock_seconds
    )


class ChainActivities:
    def __init__(
        self,
        *,
        sessions: SessionFactory,
        agents: Mapping[str, ChainAgent],
        skills: SkillRegistry,
        skills_mode: Mode,
        settings: CaseSettings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """`agents` are the chain agents by ID (`ChainRuntime.agents()`); `skills` the
        registry the worker loaded in `skills_mode`."""
        self._sessions = sessions
        self._agents = agents
        self._skills = skills
        self._skills_mode: Mode = skills_mode
        self._settings = settings
        self._clock = clock

    @classmethod
    def of(cls, runtime: ChainRuntime, *, sessions: SessionFactory, settings: CaseSettings) -> Self:
        """The activities of `runtime`'s agents and skills."""
        return cls(
            sessions=sessions,
            agents=runtime.agents(),
            skills=runtime.skills,
            skills_mode=runtime.skills_mode,
            settings=settings,
        )

    def activities(self) -> list[Callable[..., object]]:
        return [
            self.evaluation_window,
            self.candidate_skills,
            self.skill_telemetry,
            self.plan_budgets,
            self.record_plan,
            self.begin_agent_run,
            self.load_evidence,
            self.finish_agent_run,
        ]

    # --- CaseWorkflow ---------------------------------------------------------------------

    @activity.defn(name=EVALUATION_WINDOW)
    async def evaluation_window(self, offense: OffenseSnapshot) -> TimeWindow:
        """The evaluation's window now: the plan's steps lie inside it (T-41)."""
        return evaluation_window(offense, self._clock())

    @activity.defn(name=CANDIDATE_SKILLS)
    async def candidate_skills(
        self, offense: OffenseSnapshot, enrichment: EnrichmentContext
    ) -> list[tuple[str, SkillRef, Budget]]:
        """The router's candidates of each plan agent: (agent, skill, the skill's budget)."""
        return candidates(
            self._skills, offense, enrichment, now=self._clock(), mode=self._skills_mode
        )

    @activity.defn(name=SKILL_TELEMETRY)
    async def skill_telemetry(self, skill_id: str, version: str) -> list[SkillTelemetrySource]:
        """The installation's sources for each telemetry class the loaded skill asks for."""
        skill = self._skills.get(skill_id, version)
        if skill is None:
            return []
        resolved: list[SkillTelemetrySource] = []
        classes = dict.fromkeys(item.telemetry_class for item in skill.manifest.required_telemetry)
        async with self._sessions() as session:
            for telemetry_class in classes:
                rows = await list_catalog_log_sources(session, telemetry_class=telemetry_class)
                by_type: dict[str, list[int]] = {}
                for row in rows:
                    by_type.setdefault(row.type_name, []).append(row.log_source_id)
                for type_name, log_source_ids in sorted(by_type.items()):
                    resolved.append(
                        SkillTelemetrySource(
                            telemetry_class=telemetry_class.value,
                            type_name=(
                                type_name
                                if SAFE_TYPE_NAME.fullmatch(type_name) and not scan_text(type_name)
                                else None
                            ),
                            log_source_ids=tuple(sorted(log_source_ids)[:20]),
                            total=len(log_source_ids),
                        )
                    )
        return resolved

    @activity.defn(name=PLAN_BUDGETS)
    async def plan_budgets(self) -> tuple[Budget, dict[str, Budget]]:
        """The plan budget and the plan agents' manifest budgets, by agent ID."""
        return self._settings.plan_budget, {
            agent: manifest_budget(self._agents[agent].manifest) for agent in PLAN_AGENTS
        }

    @activity.defn(name=RECORD_PLAN)
    async def record_plan(
        self,
        case_id: str,
        run_id: str,
        reason: str | None,
        step: int | None,
        detail: str | None,
        dropped: list[str],
        steps: list[str],
    ) -> None:
        """Record why the Orchestrator's plan was replaced by the default plan (`reason`) or
        lost steps to the plan budget (`dropped`), under the Orchestrator's run."""
        details: dict[str, JsonValue] = {
            "case_id": case_id,
            "reason": reason,
            "step": step,
            "detail": detail,
            "dropped": list(dropped),
            "steps": list(steps),
        }
        activity.logger.warning("plan of %s: %s", run_id, details)
        async with self._sessions.begin() as session:
            await append_audit(
                session,
                actor_kind=ActorKind.SYSTEM,
                actor_id=CHAIN_ACTOR,
                action=PLAN_STEPS_DROPPED if reason is None else PLAN_REPLACED,
                object_type=AGENT_RUN_OBJECT,
                object_id=run_id,
                details=details,
            )

    # --- AgentWorkflow --------------------------------------------------------------------

    @activity.defn(name=BEGIN_AGENT_RUN)
    async def begin_agent_run(
        self,
        run_id: str,
        agent_id: str,
        case_id: str,
        parent_run_id: str,
        objective: str,
        time_window: TimeWindow,
        budget: Budget | None,
        context_refs: list[str],
        skill: SkillRef | None,
    ) -> tuple[AgentTask, str, SkillRef | None]:
        """Record the run as started; returns its AgentTask, a fresh `untrusted_*` nonce and the
        skill it uses (None when there is none or the one given failed its check).

        `budget` is a plan step's; None gives the manifest's. A retry finds the run it recorded
        and returns the same task and skill.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ApplicationError(
                f"no chain agent {agent_id!r} in this worker",
                type="UnknownAgent",
                non_retryable=True,
            )
        manifest = agent.manifest
        async with self._sessions.begin() as session:
            existing = await get_agent_run(session, run_id)
            if existing is not None:
                if existing.status is not None or existing.agent_id != manifest.id:
                    raise ApplicationError(
                        f"agent run {run_id} exists and is not a {agent_id} run in progress",
                        type="AgentRunConflict",
                        non_retryable=True,
                    )
                return existing.task, new_nonce(), existing.skill
            now = self._clock()
            used = await self._skill(session, run_id, case_id, agent, skill, now=now)
            task = AgentTask(
                task_id=run_id,
                parent_run_id=parent_run_id,
                case_id=case_id,
                agent_id=manifest.id,
                agent_version=manifest.version,
                objective=objective,
                context_refs=context_refs,
                time_window=time_window,
                budget=manifest_budget(manifest) if budget is None else budget,
            )
            await start_agent_run(
                session,
                run_id=run_id,
                task=task,
                prompt_version=agent.prompt_version,
                model_alias=manifest.model_alias,
                model_target=agent.model_release.target,
                toolset_profile=agent.toolset_profile,
                started_at=now,
                model_release=agent.model_release,
                skill=used,
            )
        return task, new_nonce(), used

    async def _skill(
        self,
        session: AsyncSession,
        run_id: str,
        case_id: str,
        agent: ChainAgent,
        skill: SkillRef | None,
        *,
        now: datetime,
    ) -> SkillRef | None:
        """The skill the run uses: `skill` if it passes its check and the agent takes one."""
        if skill is None:
            return None
        if not agent.takes_skill:
            reason: str | None = f"the {agent.manifest.id} agent takes no skill"
        else:
            reason = check_skill(
                self._skills, skill, agent_role=agent.manifest.id, now=now, mode=self._skills_mode
            )
        if reason is None:
            return skill
        activity.logger.warning("run %s goes without its skill: %s", run_id, reason)
        await append_audit(
            session,
            actor_kind=ActorKind.SYSTEM,
            actor_id=CHAIN_ACTOR,
            action=SKILL_REJECTED,
            object_type=AGENT_RUN_OBJECT,
            object_id=run_id,
            details={
                "case_id": case_id,
                "skill_id": skill.skill_id,
                "version": skill.version,
                "content_hash": skill.content_hash,
                "reason": reason,
            },
        )
        return None

    @activity.defn(name=LOAD_EVIDENCE)
    async def load_evidence(self, evidence_ids: list[str]) -> list[EvidenceRef]:
        """The stored evidence among `evidence_ids`, in their order; IDs storage does not know
        are left out."""
        async with self._sessions() as session:
            found = await get_evidence_refs(session, evidence_ids)
        return [
            found[evidence_id]
            for evidence_id in dict.fromkeys(evidence_ids)
            if evidence_id in found
        ]

    @activity.defn(name=FINISH_AGENT_RUN)
    async def finish_agent_run(
        self,
        run_id: str,
        status: RunStatus,
        result: ChainResult | None,
        usage: Usage,
        error: str | None,
    ) -> None:
        """Record how the run ended; its result when it completed."""
        if error is not None:
            activity.logger.warning("agent run %s ended %s: %s", run_id, status.value, error)
        async with self._sessions.begin() as session:
            await finish_agent_run(
                session,
                run_id,
                status=status,
                result=result,
                tokens=usage.tokens,
                tool_calls=usage.tool_calls,
                ended_at=self._clock(),
                error=error,
            )
