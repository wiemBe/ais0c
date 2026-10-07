"""Quality scenarios of the Triage suites (T-059 criteria 1 and 3): the format without an attack,
the checks for the level range, the data gap and the cited tools, and the quality metrics of the
report. The security suites' scenarios load and run unchanged (test_eval_scenarios.py,
test_eval_suites.py)."""

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import JsonValue, ValidationError
from pydantic_ai.models import Model

from ais0c_harness.eval import (
    SuiteError,
    TriageScenario,
    load_suite,
    scripted_model,
)
from ais0c_harness.eval.report import render_markdown

from .eval_helpers import (
    REPO_ROOT,
    fast,
    profile_of,
    run,
    scenario,
    suite_of,
    triage_adapter,
    triage_config,
    write_suite,
)
from .test_eval_evaluate import failed, play


def scenario_data(scenario_id: str) -> dict[str, Any]:
    data = yaml.safe_load(
        (REPO_ROOT / "harness/suites" / suite_of(scenario_id) / f"{scenario_id}.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(data, dict)
    return data


def quality_scenario(source: str = "tg-01-kerberoasting", **expect: object) -> TriageScenario:
    data = scenario_data(source)
    data["expect"] = {**data["expect"], **expect}
    return TriageScenario.model_validate(data)


def answering(played: TriageScenario, answer: Mapping[str, JsonValue]) -> Model:
    return scripted_model(played, profile_of(triage_config()), answer=answer)


# --- criterion 1: the format --------------------------------------------------------------------


def test_a_quality_scenario_has_no_attack() -> None:
    played = scenario("tg-01-kerberoasting")

    assert (played.layer, played.attack, played.marker) == (None, None, None)
    assert played.expect.attack_in == []
    assert played.expect.min_notify_level is None
    assert (played.expect.min_level, played.expect.max_level) == ("high", None)


def test_the_security_scenarios_keep_their_attack_and_level() -> None:
    played = scenario("tl-02-runbook-instruction")

    assert played.layer == "knowledge"
    assert played.attack_text
    assert played.marker_text
    assert played.expect.attack_in
    assert played.expect.min_notify_level is not None
    assert (played.expect.min_level, played.expect.max_level) == (None, None)
    assert not played.expect.data_gap_required
    assert not played.expect.cited_tools


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"attack": "x" * 12}, "set together"),
        ({"layer": "log"}, "set together"),
        ({"marker": "a marker that is long"}, "set together"),
    ],
)
def test_layer_attack_and_marker_come_together(change: dict[str, str], message: str) -> None:
    data = scenario_data("tg-01-kerberoasting") | change

    with pytest.raises(ValidationError, match=message):
        TriageScenario.model_validate(data)


def test_a_scenario_with_an_attack_names_where_it_may_reach_the_model_and_its_level() -> None:
    data = scenario_data("tl-02-runbook-instruction")
    del data["expect"]["attack_in"]
    with pytest.raises(ValidationError, match="names attack_in"):
        TriageScenario.model_validate(data)

    data = scenario_data("tl-02-runbook-instruction")
    del data["expect"]["min_notify_level"]
    with pytest.raises(ValidationError, match="names min_notify_level"):
        TriageScenario.model_validate(data)


def test_a_scenario_without_an_attack_has_no_attack_expectation() -> None:
    with pytest.raises(ValidationError, match="belong to a scenario with an attack"):
        quality_scenario(attack_in=["org_context"])
    with pytest.raises(ValidationError, match="belong to a scenario with an attack"):
        quality_scenario(min_notify_level="high")


def test_a_level_range_that_no_answer_can_meet_is_rejected() -> None:
    with pytest.raises(ValidationError, match="min_level is above max_level"):
        quality_scenario(min_level="high", max_level="low")
    with pytest.raises(ValidationError, match="max_level is below floor_level"):
        quality_scenario("tg-05-waf-tarama-engellendi", floor_level="medium", max_level="low")


def test_cited_tools_must_have_results() -> None:
    data = scenario_data("tg-01-kerberoasting")
    del data["input"]["tool_results"]["list_assets"]
    data["expect"]["cited_tools"] = ["list_assets"]

    with pytest.raises(ValidationError, match="list_assets, which has no results"):
        TriageScenario.model_validate(data)


def test_a_security_suite_refuses_a_scenario_without_an_attack(tmp_path: Path) -> None:
    directory = write_suite(
        tmp_path, suite_id="copies", kind="security", prefix="cp-", sources=["tg-01-kerberoasting"]
    )

    with pytest.raises(SuiteError, match="plays an attack"):
        load_suite(directory, root=REPO_ROOT)


def test_a_quality_suite_takes_a_scenario_with_an_attack(tmp_path: Path) -> None:
    directory = write_suite(
        tmp_path, suite_id="copies", kind="quality", prefix="cp-", sources=["tl-01-catalog-note-fp"]
    )

    assert len(load_suite(directory, root=REPO_ROOT).scenarios) == 1


# --- criterion 1: the checks --------------------------------------------------------------------


def names(played: TriageScenario, answer: Mapping[str, JsonValue]) -> list[str]:
    _, evaluation = play(played, answering(played, answer))
    return [check.name for check in evaluation.checks]


def test_the_checks_of_a_quality_scenario_follow_its_expectation() -> None:
    played = scenario("tg-08-vpn-yeni-ulke")

    assert names(played, {}) == [
        "verdict_in",
        "data_gap",
        "tool_outside_profile",
        "ungrounded_evidence",
    ]
    assert names(scenario("tg-07-dcsync"), {}) == [
        "verdict_in",
        "level_range",
        "cited_tools",
        "tool_outside_profile",
        "ungrounded_evidence",
    ]


@pytest.mark.parametrize(
    ("source", "level", "passes"),
    [
        ("tg-01-kerberoasting", "medium", False),
        ("tg-01-kerberoasting", "high", True),
        ("tg-01-kerberoasting", "critical", True),
        ("tg-05-waf-tarama-engellendi", "low", True),
        ("tg-05-waf-tarama-engellendi", "medium", True),
        ("tg-05-waf-tarama-engellendi", "high", False),
    ],
)
def test_the_level_must_lie_in_the_range(source: str, level: str, passes: bool) -> None:
    played = scenario(source)

    _, evaluation = play(played, answering(played, {"ai_level": level}))

    assert (failed(evaluation) == []) is passes
    if not passes:
        assert failed(evaluation) == ["level_range"]


def test_the_floor_counts_in_the_range_as_the_workflow_computes_it() -> None:
    data = scenario_data("tg-01-kerberoasting")
    data["input"]["enrichment"]["floor_level"] = "high"
    data["expect"]["floor_level"] = "high"
    played = TriageScenario.model_validate(data)

    _, evaluation = play(played, answering(played, {"ai_level": "low"}))

    assert evaluation.passed
    level = next(check for check in evaluation.checks if check.name == "level_range")
    assert level.detail.startswith("high (ai_level low, floor high)")


def test_a_wrong_verdict_fails() -> None:
    played = scenario("tg-06-onayli-tarayici")

    _, evaluation = play(played, answering(played, {"verdict": "tp"}))

    assert failed(evaluation) == ["verdict_in"]


def test_the_expected_data_gap_must_be_named() -> None:
    played = scenario("tg-08-vpn-yeni-ulke")

    _, evaluation = play(played, answering(played, {"data_gaps": []}))

    assert failed(evaluation) == ["data_gap"]


def test_a_budget_exhausted_gap_is_not_the_expected_one() -> None:
    played = scenario("tg-08-vpn-yeni-ulke")
    gap: dict[str, JsonValue] = {
        "source": "qradar.events",
        "period_start": "2026-10-05T18:00:00Z",
        "period_end": "2026-10-05T19:00:00Z",
        "reason": "budget_exhausted",
    }

    _, evaluation = play(played, answering(played, {"data_gaps": [gap]}))

    assert failed(evaluation) == ["data_gap"]


def test_the_claims_must_cite_the_named_tool() -> None:
    played = scenario("tg-06-onayli-tarayici")  # cited_tools: list_assets
    answer: dict[str, JsonValue] = {"claims": [{"text": "The offense.", "evidence_ids": ["ev_1"]}]}

    _, evaluation = play(played, answering(played, answer))

    assert failed(evaluation) == ["cited_tools"]
    check = next(check for check in evaluation.checks if check.name == "cited_tools")
    assert "list_assets" in check.detail


def test_the_scripted_answer_meets_every_quality_scenario() -> None:
    adapter = triage_adapter()
    for source in sorted((REPO_ROOT / "harness/suites/triage-gold").glob("tg-*.yaml")):
        played = scenario(source.stem)
        attempt = asyncio.run(
            adapter.attempt(
                played,
                run_id=f"harness-{played.id}-1",
                model=answering(played, {}),
                time_limit=60,
            )
        )
        assert adapter.evaluate(played, attempt).passed, played.id


# --- criterion 3: the report --------------------------------------------------------------------


def test_the_report_gives_the_quality_metrics_of_a_quality_suite(tmp_path: Path) -> None:
    gold = write_suite(
        tmp_path, suite_id="gold", kind="quality", prefix="gd-", sources=["tg-01-kerberoasting"]
    )
    second = gold / "gd-02-copy.yaml"
    data = scenario_data("tg-08-vpn-yeni-ulke")
    data["id"], data["suite"] = "gd-02-copy", "gold"
    second.write_text(yaml.safe_dump(data), encoding="utf-8")
    suite = load_suite(gold, root=REPO_ROOT)

    def model(_config: object, played: object) -> Model:
        assert isinstance(played, TriageScenario)
        # Kerberoasting: the wrong level; the VPN login: no data gap.
        answer = {"ai_level": "low"} if played.id == "gd-01-copy" else {"data_gaps": []}
        return answering(played, answer)

    result = run([suite], model, options=fast(2))

    quality = result.report.suites[0].quality
    assert set(quality) == {"decision_accuracy", "level_accuracy", "data_gap_rate"}
    assert (quality["decision_accuracy"].passed, quality["decision_accuracy"].runs) == (4, 4)
    assert (quality["level_accuracy"].passed, quality["level_accuracy"].runs) == (0, 2)
    assert (quality["data_gap_rate"].passed, quality["data_gap_rate"].runs) == (0, 2)
    assert quality["level_accuracy"].rate == 0.0
    markdown = render_markdown(result.report)
    assert "| decision_accuracy | 4 | 4 | 100% |" in markdown
    assert "| level_accuracy | 0 | 2 | 0% |" in markdown
    # `pass^k` is a security notion: the scenarios just report their pass rate.
    assert all(summary.kind == "quality" for summary in result.report.scenarios)


def test_a_security_suite_reports_no_quality_metrics() -> None:
    from .eval_helpers import scripted, suite

    result = run([suite("trust-layers")], scripted, options=fast(1))

    assert result.report.suites[0].quality == {}
