"""The runner plays every scenario of both security suites (T-030 criteria 12 and 13): a scripted
model that meets each expectation passes every scenario k = 2 times."""

from .eval_helpers import fast, run, scripted, suite


def test_the_runner_plays_every_scenario_of_both_suites() -> None:
    suites = [suite("trust-layers"), suite("adversarial-fn")]

    result = run(
        suites,
        scripted,
        options=fast(2, concurrency=2),
    )

    report = result.report
    scenario_ids = [item.id for each in suites for item in each.scenarios]
    assert len(scenario_ids) == 8
    assert [summary.scenario_id for summary in report.scenarios] == scenario_ids
    for summary in report.scenarios:
        assert (summary.status, summary.passes, summary.k) == ("passed", 2, 2), summary.scenario_id
        assert summary.failed_checks == {}
    for record in report.runs:
        # Every scripted call reached its result: nothing unscripted, nothing denied.
        assert record.metrics.unscripted_calls == 0
        assert record.metrics.schema_invalid_intents == 0
        assert record.metrics.tool_calls > 0
    assert {suite_report.id: suite_report.passing_scenarios for suite_report in report.suites} == {
        "trust-layers": 3,
        "adversarial-fn": 5,
    }
    assert report.passed
    assert len(result.files) == 16
