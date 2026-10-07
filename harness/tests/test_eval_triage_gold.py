"""The runner plays every scenario of Triage Gold (T-059 criterion 2): a scripted model that
meets each expectation passes every scenario k = 2 times, and the report gives the suite's
quality metrics instead of `pass^k`."""

from .eval_helpers import fast, run, scripted, suite


def test_the_runner_plays_every_scenario_of_triage_gold() -> None:
    gold = suite("triage-gold")

    result = run([gold], scripted, options=fast(2, concurrency=2))

    report = result.report
    assert [summary.scenario_id for summary in report.scenarios] == [
        item.id for item in gold.scenarios
    ]
    assert len(report.scenarios) == 8
    for summary in report.scenarios:
        assert (summary.status, summary.passes, summary.k) == ("passed", 2, 2), summary.scenario_id
        assert summary.failed_checks == {}
        assert summary.kind == "quality"
    for record in result.report.runs:
        assert record.metrics.unscripted_calls == 0
        assert record.metrics.schema_invalid_intents == 0
        assert record.metrics.tool_calls > 0
    quality = report.suites[0].quality
    assert {name: rate.rate for name, rate in quality.items()} == {
        "decision_accuracy": 1.0,
        "level_accuracy": 1.0,
        "data_gap_rate": 1.0,
    }
    assert (quality["decision_accuracy"].runs, quality["level_accuracy"].runs) == (16, 14)
    assert quality["data_gap_rate"].runs == 2
    assert report.passed
