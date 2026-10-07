"""The model gate, B2 (T-030 criterion 9): the same report passes; each block reason and each
reason the reports cannot be compared has its own test."""

from collections.abc import Callable
from fractions import Fraction

import pytest

from ais0c_harness.eval import Report, compare_reports
from ais0c_harness.eval.gate import BLOCK, INCOMPARABLE, PASS

from .eval_helpers import fast, run, scripted, suite, triage_config

SCENARIOS = ["tl-01-catalog-note-fp", "tl-02-runbook-instruction"]


@pytest.fixture(scope="module")
def baseline() -> Report:
    report = run(
        [suite("trust-layers")],
        scripted,
        options=fast(2),
        scenario_ids=SCENARIOS,
    ).report
    assert report.passed
    return report


def with_suite(report: Report, **changes: object) -> Report:
    return report.model_copy(update={"suites": [report.suites[0].model_copy(update=changes)]})


def with_scenario(report: Report, scenario_id: str, **changes: object) -> Report:
    scenarios = [
        scenario.model_copy(update=changes) if scenario.scenario_id == scenario_id else scenario
        for scenario in report.scenarios
    ]
    return report.model_copy(update={"scenarios": scenarios})


def with_agent(report: Report, **changes: object) -> Report:
    return report.model_copy(update={"agents": [report.agents[0].model_copy(update=changes)]})


def with_gate(report: Report, gate_id: str, **changes: object) -> Report:
    gates = [
        gate.model_copy(update=changes) if gate.id == gate_id else gate
        for gate in report.hard_gates
    ]
    return report.model_copy(update={"hard_gates": gates, "passed": False})


def test_the_same_report_passes(baseline: Report) -> None:
    result = compare_reports(baseline, baseline)

    assert (result.exit_code, result.reasons) == (PASS, [])
    assert result.text.endswith("Passed.\n")
    assert "trust-layers: 100% -> 100% (4/4 -> 4/4)" in result.text
    assert f"total tokens: {baseline.total_tokens:,} -> {baseline.total_tokens:,}" in result.text


def test_another_model_release_is_expected_and_printed(baseline: Report) -> None:
    release = triage_config().model_release.model_copy(
        update={
            "target": "hosted_vllm/deepseek-ai/DeepSeek-V4-Flash",
            "artifact": "on-prem-artifact",
        }
    )
    candidate = with_agent(baseline, model_release=release)

    result = compare_reports(baseline, candidate)

    assert result.exit_code == PASS
    assert "'artifact': 'deepseek/deepseek-v4-flash'" in result.text
    assert "'artifact': 'on-prem-artifact'" in result.text


# --- not comparable -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda report: with_suite(report, version="0" * 64),
            "suite versions differ: trust-layers",
        ),
        (
            lambda report: with_scenario(
                report, "tl-02-runbook-instruction", scenario_version="0" * 64
            ),
            "scenario versions differ: trust-layers/tl-02-runbook-instruction",
        ),
        (lambda report: with_agent(report, agent_version="1.2.0"), "agent versions differ: triage"),
        (lambda report: with_agent(report, prompt_hash="0" * 64), "prompt hashes differ: triage"),
        (
            lambda report: with_agent(report, toolset_sha256="0" * 64),
            "tool profile hashes differ: triage",
        ),
        (
            lambda report: report.model_copy(
                update={"settings": report.settings.model_copy(update={"k": 5})}
            ),
            "k differs: 2 and 5",
        ),
        (
            lambda report: report.model_copy(update={"scenarios": report.scenarios[:1]}),
            "scenario versions differ: trust-layers/tl-02-runbook-instruction",
        ),
    ],
    ids=["suite", "scenario", "agent", "prompt", "toolset", "k", "scenario-set"],
)
def test_reports_that_ran_something_else_are_not_comparable(
    baseline: Report, change: Callable[[Report], Report], reason: str
) -> None:
    candidate = change(baseline)

    result = compare_reports(baseline, candidate)

    assert result.exit_code == INCOMPARABLE
    assert result.reasons == [reason]
    assert "Not comparable:" in result.text


# --- blocked ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gate_id",
    [
        "unauthorized_tool_execution",
        "ungrounded_evidence",
        "tool_argument_schema_validity",
        "security_pass_k",
        "completeness",
    ],
)
def test_a_failing_hard_gate_of_the_candidate_blocks(baseline: Report, gate_id: str) -> None:
    candidate = with_gate(baseline, gate_id, passed=False, detail="1 found")

    result = compare_reports(baseline, candidate)

    assert result.exit_code == BLOCK
    assert result.reasons == [f"candidate hard gate {gate_id} fails: 1 found"]


def test_a_scenario_that_loses_pass_k_blocks(baseline: Report) -> None:
    candidate = with_scenario(
        baseline, "tl-01-catalog-note-fp", pass_k=False, status="failed", passes=1
    )
    # The suite's rate stays, so pass^k is the only reason.

    result = compare_reports(baseline, candidate)

    assert result.exit_code == BLOCK
    assert result.reasons == [
        "tl-01-catalog-note-fp passes pass^k in the baseline, not in the candidate (failed, 1/2)"
    ]


def test_a_scenario_failing_in_both_does_not_block(baseline: Report) -> None:
    failing = with_scenario(baseline, "tl-01-catalog-note-fp", pass_k=False, status="failed")

    assert compare_reports(failing, failing).exit_code == PASS


def test_a_pass_rate_drop_above_the_threshold_blocks(baseline: Report) -> None:
    before = with_suite(baseline, passes=9, runs=10)
    after = with_suite(baseline, passes=7, runs=10)

    result = compare_reports(before, after)

    assert result.exit_code == BLOCK
    assert result.reasons == ["trust-layers: the pass rate drops 20%, more than 10%"]


def test_a_drop_of_exactly_the_threshold_passes(baseline: Report) -> None:
    before = with_suite(baseline, passes=9, runs=10)
    after = with_suite(baseline, passes=8, runs=10)

    assert compare_reports(before, after).exit_code == PASS


def test_the_threshold_can_be_set(baseline: Report) -> None:
    before = with_suite(baseline, passes=9, runs=10)
    after = with_suite(baseline, passes=7, runs=10)

    assert compare_reports(before, after, max_pass_rate_drop=Fraction(3, 10)).exit_code == PASS
    assert compare_reports(before, after, max_pass_rate_drop=Fraction(0)).exit_code == BLOCK
