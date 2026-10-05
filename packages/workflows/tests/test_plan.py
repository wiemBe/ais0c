"""Plan validation (T-044 criterion 4, decision T-41): `ais0c_workflows.plan.validate_plan`.

One test per rule of T-41, plus the default plan, determinism and the module's imports. The
budgets are those of the Investigation and Verification manifests and of the plan budget's
defaults; the skill is the repository's windows-dcsync.
"""

import ast
import itertools
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ais0c_contracts import Budget, CasePlan, PlanStep, RunStatus, SkillRef, TimeWindow, Usage
from ais0c_workflows import plan as plan_module
from ais0c_workflows.plan import (
    DEFAULT_OBJECTIVES,
    PlanCandidate,
    PlanDecision,
    PlanRejection,
    PlanRejectReason,
    validate_plan,
)

START = datetime(2026, 10, 2, 13, 0, tzinfo=UTC)
END = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
WINDOW = TimeWindow(start=START, end=END)

INVESTIGATION_BUDGET = Budget(tokens=150000, tool_calls=24, seconds=300)
VERIFICATION_BUDGET = Budget(tokens=80000, tool_calls=12, seconds=180)
AGENTS = {"investigation": INVESTIGATION_BUDGET, "verification": VERIFICATION_BUDGET}
PLAN_BUDGET = Budget(tokens=250000, tool_calls=40, seconds=480)

DCSYNC = SkillRef(skill_id="windows-dcsync", version="1.0.0", content_hash="sha256:" + "a" * 64)
DCSYNC_CHECK = SkillRef(skill_id="dcsync-check", version="1.2.0", content_hash="sha256:" + "b" * 64)
DCSYNC_BUDGET = Budget(tokens=120000, tool_calls=20, seconds=300)
CANDIDATES = (
    PlanCandidate(agent_id="investigation", skill=DCSYNC, budget=DCSYNC_BUDGET),
    PlanCandidate(
        agent_id="verification",
        skill=DCSYNC_CHECK,
        budget=Budget(tokens=60000, tool_calls=8, seconds=120),
    ),
)


def step(
    agent_id: str,
    *,
    objective: str | None = None,
    skill: SkillRef | None = None,
    window: TimeWindow = WINDOW,
    budget: Budget | None = None,
) -> PlanStep:
    return PlanStep(
        agent_id=agent_id,
        skill_id=skill.skill_id if skill else None,
        skill_version=skill.version if skill else None,
        objective="Establish what the offense's events show." if objective is None else objective,
        time_window=window,
        budget=budget or AGENTS.get(agent_id, VERIFICATION_BUDGET),
    )


def case_plan(*steps: PlanStep) -> CasePlan:
    return CasePlan(
        task_id="task-4711-orchestrator-1",
        status=RunStatus.COMPLETED,
        claims=[],
        data_gaps=[],
        injection_suspected=False,
        usage=Usage(tokens=1200, tool_calls=0, seconds=2.5),
        steps=list(steps),
    )


def decide(
    plan: CasePlan | None,
    *,
    needs_investigation: bool = True,
    plan_budget: Budget = PLAN_BUDGET,
    candidates: tuple[PlanCandidate, ...] = CANDIDATES,
    agents: dict[str, Budget] | None = None,
    window: TimeWindow = WINDOW,
) -> PlanDecision:
    return validate_plan(
        plan,
        agents=AGENTS if agents is None else agents,
        candidates=candidates,
        plan_budget=plan_budget,
        window=window,
        needs_investigation=needs_investigation,
    )


def agents_of(decision: PlanDecision) -> list[str]:
    return [s.agent_id for s in decision.steps]


def default_step(agent_id: str) -> PlanStep:
    """A step of the default plan, as the workflow writes it."""
    return PlanStep(
        agent_id=agent_id,
        objective=DEFAULT_OBJECTIVES[agent_id],
        time_window=WINDOW,
        budget=AGENTS[agent_id],
    )


def assert_rejected(decision: PlanDecision, reason: PlanRejectReason, step_no: int | None) -> None:
    """The plan was rejected for `reason` and the default plan replaced it."""
    assert decision.rejection is not None
    assert (decision.rejection.reason, decision.rejection.step) == (reason, step_no)
    assert decision.used_default
    # needs_investigation is True in these tests.
    assert decision.steps == (default_step("investigation"), default_step("verification"))


def test_a_valid_plan_is_accepted_as_it_is() -> None:
    planned = (
        step(
            "investigation", skill=DCSYNC, budget=Budget(tokens=100000, tool_calls=16, seconds=240)
        ),
        step("verification", budget=Budget(tokens=60000, tool_calls=10, seconds=150)),
    )

    decision = decide(case_plan(*planned))

    assert decision == PlanDecision(steps=planned, used_default=False, rejection=None, dropped=())


# --- rule 1: plan agents -------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", ["endpoint", "hunter-internal", "Investigation", ""])
def test_an_unknown_agent_rejects_the_plan(agent_id: str) -> None:
    decision = decide(case_plan(step("investigation"), step(agent_id), step("verification")))

    assert_rejected(decision, PlanRejectReason.UNKNOWN_AGENT, 2)
    assert decision.rejection is not None
    assert decision.rejection.detail == f"step 2: unknown agent {agent_id!r}"


@pytest.mark.parametrize("agent_id", ["triage", "orchestrator", "reporting"])
def test_triage_the_orchestrator_and_reporting_are_never_steps(agent_id: str) -> None:
    # Even when the registry lists the agent for the case workflow.
    agents = {**AGENTS, agent_id: Budget(tokens=60000, tool_calls=4, seconds=120)}

    decision = decide(case_plan(step(agent_id), step("verification")), agents=agents)

    assert_rejected(decision, PlanRejectReason.NOT_A_PLAN_AGENT, 1)


def test_a_long_unknown_agent_id_is_cut_in_the_reason() -> None:
    decision = decide(case_plan(step("x" * 500)))

    assert decision.rejection is not None
    assert decision.rejection.detail == f"step 1: unknown agent '{'x' * 64}...'"


# --- rule 3: repeated agents and empty objectives ----------------------------------------------


def test_an_agent_twice_rejects_the_plan() -> None:
    decision = decide(
        case_plan(step("investigation"), step("investigation", skill=DCSYNC), step("verification"))
    )

    assert_rejected(decision, PlanRejectReason.REPEATED_AGENT, 2)


def test_verification_twice_rejects_the_plan() -> None:
    decision = decide(case_plan(step("verification"), step("investigation"), step("verification")))

    assert_rejected(decision, PlanRejectReason.REPEATED_AGENT, 3)


@pytest.mark.parametrize("objective", ["", "   ", "\n\t"])
def test_an_empty_objective_rejects_the_plan(objective: str) -> None:
    decision = decide(case_plan(step("investigation", objective=objective), step("verification")))

    assert_rejected(decision, PlanRejectReason.EMPTY_OBJECTIVE, 1)


# --- rule 2: skills ------------------------------------------------------------------------------


def test_a_skill_that_is_not_a_candidate_rejects_the_plan() -> None:
    other = SkillRef(skill_id="vpn-new-country", version="1.0.0", content_hash="sha256:" + "c" * 64)

    decision = decide(case_plan(step("investigation", skill=other), step("verification")))

    assert_rejected(decision, PlanRejectReason.SKILL_NOT_A_CANDIDATE, 1)


def test_another_agents_skill_rejects_the_plan() -> None:
    # dcsync-check is a candidate, but for Verification only.
    decision = decide(case_plan(step("investigation", skill=DCSYNC_CHECK), step("verification")))

    assert_rejected(decision, PlanRejectReason.SKILL_FOR_ANOTHER_AGENT, 1)
    assert decision.rejection is not None
    assert "not a candidate for investigation" in decision.rejection.detail


@pytest.mark.parametrize("version", ["0.9.0", "1.0.1", "2.0.0"])
def test_a_version_other_than_the_listed_one_rejects_the_plan(version: str) -> None:
    older = DCSYNC.model_copy(update={"version": version})

    decision = decide(case_plan(step("investigation", skill=older), step("verification")))

    assert_rejected(decision, PlanRejectReason.SKILL_VERSION_NOT_LISTED, 1)
    assert decision.rejection is not None
    assert "listed at version 1.0.0" in decision.rejection.detail


def test_without_candidates_any_skill_rejects_the_plan() -> None:
    decision = decide(
        case_plan(step("investigation", skill=DCSYNC), step("verification")), candidates=()
    )

    assert_rejected(decision, PlanRejectReason.SKILL_NOT_A_CANDIDATE, 1)


def test_a_verification_skill_from_the_list_is_accepted() -> None:
    decision = decide(case_plan(step("verification", skill=DCSYNC_CHECK)))

    assert decision.rejection is None
    assert decision.steps[0].skill_id == "dcsync-check"


# --- the schema (rule 6) ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 5])
def test_a_plan_that_breaks_the_schema_is_rejected(count: int) -> None:
    # Built without validation, as a plan that did not come from the agent could be.
    steps = [step("investigation"), step("verification"), *[step("verification")] * 3][:count]
    plan = CasePlan.model_construct(**{**case_plan(step("verification")).__dict__, "steps": steps})

    decision = decide(plan)

    assert_rejected(decision, PlanRejectReason.SCHEMA, None)
    assert decision.rejection is not None
    assert "validation error for CasePlan" in decision.rejection.detail


def test_no_plan_gives_the_default_plan() -> None:
    decision = decide(None)

    assert_rejected(decision, PlanRejectReason.NO_PLAN, None)


# --- the default plan (rule 6) --------------------------------------------------------------------


def test_the_default_plan_investigates_when_triage_asks_for_it() -> None:
    decision = decide(case_plan(step("triage")), needs_investigation=True)

    assert decision.used_default
    assert decision.steps == (default_step("investigation"), default_step("verification"))
    assert decision.steps[0].skill_id is None
    assert decision.dropped == ()


def test_the_default_plan_only_verifies_when_triage_does_not_ask_for_an_investigation() -> None:
    decision = decide(case_plan(step("triage")), needs_investigation=False)

    assert decision.used_default
    assert decision.steps == (default_step("verification"),)


def test_needs_investigation_does_not_change_an_accepted_plan() -> None:
    planned = case_plan(step("investigation"), step("verification"))

    assert agents_of(decide(planned, needs_investigation=False)) == [
        "investigation",
        "verification",
    ]
    assert agents_of(decide(case_plan(step("verification")), needs_investigation=True)) == [
        "verification"
    ]


# --- rule 4: Verification last ----------------------------------------------------------------------


def test_a_missing_verification_is_added_at_the_end() -> None:
    decision = decide(case_plan(step("investigation", skill=DCSYNC)))

    assert decision.rejection is None
    assert not decision.used_default
    assert agents_of(decision) == ["investigation", "verification"]
    assert decision.steps[1] == default_step("verification")


def test_verification_is_moved_to_the_end() -> None:
    verification = step("verification", objective="Check the replication claims.")

    decision = decide(case_plan(verification, step("investigation")))

    assert agents_of(decision) == ["investigation", "verification"]
    assert decision.steps[1] == verification
    assert not decision.used_default


# --- rule 5: budgets ---------------------------------------------------------------------------------


def test_a_request_above_the_manifest_budget_is_cut_to_it() -> None:
    greedy = Budget(tokens=900000, tool_calls=100, seconds=3600)

    decision = decide(
        case_plan(step("investigation", budget=greedy), step("verification", budget=greedy))
    )

    assert [s.budget for s in decision.steps] == [INVESTIGATION_BUDGET, VERIFICATION_BUDGET]


def test_a_skill_budget_below_the_manifest_cuts_the_step() -> None:
    decision = decide(case_plan(step("investigation", skill=DCSYNC, budget=INVESTIGATION_BUDGET)))

    # tokens and tool calls from the skill, seconds from both (300).
    assert decision.steps[0].budget == DCSYNC_BUDGET


def test_each_part_of_the_budget_is_cut_on_its_own() -> None:
    request = Budget(tokens=100000, tool_calls=30, seconds=200)

    decision = decide(case_plan(step("investigation", skill=DCSYNC, budget=request)))

    assert decision.steps[0].budget == Budget(tokens=100000, tool_calls=20, seconds=200)


def test_a_part_that_is_not_positive_counts_as_no_request() -> None:
    request = Budget(tokens=0, tool_calls=-3, seconds=200)

    decision = decide(case_plan(step("investigation", skill=DCSYNC, budget=request)))

    assert decision.steps[0].budget == Budget(tokens=120000, tool_calls=20, seconds=200)
    assert decision.rejection is None


def test_verifications_budget_is_set_aside_first() -> None:
    # Investigation comes first but may only use what Verification leaves.
    plan_budget = Budget(tokens=200000, tool_calls=30, seconds=420)

    decision = decide(
        case_plan(step("investigation"), step("verification")), plan_budget=plan_budget
    )

    assert agents_of(decision) == ["investigation", "verification"]
    assert decision.steps[1].budget == VERIFICATION_BUDGET
    assert decision.steps[0].budget == Budget(tokens=120000, tool_calls=18, seconds=240)
    assert decision.dropped == ()


def test_the_plan_budget_caps_verification_too() -> None:
    plan_budget = Budget(tokens=50000, tool_calls=40, seconds=480)

    decision = decide(
        case_plan(step("investigation"), step("verification")), plan_budget=plan_budget
    )

    # Nothing is left for Investigation; Verification is never dropped.
    assert agents_of(decision) == ["verification"]
    assert decision.steps[0].budget == Budget(tokens=50000, tool_calls=12, seconds=180)
    assert decision.dropped == ("investigation",)


def test_a_step_left_with_less_than_a_quarter_of_its_manifest_budget_is_dropped() -> None:
    # 117499 tokens are left for Investigation: below a quarter (37500) of its 150000.
    plan_budget = Budget(tokens=80000 + 37499, tool_calls=40, seconds=480)

    decision = decide(
        case_plan(step("investigation"), step("verification")), plan_budget=plan_budget
    )

    assert agents_of(decision) == ["verification"]
    assert decision.dropped == ("investigation",)
    assert not decision.used_default
    assert decision.rejection is None


def test_a_step_left_with_exactly_a_quarter_is_kept() -> None:
    plan_budget = Budget(tokens=80000 + 37500, tool_calls=40, seconds=480)

    decision = decide(
        case_plan(step("investigation"), step("verification")), plan_budget=plan_budget
    )

    assert agents_of(decision) == ["investigation", "verification"]
    assert decision.steps[0].budget.tokens == 37500


def test_any_part_below_a_quarter_drops_the_step() -> None:
    # Tokens and seconds are plenty; 5 tool calls are left, below a quarter (6) of 24.
    plan_budget = Budget(tokens=250000, tool_calls=12 + 5, seconds=480)

    decision = decide(
        case_plan(step("investigation"), step("verification")), plan_budget=plan_budget
    )

    assert decision.dropped == ("investigation",)


def test_a_step_that_asks_for_less_than_a_quarter_is_not_dropped() -> None:
    # It asks for 3 tool calls (below a quarter of 24) and gets them: the plan budget did not
    # cut it.
    small = Budget(tokens=20000, tool_calls=3, seconds=60)

    decision = decide(case_plan(step("investigation", budget=small), step("verification")))

    assert agents_of(decision) == ["investigation", "verification"]
    assert decision.steps[0].budget == small


def test_the_default_plan_is_cut_to_the_plan_budget_too() -> None:
    plan_budget = Budget(tokens=90000, tool_calls=40, seconds=480)

    decision = decide(None, plan_budget=plan_budget)

    assert decision.used_default
    assert agents_of(decision) == ["verification"]
    assert decision.dropped == ("investigation",)


# --- rule 5: windows --------------------------------------------------------------------------------


def test_a_window_that_reaches_outside_the_evaluation_is_clipped() -> None:
    wide = TimeWindow(start=START - timedelta(days=3), end=END + timedelta(hours=2))
    late = TimeWindow(start=START + timedelta(minutes=20), end=END + timedelta(hours=2))

    decision = decide(
        case_plan(step("investigation", window=wide), step("verification", window=late))
    )

    assert decision.rejection is None
    assert decision.steps[0].time_window == WINDOW
    assert decision.steps[1].time_window == TimeWindow(start=START + timedelta(minutes=20), end=END)


def test_a_window_inside_the_evaluation_is_kept() -> None:
    narrow = TimeWindow(start=START + timedelta(minutes=10), end=START + timedelta(minutes=30))

    decision = decide(case_plan(step("investigation", window=narrow)))

    assert decision.steps[0].time_window == narrow


@pytest.mark.parametrize(
    "window",
    [
        # Before the evaluation.
        TimeWindow(start=START - timedelta(days=2), end=START - timedelta(days=1)),
        # After it.
        TimeWindow(start=END + timedelta(minutes=1), end=END + timedelta(hours=1)),
        # Ends before it starts.
        TimeWindow(start=END, end=START),
    ],
)
def test_a_window_with_no_time_in_the_evaluation_becomes_the_evaluations(
    window: TimeWindow,
) -> None:
    decision = decide(case_plan(step("investigation", window=window)))

    assert decision.rejection is None
    assert decision.steps[0].time_window == WINDOW


# --- arguments, determinism and imports ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"agents": {"investigation": INVESTIGATION_BUDGET}},
            "no manifest budget for verification",
        ),
        ({"plan_budget": Budget(tokens=0, tool_calls=40, seconds=480)}, "budgets must be positive"),
        ({"window": TimeWindow(start=END, end=START)}, "ends before it starts"),
    ],
)
def test_invalid_arguments_raise(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decide(case_plan(step("verification")), **change)  # type: ignore[arg-type]


def test_budgets_of_agents_that_are_never_steps_are_not_read() -> None:
    # Reporting and the Orchestrator have no tool calls; T-026 may pass every case agent.
    agents = {
        **AGENTS,
        "reporting": Budget(tokens=60000, tool_calls=0, seconds=120),
        "orchestrator": Budget(tokens=40000, tool_calls=0, seconds=90),
    }

    decision = decide(case_plan(step("investigation"), step("verification")), agents=agents)

    assert agents_of(decision) == ["investigation", "verification"]


@pytest.mark.parametrize("agent_id", ["investigation", "verification"])
def test_a_plan_agents_budget_must_be_positive(agent_id: str) -> None:
    agents = {**AGENTS, agent_id: Budget(tokens=1000, tool_calls=0, seconds=60)}

    with pytest.raises(ValueError, match=f"{agent_id}: budgets must be positive"):
        decide(None, agents=agents)


PLANS = [
    None,
    case_plan(step("verification")),
    case_plan(step("verification"), step("investigation", skill=DCSYNC)),
    case_plan(step("investigation", skill=DCSYNC.model_copy(update={"version": "0.9.0"}))),
    case_plan(step("reporting")),
]


@pytest.mark.parametrize("planned", PLANS)
@pytest.mark.parametrize("needs_investigation", [True, False])
def test_the_same_input_always_gives_the_same_result(
    planned: CasePlan | None, needs_investigation: bool
) -> None:
    first = decide(planned, needs_investigation=needs_investigation)

    for _ in range(3):
        assert decide(planned, needs_investigation=needs_investigation) == first
    # The order of the candidates and of the agents does not matter.
    agents = dict(reversed(AGENTS.items()))
    for candidates in itertools.permutations(CANDIDATES):
        assert (
            decide(
                planned,
                needs_investigation=needs_investigation,
                candidates=candidates,
                agents=agents,
            )
            == first
        )


def test_the_arguments_are_not_changed() -> None:
    planned = case_plan(step("verification"), step("investigation", budget=PLAN_BUDGET))
    before = planned.model_dump()

    decide(planned)

    assert planned.model_dump() == before


def test_the_module_imports_only_the_contracts_and_the_standard_library() -> None:
    source = Path(plan_module.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add((node.module or "").split(".")[0])

    assert imported - set(sys.stdlib_module_names) == {"ais0c_contracts"}


def test_a_rejection_is_a_plain_value() -> None:
    rejection = PlanRejection(reason=PlanRejectReason.EMPTY_OBJECTIVE, step=1, detail="x")

    assert rejection.reason == "empty_objective"
    assert rejection == PlanRejection(reason=PlanRejectReason.EMPTY_OBJECTIVE, step=1, detail="x")
