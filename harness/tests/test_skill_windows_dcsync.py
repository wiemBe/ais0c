"""T-055: the windows-dcsync security suite, overlays and deterministic routing."""

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from pydantic import JsonValue
from pydantic_ai.models import Model

from ais0c_contracts import CatalogContext, CatalogLogSource, EnrichmentContext, OffenseSnapshot
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

SUITE = "skill-windows-dcsync"
SCENARIOS = (
    "sk-dcs-01-detect",
    "sk-dcs-02-missing-telemetry",
    "sk-dcs-03-injection-in-payload",
)


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


def test_the_skill_suite_is_security_and_every_scenario_passes_k_2() -> None:
    result = run()

    [suite] = result.report.suites
    assert (suite.id, suite.kind, suite.passing_scenarios, suite.runs) == (SUITE, "security", 3, 6)
    assert [scenario.scenario_id for scenario in result.report.scenarios] == list(SCENARIOS)
    assert all(record.outcome == "pass" for record in result.report.runs)
    assert all(record.metrics.ungrounded_evidence == 0 for record in result.report.runs)
    assert all(record.envelope.skill_id == "windows-dcsync" for record in result.report.runs)
    assert all(record.envelope.skill_version == "1.0.0" for record in result.report.runs)
    assert all(record.envelope.skill_hash for record in result.report.runs)
    assert all(record.envelope.budget.tokens == 600000 for record in result.report.runs)

    by_id = {scenario.scenario_id: scenario for scenario in result.report.scenarios}
    missing = by_id["sk-dcs-02-missing-telemetry"]
    injection = by_id["sk-dcs-03-injection-in-payload"]
    assert missing.distributions["verdict"] == {"suspicious": 2}
    assert missing.budget_exhausted_gaps == 0
    assert injection.injection_suspected == 2


def test_missing_telemetry_without_a_non_budget_gap_fails_closed() -> None:
    scenario = "sk-dcs-02-missing-telemetry"
    result = run(k=1, scenario_ids=[scenario], answers={scenario: {"data_gaps": []}})

    assert result.report.runs[0].outcome == "fail"
    assert {check.name for check in result.report.runs[0].checks if not check.passed} == {
        "data_gap_reason_in"
    }


def test_payload_injection_must_be_reported() -> None:
    scenario = "sk-dcs-03-injection-in-payload"
    result = run(
        k=1,
        scenario_ids=[scenario],
        answers={scenario: {"injection_suspected": False}},
    )

    assert result.report.runs[0].outcome == "fail"
    assert {check.name for check in result.report.runs[0].checks if not check.passed} == {
        "injection_suspected"
    }


def test_windows_dcsync_routes_only_for_the_dcsync_offense() -> None:
    registry = load_skills(REPO_ROOT / "skills", mode="dev")
    recorded = recording_of(REPO_ROOT.resolve(), "lab-30-dcsync")
    rule = recorded.enrichment.catalog.rules[0].model_copy(
        update={"attack_techniques": ["T1003.006"]}
    )
    dcsync_enrichment = recorded.enrichment.model_copy(
        update={"catalog": recorded.enrichment.catalog.model_copy(update={"rules": [rule]})}
    )
    now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

    def routed(offense: OffenseSnapshot, enrichment: EnrichmentContext) -> list[str]:
        return [
            ref.skill_id
            for ref in candidate_skills(
                registry,
                offense,
                enrichment,
                agent_role="investigation",
                now=now,
                mode="dev",
            )
        ]

    assert "windows-dcsync" in routed(recorded.offense, dcsync_enrichment)

    for description, source_type in (
        ("VPN login from a new country", "VPN Gateway"),
        ("Routine system background event", "System Notification"),
    ):
        offense = recorded.offense.model_copy(
            update={
                "description": description,
                "rule_ids": [900001],
                "rule_names": [description],
                "log_source_ids": [9001],
            }
        )
        enrichment = EnrichmentContext(
            catalog=CatalogContext(
                rules=[],
                log_sources=[CatalogLogSource(log_source_id=9001, type_name=source_type)],
            ),
            critical_asset_hits=[],
            ioc_hits=[],
            entity_resolutions=[],
        )
        assert "windows-dcsync" not in routed(offense, enrichment)
