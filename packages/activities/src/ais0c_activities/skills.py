"""Skills in the case chain (architecture §7, "Skill'ler"; decision T-21; T-026 criterion 5).

The router lists the candidate skills of each plan agent's role (`candidates`); the Orchestrator
chooses from them, and validate_plan keeps only a listed one. Before the step's agent starts,
`check_skill` checks the chosen skill once more: its role, its status (approved in prod), that
it is the latest approved version, its expiry and its content hash. A skill that fails runs
nowhere: the step runs without one, and the reason is recorded.

The agents package may not import the knowledge package (docs/impl/repo-structure.md), so the
loader's Skill becomes the agents' own types here: `skill_input` for Investigation's prompt and
`candidate_skill` for the Orchestrator's.
"""

from datetime import datetime
from typing import Final

from ais0c_agents import Budgets, CandidateSkill, SkillEvidence, SkillInput, SkillTelemetry
from ais0c_contracts import Budget, EnrichmentContext, OffenseSnapshot, SkillRef
from ais0c_knowledge.skills import AgentRole, Mode, Skill, SkillRegistry, candidate_skills

# The agents a plan step can run (decision T-41), as skill roles.
PLAN_AGENT_ROLES: Final[tuple[AgentRole, ...]] = ("investigation", "verification")


def candidates(
    registry: SkillRegistry,
    offense: OffenseSnapshot,
    enrichment: EnrichmentContext,
    *,
    now: datetime,
    mode: Mode,
) -> list[tuple[str, SkillRef, Budget]]:
    """The router's candidates of every plan agent: (agent, skill, the skill's budget)."""
    listed: list[tuple[str, SkillRef, Budget]] = []
    for role in PLAN_AGENT_ROLES:
        for ref in candidate_skills(
            registry, offense, enrichment, agent_role=role, now=now, mode=mode
        ):
            skill = registry.get(ref.skill_id, ref.version)
            if skill is not None:  # always: the router lists skills of this registry
                listed.append((role, ref, skill_budget(skill)))
    return listed


def check_skill(
    registry: SkillRegistry, ref: SkillRef, *, agent_role: str, now: datetime, mode: Mode
) -> str | None:
    """Why the agent in `agent_role` may not use `ref` now; None when it may.

    In mode "dev" a draft passes the status check; the router never lists one, so a draft can
    reach a step only from a plan that names it outside the candidates, which validate_plan
    rejects.
    """
    name = f"skill {ref.skill_id} {ref.version}"
    skill = registry.get(ref.skill_id, ref.version)
    if skill is None:
        return f"{name} is not loaded"
    manifest = skill.manifest
    if agent_role not in manifest.allowed_agent_roles:
        return f"{name} does not allow the {agent_role} role"
    if manifest.status != "approved" and mode == "prod":
        return f"{name} is a {manifest.status}, not approved"
    if manifest.status == "approved":
        latest = {item.manifest.id: item for item in registry.latest_approved()}[manifest.id]
        if latest is not skill:
            return f"{name} is not the latest approved version, {latest.manifest.version}"
    if manifest.is_expired(now):
        return f"{name} expired on {manifest.expires_at.isoformat()}"
    if ref.content_hash != skill.content_hash:
        return f"{name} has the content hash {skill.content_hash}, not {ref.content_hash}"
    return None


def skill_budget(skill: Skill) -> Budget:
    budgets = skill.manifest.budgets
    return Budget(
        tokens=budgets.tokens, tool_calls=budgets.tool_calls, seconds=budgets.wall_clock_seconds
    )


def skill_input(skill: Skill) -> SkillInput:
    """The skill as Investigation's task carries it (PR-T-043)."""
    manifest = skill.manifest
    return SkillInput(
        ref=skill.ref,
        allowed_agent_roles=frozenset(manifest.allowed_agent_roles),
        budgets=_budgets(skill),
        instructions=skill.instructions,
        required_telemetry=tuple(
            SkillTelemetry(
                log_source_type=item.log_source_type, events=item.events, required=item.required
            )
            for item in manifest.required_telemetry
        ),
        required_evidence=tuple(
            SkillEvidence(id=item.id, description=item.description)
            for item in manifest.required_evidence
        ),
    )


def candidate_skill(skill: Skill, *, agent_role: str) -> CandidateSkill:
    """The skill as the Orchestrator's task lists it for `agent_role`."""
    return CandidateSkill(
        ref=skill.ref,
        agent_role=agent_role,
        required_evidence=tuple(
            SkillEvidence(id=item.id, description=item.description)
            for item in skill.manifest.required_evidence
        ),
        budgets=_budgets(skill),
    )


def _budgets(skill: Skill) -> Budgets:
    budgets = skill.manifest.budgets
    return Budgets(
        tokens=budgets.tokens,
        tool_calls=budgets.tool_calls,
        wall_clock_seconds=budgets.wall_clock_seconds,
    )
