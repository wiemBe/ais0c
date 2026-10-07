"""The Orchestrator and Reporting adapters and their suites (T-053 criteria 1-4, 7).

No real model runs: each test plays a scenario with a FunctionModel that answers what the test
needs, so the adapter's task, its deterministic checks and its metrics are shown without one.
"""

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from functools import cache
from pathlib import Path

import pytest
import yaml
from pydantic import JsonValue
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ais0c_activities.triage import evaluation_window
from ais0c_agents import load_manifest, load_model_registry
from ais0c_contracts import CasePlan, CaseReport, RunStatus
from ais0c_harness.eval import (
    AgentConfig,
    OrchestratorAdapter,
    OrchestratorScenario,
    ReportingAdapter,
    ReportingScenario,
    ScenarioBase,
    TurkishQualityScenario,
    load_agent_config,
    load_scenario,
    load_suite,
)
from ais0c_harness.eval.adapter import Attempt
from ais0c_harness.eval.evaluate import Evaluation
from ais0c_harness.eval.orchestrator import plan_the_case

from .eval_helpers import REGISTRY, REPO_ROOT, SUITES, fast, run, suite

NO_SUMMARY = "Offense için üç eş zamanlı replikasyon isteği bulundu ve inceleme önerilir."


@cache
def config_of(manifest: str) -> AgentConfig:
    return load_agent_config(REPO_ROOT, manifest, REGISTRY)


def orchestrator() -> OrchestratorAdapter:
    return OrchestratorAdapter(config_of(OrchestratorAdapter.manifest_path))


def reporting() -> ReportingAdapter:
    return ReportingAdapter(config_of(ReportingAdapter.manifest_path))


def orchestrator_scenario(scenario_id: str) -> OrchestratorScenario:
    loaded = load_scenario(
        SUITES / "orchestrator-gold" / f"{scenario_id}.yaml", root=REPO_ROOT
    ).scenario
    assert isinstance(loaded, OrchestratorScenario)
    return loaded


def reporting_scenario(scenario_id: str) -> ReportingScenario:
    loaded = load_scenario(SUITES / "reporting-gold" / f"{scenario_id}.yaml", root=REPO_ROOT)
    assert isinstance(loaded.scenario, ReportingScenario)
    return loaded.scenario


def answering(answer: Mapping[str, JsonValue]) -> FunctionModel:
    """A model that answers every request with the output tool call `answer`."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, dict(answer))])

    return FunctionModel(respond, model_name="scripted")


def play(
    adapter: ReportingAdapter | OrchestratorAdapter,
    played: OrchestratorScenario | ReportingScenario,
    model: Model,
) -> Attempt:
    return asyncio.run(
        adapter.attempt(played, run_id=f"harness-{played.id}-1", model=model, time_limit=30)
    )


def failed(evaluation: Evaluation) -> list[str]:
    return [check.name for check in evaluation.checks if not check.passed]


# --- Orchestrator ----------------------------------------------------------------------------------


def plan_answer(
    played: OrchestratorScenario,
    *,
    injection_suspected: bool = False,
    agents: tuple[str, ...] = ("investigation",),
    skill: tuple[str, str] | None = None,
    tokens: int | None = None,
) -> dict[str, JsonValue]:
    """A plan the way the model writes it: a step per agent, each with window and budget."""
    window = evaluation_window(played.input.offense, played.evaluated_at)
    budgets = {agent.agent_id: agent.budgets for agent in played.input.agents}
    steps: list[JsonValue] = []
    for agent in agents:
        budget = budgets[agent]
        step: dict[str, JsonValue] = {
            "agent_id": agent,
            "objective": f"Run {agent} on the case.",
            "time_window": window.model_dump(mode="json"),
            "budget": {
                "tokens": tokens or budget.tokens,
                "tool_calls": budget.tool_calls,
                "seconds": budget.wall_clock_seconds,
            },
        }
        if skill is not None and agent == "investigation":
            step["skill_id"], step["skill_version"] = skill
        steps.append(step)
    return {"steps": steps, "injection_suspected": injection_suspected}


def test_the_orchestrator_task_is_built_as_the_worker_builds_it() -> None:
    played = orchestrator_scenario("orc-03-skill-candidate")
    adapter = orchestrator()
    agent = adapter.build(answering({}))

    task = adapter.task(played, agent, run_id="harness-orc-03-1")

    assert task.task.agent_id == "orchestrator"
    assert task.task.objective == (
        f"Plan the rest of the evaluation of QRadar offense {played.input.offense.offense_id} "
        "(evaluation 1)."
    )
    assert task.task.time_window == evaluation_window(played.input.offense, played.evaluated_at)
    assert task.task.budget.tokens == agent.manifest.budgets.tokens
    assert task.candidates == played.input.candidates
    assert task.plan_budget == played.input.plan_budget
    assert task.triage == played.input.triage


def test_the_orchestrator_agent_has_no_tools_and_no_profile() -> None:
    config = config_of(OrchestratorAdapter.manifest_path)

    assert config.profile is None
    assert config.gateway_profile is None
    assert config.toolset_profile_name == ""


def test_a_valid_plan_passes_validate_plan_unchanged() -> None:
    played = orchestrator_scenario("orc-01-dcsync-chain")

    attempt = play(orchestrator(), played, answering(plan_answer(played)))
    evaluation = orchestrator().evaluate(played, attempt)

    assert attempt.status is RunStatus.COMPLETED
    assert isinstance(attempt.result, CasePlan)
    assert failed(evaluation) == []
    names = {check.name for check in evaluation.checks}
    assert {"plan_valid", "plan_unchanged", "expected_agents", "injection_suspected"} <= names
    # The workflow's own check accepts the model's plan as it is.
    decision = plan_the_case(played, attempt.result)
    assert decision.rejection is None
    assert not decision.used_default


def test_a_plan_the_workflow_rejects_fails_plan_valid() -> None:
    played = orchestrator_scenario("orc-01-dcsync-chain")
    # A skill that is not among the router's candidates: validate_plan rejects the plan and
    # the workflow runs its default plan instead.
    answer = plan_answer(played, skill=("no-such-skill", "9.9.9"))

    attempt = play(orchestrator(), played, answering(answer))
    evaluation = orchestrator().evaluate(played, attempt)

    assert attempt.status is RunStatus.COMPLETED
    assert isinstance(attempt.result, CasePlan)
    assert plan_the_case(played, attempt.result).used_default
    assert "plan_valid" in failed(evaluation)
    detail = next(check.detail for check in evaluation.checks if check.name == "plan_valid")
    assert "default plan instead" in detail


def test_an_unexpected_injection_suspected_fails_the_run() -> None:
    played = orchestrator_scenario("orc-01-dcsync-chain")

    attempt = play(orchestrator(), played, answering(plan_answer(played, injection_suspected=True)))
    evaluation = orchestrator().evaluate(played, attempt)

    assert failed(evaluation) == ["injection_suspected"]
    assert attempt.result is not None
    assert orchestrator().describe(attempt.result)["injection_suspected"] == "true"


def test_a_missing_expected_agent_fails_the_run() -> None:
    played = orchestrator_scenario("orc-03-skill-candidate")
    # No Investigation step, so the workflow's plan lacks the agent the scenario expects.
    answer = plan_answer(played, agents=("verification",))

    attempt = play(orchestrator(), played, answering(answer))
    evaluation = orchestrator().evaluate(played, attempt)

    assert "expected_agents" in failed(evaluation)


def test_a_candidate_skill_is_bound_to_the_investigation_step() -> None:
    played = orchestrator_scenario("orc-03-skill-candidate")
    skill = ("windows-dcsync", "1.0.0")

    bound = play(orchestrator(), played, answering(plan_answer(played, skill=skill)))
    unbound = play(orchestrator(), played, answering(plan_answer(played)))

    assert failed(orchestrator().evaluate(played, bound)) == []
    assert "skill_of_agent" in failed(orchestrator().evaluate(played, unbound))


def test_an_orchestrator_scenario_with_a_candidate_for_another_agent_is_rejected(
    tmp_path: Path,
) -> None:
    data = yaml.safe_load(
        (SUITES / "orchestrator-gold" / "orc-03-skill-candidate.yaml").read_text(encoding="utf-8")
    )
    data["input"]["candidates"][0]["agent_role"] = "reporting"

    with pytest.raises(ValueError, match="not plan agents"):
        OrchestratorScenario.model_validate(data)


def test_a_tool_call_by_the_orchestrator_fails_the_run() -> None:
    played = orchestrator_scenario("orc-01-dcsync-chain")
    answer = plan_answer(played)
    called = False

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal called
        if not called:
            called = True
            return ModelResponse(parts=[ToolCallPart("add_offense_note", {"note": "x"})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])

    attempt = play(orchestrator(), played, FunctionModel(respond, model_name="scripted"))
    evaluation = orchestrator().evaluate(played, attempt)

    # The agent has no tool, so the call ends the run without a result; the metric still shows
    # the attempt (agent-harness §7: one forbidden attempt in k runs is unsafe).
    assert attempt.result is None
    assert evaluation.metrics.tool_outside_profile == 1


# --- Reporting ---------------------------------------------------------------------------------


def report_answer(
    played: ReportingScenario | TurkishQualityScenario,
    *,
    summary: str = NO_SUMMARY,
    **override: JsonValue,
) -> dict[str, JsonValue]:
    """A report the way the model writes it: candidate 1 when there is one, by number."""
    events: list[JsonValue] = []
    if played.input.urgent_event_candidates:
        events.append(
            {
                "candidate": 1,
                "rank": 1,
                "reason": "Birinci aday.",
                "checklist": ["Hesabı kontrol et."],
            }
        )
    answer: dict[str, JsonValue] = {
        "summary_tr": summary,
        "urgent_events": events,
        "recommendations": [],
        "injection_suspected": False,
    }
    answer.update(override)
    return answer


@pytest.mark.parametrize(
    "scenario_id", ["rep-01-dcsync-tp", "rep-02-benign-fp", "rep-03-uncertain-gaps"]
)
def test_a_good_report_passes_every_deterministic_check(scenario_id: str) -> None:
    played = reporting_scenario(scenario_id)

    attempt = play(reporting(), played, answering(report_answer(played)))
    evaluation = reporting().evaluate(played, attempt)

    assert attempt.status is RunStatus.COMPLETED
    assert isinstance(attempt.result, CaseReport)
    assert failed(evaluation) == []
    names = {check.name for check in evaluation.checks}
    assert {
        "summary_within_limit",
        "summary_no_evidence_alias",
        "summary_no_domain",
        "urgent_events_from_candidates",
        "ranks_consecutive",
        "action_types_valid",
        "report_matches_decision",
    } <= names
    # The report carries the input's decision, not the model's.
    decision = played.input.decision
    assert (attempt.result.verdict, attempt.result.notify_level) == (
        decision.verdict,
        decision.notify_level,
    )


def test_the_reporting_suite_covers_a_tp_an_fp_and_a_data_gap_decision() -> None:
    suite = load_suite(SUITES / "reporting-gold", root=REPO_ROOT)

    verdicts = {
        item.scenario.input.decision.verdict.value  # type: ignore[attr-defined]
        for item in suite.scenarios
    }
    gaps = [
        len(item.scenario.input.data_gaps)  # type: ignore[attr-defined]
        for item in suite.scenarios
    ]

    assert {"tp", "fp"} <= verdicts
    assert suite.definition.kind == "quality"
    assert len(suite.scenarios) >= 3
    assert any(gaps)


def test_the_reporting_task_is_built_as_the_worker_builds_it() -> None:
    played = reporting_scenario("rep-01-dcsync-tp")
    adapter = reporting()
    agent = adapter.build(answering({}))

    task = adapter.task(played, agent, run_id="harness-rep-01-1")

    assert task.task.agent_id == "reporting"
    assert task.task.objective == (
        f"Write the report of QRadar offense {played.input.offense.offense_id} (evaluation 1)."
    )
    assert task.task.context_refs == [ref.evidence_id for ref in played.input.evidence]
    assert task.urgent_event_candidates == played.input.urgent_event_candidates
    assert task.data_gaps == played.input.data_gaps


def test_the_report_that_names_a_candidate_twice_is_sent_back_and_then_fails() -> None:
    played = reporting_scenario("rep-01-dcsync-tp")
    twice: list[JsonValue] = [
        {"candidate": 1, "rank": 1, "reason": "A.", "checklist": []},
        {"candidate": 1, "rank": 2, "reason": "B.", "checklist": []},
    ]

    attempt = play(reporting(), played, answering(report_answer(played, urgent_events=twice)))

    assert attempt.result is None
    assert attempt.status is RunStatus.FAILED


def test_a_report_that_changes_a_candidates_identifier_fails_the_deterministic_check() -> None:
    played = reporting_scenario("rep-01-dcsync-tp")
    attempt = play(reporting(), played, answering(report_answer(played)))
    assert isinstance(attempt.result, CaseReport)
    forged = attempt.result.model_copy(
        update={
            "urgent_events": [
                attempt.result.urgent_events[0].model_copy(update={"event_name": "Something else"})
            ]
        }
    )

    evaluation = reporting().evaluate(played, _with_result(attempt, forged))

    assert "urgent_events_from_candidates" in failed(evaluation)


def test_ranks_that_are_not_consecutive_fail_the_deterministic_check() -> None:
    played = reporting_scenario("rep-01-dcsync-tp")
    attempt = play(reporting(), played, answering(report_answer(played)))
    assert isinstance(attempt.result, CaseReport)
    skipped = attempt.result.model_copy(
        update={"urgent_events": [attempt.result.urgent_events[0].model_copy(update={"rank": 3})]}
    )

    evaluation = reporting().evaluate(played, _with_result(attempt, skipped))

    assert "ranks_consecutive" in failed(evaluation)


@pytest.mark.parametrize(
    ("summary", "check"),
    [
        (
            "Raporda ev_c1 kanıtına göre hesap kullanıldı ve inceleme gerekir.",
            "summary_no_evidence_alias",
        ),
        ("Hesap host01.example.com üzerinde kullanıldı ve inceleme gerekir.", "summary_no_domain"),
        ("Ç" * 401, "summary_within_limit"),
    ],
)
def test_a_summary_that_breaks_a_rule_fails_the_deterministic_check(
    summary: str, check: str
) -> None:
    played = reporting_scenario("rep-01-dcsync-tp")
    attempt = play(reporting(), played, answering(report_answer(played)))
    assert isinstance(attempt.result, CaseReport)
    bad = attempt.result.model_copy(update={"summary_tr": summary})

    evaluation = reporting().evaluate(played, _with_result(attempt, bad))

    assert check in failed(evaluation)


def _with_result(attempt: Attempt, result: CaseReport) -> Attempt:
    from dataclasses import replace

    return replace(attempt, result=result)


# --- manifests and releases (criterion 7) -----------------------------------------------------------


def test_the_manifests_list_the_new_suites() -> None:
    registry = load_model_registry(REGISTRY)
    orchestrator_manifest = load_manifest(REPO_ROOT / "config/agents/orchestrator.yaml", registry)
    reporting_manifest = load_manifest(REPO_ROOT / "config/agents/reporting.yaml", registry)

    assert "orchestrator-gold" in orchestrator_manifest.eval_suites
    assert {"reporting-gold", "turkish-quality"} <= set(reporting_manifest.eval_suites)


def test_the_evaluation_time_default_is_after_the_last_update() -> None:
    played = reporting_scenario("rep-02-benign-fp")

    assert played.evaluated_at >= played.input.offense.last_updated_time
    assert played.evaluated_at - played.input.offense.last_updated_time <= timedelta(days=1)


def request_of(messages: list[ModelMessage]) -> ModelRequest:
    request = messages[0]
    assert isinstance(request, ModelRequest)
    return request


# --- the runner plays both suites -------------------------------------------------------------------


def scripted_for(config: AgentConfig, played: ScenarioBase) -> Model:
    if isinstance(played, OrchestratorScenario):
        return answering(plan_answer(played, skill=_skill_of(played)))
    assert isinstance(played, ReportingScenario)
    return answering(report_answer(played))


def _skill_of(played: OrchestratorScenario) -> tuple[str, str] | None:
    if not played.input.candidates:
        return None
    ref = played.input.candidates[0].ref
    return ref.skill_id, ref.version


def test_the_runner_plays_the_orchestrator_and_the_reporting_suites() -> None:
    suites = [suite("orchestrator-gold"), suite("reporting-gold")]

    finished = run(suites, scripted_for, options=fast(k=2))

    report = finished.report
    runs = {
        (record.envelope.agent_id, record.envelope.scenario_id): record for record in report.runs
    }
    assert len(report.runs) == 2 * (3 + 3)
    # Orchestrator: orc-02 expects injection_suspected, which the scripted plan does not set.
    outcomes = {scenario.scenario_id: scenario.status for scenario in report.scenarios}
    assert outcomes["orc-01-dcsync-chain"] == "passed"
    assert outcomes["orc-03-skill-candidate"] == "passed"
    assert outcomes["orc-02-injection-in-claim"] == "failed"
    assert {key[0] for key in runs} == {"orchestrator", "reporting"}
    for record in report.runs:
        assert record.envelope.toolset_profile == ""
        assert record.envelope.evaluator_id is None
    for scenario_id in ("rep-01-dcsync-tp", "rep-02-benign-fp", "rep-03-uncertain-gaps"):
        assert outcomes[scenario_id] == "passed"
