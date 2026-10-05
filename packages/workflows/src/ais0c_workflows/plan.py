"""Plan validation (decision T-41, architecture §7 "Orchestrator"): the workflow's check of the
Orchestrator's CasePlan.

The Orchestrator proposes a plan; the workflow runs only what validate_plan returns. The rules:

1. A step's agent is Investigation or Verification. Triage, the Orchestrator and Reporting are
   never steps; any other agent is unknown. Endpoint investigation (Faz 2) is part of
   Investigation (T-25).
2. A step's skill is one of the router's candidates for that step's agent, at the listed
   version.
3. No agent appears twice, and no objective is empty.
4. Verification is the last step: added when the plan has none, moved to the end otherwise.
5. Each step's budget is the smallest of its request, its agent's manifest budget and its
   skill's budget. Verification's budget is set aside from the plan budget first; the rest goes
   to the other steps in order. A step that the remaining plan budget would leave with less
   than a quarter of its manifest budget is dropped. Each step's window is clipped to the
   evaluation's window.
6. A plan that breaks rule 1, 2 or 3, or the CasePlan schema, is rejected. The default plan
   replaces it: Investigation without a skill and then Verification when Triage says the case
   needs investigation, Verification alone otherwise. The reason stays in the Orchestrator
   run's record.

Rules 4 and 5 correct a plan and never reject it. They apply to the default plan as well.
Repairs the rules leave open:

- A non-positive part of a requested budget counts as no request: that part comes from the
  manifest and the skill.
- A window with no time inside the evaluation's window (it lies outside it or ends before it
  starts) becomes the evaluation's window.
- Verification is never dropped: it gets its budget first, capped only by the plan budget.

Pure: the result depends only on the arguments. The module imports only ais0c_contracts and the
standard library; it reads no clock, file or network and draws no random numbers.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ais0c_contracts import Budget, CasePlan, PlanStep, SkillRef, TimeWindow

INVESTIGATION: Final = "investigation"
VERIFICATION: Final = "verification"
# The agents a plan step may run (rule 1).
PLAN_AGENTS: Final = frozenset({INVESTIGATION, VERIFICATION})
# Agents of the case workflow that are never plan steps: Triage runs before the plan, the
# Orchestrator writes it and Reporting runs after every evaluation (decision T-40).
NOT_PLAN_AGENTS: Final = frozenset({"triage", "orchestrator", "reporting"})

# Objectives of the steps the workflow writes itself: the default plan and an added
# Verification step.
DEFAULT_OBJECTIVES: Final[Mapping[str, str]] = {
    INVESTIGATION: "Investigate the offense with the general method and decide its verdict.",
    VERIFICATION: "Check the case's verdict and its claims against the evidence.",
}

# Agent IDs and skill IDs come from a model: a rejection quotes at most this much of one.
_MAX_QUOTE: Final = 64
_MAX_DETAIL: Final = 300


class PlanRejectReason(StrEnum):
    NO_PLAN = "no_plan"
    """The Orchestrator returned no plan: its run failed or ran out of budget."""
    SCHEMA = "schema"
    UNKNOWN_AGENT = "unknown_agent"
    NOT_A_PLAN_AGENT = "not_a_plan_agent"
    """Triage, the Orchestrator or Reporting as a step."""
    REPEATED_AGENT = "repeated_agent"
    EMPTY_OBJECTIVE = "empty_objective"
    SKILL_NOT_A_CANDIDATE = "skill_not_a_candidate"
    SKILL_FOR_ANOTHER_AGENT = "skill_for_another_agent"
    SKILL_VERSION_NOT_LISTED = "skill_version_not_listed"


@dataclass(frozen=True, kw_only=True)
class PlanCandidate:
    """A skill the router lists for one agent: its reference and its manifest budget."""

    agent_id: str
    skill: SkillRef
    budget: Budget


@dataclass(frozen=True, kw_only=True)
class PlanRejection:
    reason: PlanRejectReason
    step: int | None
    """The 1-based number of the step that broke the rule; None for the whole plan."""
    detail: str
    """For the run's record and traces; never shown to a model."""


@dataclass(frozen=True, kw_only=True)
class PlanDecision:
    steps: tuple[PlanStep, ...]
    """The steps to run, in order; Verification is the last one."""
    used_default: bool
    """Whether the default plan replaced the Orchestrator's."""
    rejection: PlanRejection | None
    """Why the Orchestrator's plan was rejected; None when it was accepted."""
    dropped: tuple[str, ...]
    """The agents whose steps rule 5 dropped for lack of plan budget."""


def validate_plan(
    plan: CasePlan | None,
    *,
    agents: Mapping[str, Budget],
    candidates: Iterable[PlanCandidate],
    plan_budget: Budget,
    window: TimeWindow,
    needs_investigation: bool,
) -> PlanDecision:
    """The steps to run for `plan`, the Orchestrator's plan; None when it returned none.

    `agents` holds the manifest budget of each agent the case workflow allows; it must include
    Investigation and Verification. `candidates` is the router's list for each plan agent.
    `plan_budget` caps the plan's steps together, `window` is the evaluation's window and
    `needs_investigation` is Triage's, which picks the default plan.

    Raises ValueError when the arguments themselves are invalid: a plan agent's manifest budget
    is missing or not positive, the plan budget is not positive, or `window` ends before it
    starts. Other agents' budgets are not read: Reporting's has no tool calls, for example.
    """
    _check_arguments(agents, plan_budget, window)
    listed = tuple(candidates)
    if plan is None:
        rejection: PlanRejection | None = PlanRejection(
            reason=PlanRejectReason.NO_PLAN, step=None, detail="the Orchestrator returned no plan"
        )
    else:
        rejection = _rejection(plan, listed)
    if plan is not None and rejection is None:
        steps = _verification_last(plan.steps, agents, window)
    else:
        steps = _default_steps(needs_investigation, agents, window)
    wanted = [_wanted(step, agents, listed, window) for step in steps]
    kept, dropped = _share_budget(wanted, agents, plan_budget)
    return PlanDecision(
        steps=tuple(kept),
        used_default=rejection is not None,
        rejection=rejection,
        dropped=tuple(dropped),
    )


def _check_arguments(agents: Mapping[str, Budget], plan_budget: Budget, window: TimeWindow) -> None:
    if missing := sorted(PLAN_AGENTS - agents.keys()):
        raise ValueError(f"no manifest budget for {', '.join(missing)}")
    plan_agents = [(agent, agents[agent]) for agent in sorted(PLAN_AGENTS)]
    for name, budget in [("the plan budget", plan_budget), *plan_agents]:
        if min(_parts(budget)) < 1:
            raise ValueError(f"{name}: budgets must be positive, got {budget}")
    if window.end < window.start:
        raise ValueError("the evaluation window ends before it starts")


# --- rules 1-3 and the schema (rule 6) ---------------------------------------------------------


def _rejection(plan: CasePlan, candidates: Sequence[PlanCandidate]) -> PlanRejection | None:
    """The first rule `plan` breaks, step by step; None when it breaks none."""
    if (problem := _schema_problem(plan)) is not None:
        return PlanRejection(reason=PlanRejectReason.SCHEMA, step=None, detail=problem)
    seen: set[str] = set()
    for number, step in enumerate(plan.steps, start=1):
        agent = step.agent_id
        if agent in NOT_PLAN_AGENTS:
            return _reject(PlanRejectReason.NOT_A_PLAN_AGENT, number, f"{agent} is not a step")
        if agent not in PLAN_AGENTS:
            return _reject(PlanRejectReason.UNKNOWN_AGENT, number, f"unknown agent {_quote(agent)}")
        if agent in seen:
            return _reject(PlanRejectReason.REPEATED_AGENT, number, f"{agent} appears twice")
        seen.add(agent)
        if not step.objective.strip():
            return _reject(PlanRejectReason.EMPTY_OBJECTIVE, number, "the objective is empty")
        if step.skill_id and (problem := _skill_problem(step, candidates)) is not None:
            return _reject(problem[0], number, problem[1])
    return None


def _schema_problem(plan: CasePlan) -> str | None:
    """Why `plan` does not fit the CasePlan schema, or None.

    A CasePlan from the agent was validated already; one built without validation, such as by
    model_construct, was not.
    """
    try:
        CasePlan.model_validate(plan.model_dump(warnings=False))
    # Pydantic's ValidationError is a ValueError; this module does not import Pydantic.
    except ValueError as error:
        return " ".join(str(error).split())[:_MAX_DETAIL]
    return None


def _skill_problem(
    step: PlanStep, candidates: Sequence[PlanCandidate]
) -> tuple[PlanRejectReason, str] | None:
    same_skill = [c for c in candidates if c.skill.skill_id == step.skill_id]
    skill = _quote(step.skill_id or "")
    if not same_skill:
        return PlanRejectReason.SKILL_NOT_A_CANDIDATE, f"skill {skill} is not a candidate"
    for_agent = [c for c in same_skill if c.agent_id == step.agent_id]
    if not for_agent:
        return (
            PlanRejectReason.SKILL_FOR_ANOTHER_AGENT,
            f"skill {skill} is not a candidate for {step.agent_id}",
        )
    if all(c.skill.version != step.skill_version for c in for_agent):
        listed = ", ".join(sorted(c.skill.version for c in for_agent))
        return (
            PlanRejectReason.SKILL_VERSION_NOT_LISTED,
            f"skill {skill} is listed at version {listed}, not {_quote(step.skill_version or '')}",
        )
    return None


def _reject(reason: PlanRejectReason, step: int, detail: str) -> PlanRejection:
    return PlanRejection(reason=reason, step=step, detail=f"step {step}: {detail}")


def _quote(value: str) -> str:
    return repr(value[:_MAX_QUOTE] + ("..." if len(value) > _MAX_QUOTE else ""))


# --- rule 4 and the default plan ---------------------------------------------------------------


def _verification_last(
    steps: Sequence[PlanStep], agents: Mapping[str, Budget], window: TimeWindow
) -> list[PlanStep]:
    others = [step for step in steps if step.agent_id != VERIFICATION]
    verification = [step for step in steps if step.agent_id == VERIFICATION]
    return [*others, *(verification or [_default_step(VERIFICATION, agents, window)])]


def _default_steps(
    needs_investigation: bool, agents: Mapping[str, Budget], window: TimeWindow
) -> list[PlanStep]:
    plan = [INVESTIGATION, VERIFICATION] if needs_investigation else [VERIFICATION]
    return [_default_step(agent, agents, window) for agent in plan]


def _default_step(agent: str, agents: Mapping[str, Budget], window: TimeWindow) -> PlanStep:
    """A step without a skill that asks for its manifest budget over the whole window."""
    return PlanStep(
        agent_id=agent,
        objective=DEFAULT_OBJECTIVES[agent],
        time_window=window,
        budget=agents[agent],
    )


# --- rule 5 ------------------------------------------------------------------------------------


def _wanted(
    step: PlanStep,
    agents: Mapping[str, Budget],
    candidates: Sequence[PlanCandidate],
    window: TimeWindow,
) -> PlanStep:
    """`step` with its budget cut to its manifest's and its skill's and its window clipped."""
    cap = _smallest(
        [
            agents[step.agent_id],
            *(
                c.budget
                for c in candidates
                if c.agent_id == step.agent_id
                and c.skill.skill_id == step.skill_id
                and c.skill.version == step.skill_version
            ),
        ]
    )
    requested = step.budget
    budget = Budget(
        tokens=_request(requested.tokens, cap.tokens),
        tool_calls=_request(requested.tool_calls, cap.tool_calls),
        seconds=_request(requested.seconds, cap.seconds),
    )
    return step.model_copy(update={"budget": budget, "time_window": _clip(step, window)})


def _request(requested: int, cap: int) -> int:
    return cap if requested < 1 else min(requested, cap)


def _clip(step: PlanStep, window: TimeWindow) -> TimeWindow:
    own = step.time_window
    start, end = max(own.start, window.start), min(own.end, window.end)
    if start > end:
        return window
    if (start, end) == (own.start, own.end):
        return own
    return TimeWindow(start=start, end=end)


def _share_budget(
    steps: Sequence[PlanStep], agents: Mapping[str, Budget], plan_budget: Budget
) -> tuple[list[PlanStep], list[str]]:
    """Verification (the last step) first, then the others in order (rule 5)."""
    *others, verification = steps
    first = _smallest([verification.budget, plan_budget])
    left = _minus(plan_budget, first)
    kept: list[PlanStep] = []
    dropped: list[str] = []
    for step in others:
        granted = _smallest([step.budget, left])
        if _starved(step.budget, granted, agents[step.agent_id]):
            dropped.append(step.agent_id)
            continue
        kept.append(step.model_copy(update={"budget": granted}))
        left = _minus(left, granted)
    return [*kept, verification.model_copy(update={"budget": first})], dropped


def _starved(wanted: Budget, granted: Budget, manifest: Budget) -> bool:
    """Whether the plan budget cut some part of `wanted` below a quarter of `manifest`.

    A step that asked for less than a quarter itself is not starved by getting it.
    """
    return any(
        got < want and 4 * got < limit
        for want, got, limit in zip(_parts(wanted), _parts(granted), _parts(manifest), strict=True)
    )


def _parts(budget: Budget) -> tuple[int, int, int]:
    return budget.tokens, budget.tool_calls, budget.seconds


def _smallest(budgets: Sequence[Budget]) -> Budget:
    return Budget(
        tokens=min(b.tokens for b in budgets),
        tool_calls=min(b.tool_calls for b in budgets),
        seconds=min(b.seconds for b in budgets),
    )


def _minus(budget: Budget, used: Budget) -> Budget:
    return Budget(
        tokens=budget.tokens - used.tokens,
        tool_calls=budget.tool_calls - used.tool_calls,
        seconds=budget.seconds - used.seconds,
    )
