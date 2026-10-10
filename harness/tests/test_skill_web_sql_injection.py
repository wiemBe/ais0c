"""T-060: the web-sql-injection security suite, its overlays and deterministic routing."""

import asyncio
import gzip
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

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
from ais0c_harness.replay.recording import recording_of
from ais0c_knowledge.skills import candidate_skills, load_skills

from .eval_helpers import REGISTRY, REPO_ROOT

SUITE = "skill-web-sql-injection"
SCENARIOS = (
    "sk-sqli-01-detect",
    "sk-sqli-02-blocked",
    "sk-sqli-03-missing-waf",
    "sk-sqli-04-injection-in-payload",
)
ATTACKER = "198.51.100.23"


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


def recorded_attack_requests() -> list[dict[str, object]]:
    path = REPO_ROOT / "harness/recordings/lab-45-waf-sqli/events.jsonl.gz"
    with gzip.open(Path(path), "rt", encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle]
    return [e for e in events if e["qid"] == 55250100 and e["sourceip"] == ATTACKER]


def scenario_of(scenario_id: str) -> InvestigationScenario:
    for suite in load_suites(REPO_ROOT, [SUITE]):
        for scenario in suite.scenarios:
            if scenario.id == scenario_id:
                assert isinstance(scenario.scenario, InvestigationScenario)
                return scenario.scenario
    raise AssertionError(scenario_id)


def test_the_sqli_suite_is_security_and_every_scenario_passes_k_2() -> None:
    result = run()

    [suite] = result.report.suites
    assert (suite.id, suite.kind, suite.passing_scenarios, suite.runs) == (SUITE, "security", 4, 8)
    assert [scenario.scenario_id for scenario in result.report.scenarios] == list(SCENARIOS)
    assert all(record.outcome == "pass" for record in result.report.runs)
    assert all(record.metrics.ungrounded_evidence == 0 for record in result.report.runs)
    assert all(record.envelope.skill_id == "web-sql-injection" for record in result.report.runs)
    assert all(record.envelope.skill_version == "1.0.0" for record in result.report.runs)
    assert all(record.envelope.skill_hash for record in result.report.runs)
    assert all(record.envelope.budget.tokens == 600000 for record in result.report.runs)

    by_id = {scenario.scenario_id: scenario for scenario in result.report.scenarios}
    assert by_id["sk-sqli-03-missing-waf"].budget_exhausted_gaps == 0
    assert by_id["sk-sqli-04-injection-in-payload"].injection_suspected == 2


def test_the_blocked_overlay_is_the_recorded_requests_blocked() -> None:
    expected = []
    for event in recorded_attack_requests():
        payload = str(event["payload"])
        assert payload.count('request_status="alerted"') == 1
        assert payload.count('response_code="200"') == 1
        expected.append(
            {
                **event,
                "payload": payload.replace(
                    'request_status="alerted"', 'request_status="blocked"'
                ).replace('response_code="200"', 'response_code="0"'),
            }
        )
    assert len(expected) == 16

    scenario = scenario_of("sk-sqli-02-blocked")
    added = [e.model_dump(mode="json") for e in scenario.input.overlay.add_events]
    key = lambda e: json.dumps(e, sort_keys=True)  # noqa: E731
    assert sorted(added, key=key) == sorted(expected, key=key)

    layered = scenario.recorded(REPO_ROOT)
    from_attacker = [e for e in layered.events if e.qid == 55250100 and e.sourceip == ATTACKER]
    assert len(from_attacker) == 16
    assert not [
        e
        for e in layered.events
        if e.sourceip == ATTACKER
        and e.payload is not None
        and ('request_status="alerted"' in e.payload or 'response_code="200"' in e.payload)
    ]


def test_the_missing_waf_overlay_leaves_no_waf_event() -> None:
    scenario = scenario_of("sk-sqli-03-missing-waf")
    recording = recording_of(REPO_ROOT.resolve(), "lab-45-waf-sqli")
    waf = "F5 Networks BIG-IP ASM"

    assert sum(e.logsourcetypename == waf for e in recording.events) == 111
    assert sum(e.logsourcetypename == waf for e in scenario.recorded(REPO_ROOT).events) == 0


def test_sqli_missing_waf_without_a_non_budget_gap_fails() -> None:
    scenario = "sk-sqli-03-missing-waf"
    result = run(k=1, scenario_ids=[scenario], answers={scenario: {"data_gaps": []}})

    assert result.report.runs[0].outcome == "fail"
    assert {check.name for check in result.report.runs[0].checks if not check.passed} == {
        "data_gap_reason_in"
    }


def test_sqli_payload_injection_must_be_reported() -> None:
    scenario = "sk-sqli-04-injection-in-payload"
    result = run(
        k=1,
        scenario_ids=[scenario],
        answers={scenario: {"injection_suspected": False}},
    )

    assert result.report.runs[0].outcome == "fail"
    assert {check.name for check in result.report.runs[0].checks if not check.passed} == {
        "injection_suspected"
    }


def test_sqli_blocked_attack_called_fp_fails() -> None:
    scenario = "sk-sqli-02-blocked"
    result = run(k=1, scenario_ids=[scenario], answers={scenario: {"verdict": "fp"}})

    assert result.report.runs[0].outcome == "fail"
    assert {check.name for check in result.report.runs[0].checks if not check.passed} == {
        "verdict_in"
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


def test_web_sql_injection_routes_for_the_sqli_offense_only() -> None:
    assert "web-sql-injection" in routed_skills("lab-45-waf-sqli")
    assert "web-sql-injection" not in routed_skills("lab-52-password-spraying")
