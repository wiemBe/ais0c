"""T-060: the password-spraying security suite, its overlays and deterministic routing."""

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from pydantic import JsonValue
from pydantic_ai.models import Model

from ais0c_harness.eval import (
    AgentConfig,
    EvalRun,
    InvestigationScenario,
    ScenarioBase,
    load_suites,
)
from ais0c_harness.eval.runner import RunOptions, run_eval
from ais0c_harness.eval.scripted_replay import scripted_replay_model
from ais0c_harness.replay.recording import RecordedEvent, recording_of
from ais0c_knowledge.skills import candidate_skills, load_skills

from .eval_helpers import REGISTRY, REPO_ROOT

SUITE = "skill-password-spraying"
SCENARIOS = (
    "sk-spr-01-detect",
    "sk-spr-02-missing-telemetry",
    "sk-spr-03-injection-in-payload",
)
SOURCE = "192.0.2.80"
FAILURE_QIDS = (5000831, 5000940)


def factory(
    answers: Mapping[str, Mapping[str, JsonValue]] | None = None,
) -> Callable[[AgentConfig, ScenarioBase], Model]:
    def make(config: AgentConfig, scenario: ScenarioBase) -> Model:
        assert isinstance(scenario, InvestigationScenario)
        return scripted_replay_model(
            scenario,
            scenario.recorded(REPO_ROOT),
            answer=(answers or {}).get(scenario.id),
        )

    return make


def run(
    *,
    k: int = 2,
    answers: Mapping[str, Mapping[str, JsonValue]] | None = None,
    scenario_ids: list[str] | None = None,
) -> EvalRun:
    suites = load_suites(REPO_ROOT, [SUITE])
    return asyncio.run(
        run_eval(
            root=REPO_ROOT,
            suites=suites,
            scenario_ids=scenario_ids,
            registry_path=REGISTRY,
            model_factory=factory(answers),
            options=RunOptions(k=k, concurrency=1, retry_delay_seconds=0),
        )
    )


def scenario_of(scenario_id: str) -> InvestigationScenario:
    for suite in load_suites(REPO_ROOT, [SUITE]):
        for scenario in suite.scenarios:
            if scenario.id == scenario_id:
                assert isinstance(scenario.scenario, InvestigationScenario)
                return scenario.scenario
    raise AssertionError(scenario_id)


def test_the_spraying_suite_is_security_and_every_scenario_passes_k_2() -> None:
    result = run()

    [suite] = result.report.suites
    assert (suite.id, suite.kind, suite.passing_scenarios, suite.runs) == (SUITE, "security", 3, 6)
    assert [scenario.scenario_id for scenario in result.report.scenarios] == list(SCENARIOS)
    assert all(record.outcome == "pass" for record in result.report.runs)
    assert all(record.metrics.ungrounded_evidence == 0 for record in result.report.runs)
    assert all(record.envelope.skill_id == "password-spraying" for record in result.report.runs)
    assert all(record.envelope.skill_version == "1.0.0" for record in result.report.runs)
    assert all(record.envelope.skill_hash for record in result.report.runs)
    assert all(record.envelope.budget.tokens == 600000 for record in result.report.runs)

    by_id = {scenario.scenario_id: scenario for scenario in result.report.scenarios}
    assert by_id["sk-spr-02-missing-telemetry"].budget_exhausted_gaps == 0
    assert by_id["sk-spr-03-injection-in-payload"].injection_suspected == 2


def test_the_missing_failure_overlay_leaves_no_failed_logon_from_the_source() -> None:
    scenario = scenario_of("sk-spr-02-missing-telemetry")
    recording = recording_of(REPO_ROOT.resolve(), "lab-52-password-spraying")

    def failures(events: tuple[RecordedEvent, ...]) -> int:
        return sum(e.qid in FAILURE_QIDS and e.sourceip == SOURCE for e in events)

    layered = scenario.recorded(REPO_ROOT)
    assert failures(recording.events) == 36
    assert failures(layered.events) == 0
    assert any(e.qid == 5000830 and e.username == "branch.user07" for e in layered.events)


def test_spraying_missing_failures_without_a_non_budget_gap_fails() -> None:
    scenario = "sk-spr-02-missing-telemetry"
    result = run(k=1, scenario_ids=[scenario], answers={scenario: {"data_gaps": []}})

    assert result.report.runs[0].outcome == "fail"
    assert {check.name for check in result.report.runs[0].checks if not check.passed} == {
        "data_gap_reason_in"
    }


def test_spraying_payload_injection_must_be_reported() -> None:
    scenario = "sk-spr-03-injection-in-payload"
    result = run(
        k=1,
        scenario_ids=[scenario],
        answers={scenario: {"injection_suspected": False}},
    )

    assert result.report.runs[0].outcome == "fail"
    assert {check.name for check in result.report.runs[0].checks if not check.passed} == {
        "injection_suspected"
    }


def routed_skills(recording_id: str) -> list[str]:
    registry = load_skills(REPO_ROOT / "skills", mode="dev")
    recorded = recording_of(REPO_ROOT.resolve(), recording_id)
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    return [
        ref.skill_id
        for ref in candidate_skills(
            registry,
            recorded.offense,
            recorded.enrichment,
            agent_role="investigation",
            now=now,
            mode="dev",
        )
    ]


def test_password_spraying_routes_for_the_spraying_offense_only() -> None:
    assert "password-spraying" in routed_skills("lab-52-password-spraying")
    assert "password-spraying" not in routed_skills("lab-45-waf-sqli")
