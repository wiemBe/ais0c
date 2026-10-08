"""The runner (T-030 criteria 5 and 6): k runs, pass^k, infrastructure retries, the quality
suite's pass rate, the report's order and the token ceiling."""

import asyncio
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from ais0c_contracts import RunStatus
from ais0c_harness.eval import (
    AgentConfig,
    Attempt,
    Evaluation,
    Job,
    Report,
    ScenarioBase,
    TriageAdapter,
    infra_failure,
    load_suite,
)
from ais0c_harness.eval.runner import (
    TRACEBACK_LINES,
    JobResult,
    git_state,
    record_of,
    run_file_of,
    run_job,
)

from .eval_helpers import (
    REPO_ROOT,
    answer_of,
    fast,
    first_request,
    run,
    scenario,
    suite,
    triage_config,
    varying_model,
    write_suite,
)

TL01 = "tl-01-catalog-note-fp"
PLAYED = scenario(TL01)
NONCE = re.compile(r"<untrusted_([0-9a-f]+) ")


def gate(report: Report, gate_id: str) -> tuple[bool, bool]:
    found = next(item for item in report.hard_gates if item.id == gate_id)
    return found.applies, found.passed


def only(model: Model):  # noqa: ANN201 - a ModelFactory
    def factory(config: AgentConfig, played: ScenarioBase) -> Model:
        return model

    return factory


# --- k runs and pass^k --------------------------------------------------------------------------


def test_one_fp_in_five_runs_fails_pass_k_with_a_pass_rate_of_0_8() -> None:
    model = varying_model(PLAYED, [None, None, {"verdict": "fp"}, None, None])

    report = run([suite("trust-layers")], only(model), options=fast(5), scenario_ids=[TL01]).report

    [summary] = report.scenarios
    assert summary.outcomes == {"pass": 4, "fail": 1, "error": 0, "not_run": 0}
    assert (summary.pass_rate, summary.pass_k, summary.status) == (0.8, False, "failed")
    assert summary.distributions["verdict"] == {"fp": 1, "suspicious": 4}
    assert summary.failed_checks == {"verdict_in": 1}
    assert [record.outcome for record in report.runs] == ["pass", "pass", "fail", "pass", "pass"]
    assert [record.envelope.run_number for record in report.runs] == [1, 2, 3, 4, 5]
    assert gate(report, "security_pass_k") == (True, False)
    assert not report.passed
    [suite_summary] = report.suites
    assert (suite_summary.passes, suite_summary.runs, suite_summary.passing_scenarios) == (4, 5, 0)


def test_k_passing_runs_pass_pass_k() -> None:
    model = varying_model(PLAYED, [None])

    report = run([suite("trust-layers")], only(model), options=fast(5), scenario_ids=[TL01]).report

    [summary] = report.scenarios
    assert (summary.pass_rate, summary.pass_k, summary.status) == (1.0, True, "passed")
    assert report.passed


# --- infrastructure failures --------------------------------------------------------------------


def test_a_503_is_retried_once_and_the_run_passes() -> None:
    model = varying_model(PLAYED, [None], failures={0: 503})

    report = run([suite("trust-layers")], only(model), options=fast(1), scenario_ids=[TL01]).report

    [record] = report.runs
    assert record.outcome == "pass"
    assert record.envelope.run_id == f"harness-{TL01}-1-retry"
    [retry] = record.infra_retries
    assert (retry.run_id, retry.reason) == (f"harness-{TL01}-1", "HTTP 503")
    assert report.scenarios[0].infra_retries == 1
    assert report.scenarios[0].pass_k


def test_a_second_503_makes_the_run_an_error_and_the_scenario_fails() -> None:
    model = varying_model(PLAYED, [None], failures={0: 503, 1: 503})

    report = run([suite("trust-layers")], only(model), options=fast(1), scenario_ids=[TL01]).report

    [record] = report.runs
    assert record.outcome == "error"
    assert record.error is not None
    assert record.error.startswith("infrastructure failure on the retry too")
    assert len(record.infra_retries) == 1
    assert (report.scenarios[0].pass_k, report.scenarios[0].status) == (False, "failed")
    assert not report.passed


def test_a_400_is_the_model_side_and_not_retried() -> None:
    model = varying_model(PLAYED, [None], failures={0: 400})

    report = run([suite("trust-layers")], only(model), options=fast(1), scenario_ids=[TL01]).report

    [record] = report.runs
    assert (record.outcome, record.status, record.infra_retries) == ("error", RunStatus.FAILED, [])


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ModelHTTPError(status_code=429, model_name="soc-fast"), "HTTP 429"),
        (ModelHTTPError(status_code=500, model_name="soc-fast"), "HTTP 500"),
        (ModelHTTPError(status_code=503, model_name="soc-fast"), "HTTP 503"),
        (
            ModelAPIError(model_name="soc-fast", message="Connection error."),
            "no HTTP answer: Connection error.",
        ),
        (ModelHTTPError(status_code=400, model_name="soc-fast"), None),
        (ModelHTTPError(status_code=404, model_name="soc-fast"), None),
        (ValueError("not the infrastructure"), None),
        (None, None),
    ],
)
def test_infrastructure_failures_are_429_5xx_and_no_http_answer(
    error: Exception | None, reason: str | None
) -> None:
    assert infra_failure(error) == reason


def test_a_timeout_is_retried_once() -> None:
    inner = varying_model(PLAYED, [None])
    first_nonce: str | None = None

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal first_nonce
        match = NONCE.search(info.instructions or "")
        assert match is not None
        if first_nonce is None:
            first_nonce = match[1]
        if match[1] == first_nonce and not first_request(messages):
            await asyncio.sleep(5)
        return answer_of(inner, messages, info)

    options = fast(1, wall_clock_seconds=0.5)
    report = run(
        [suite("trust-layers")], only(FunctionModel(respond)), options=options, scenario_ids=[TL01]
    ).report

    [record] = report.runs
    assert record.outcome == "pass"
    [retry] = record.infra_retries
    assert retry.reason == "timeout"


def test_an_output_validation_failure_is_an_error_without_a_retry() -> None:
    def chatty(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"verdict": "maybe"})])

    report = run(
        [suite("trust-layers")], only(FunctionModel(chatty)), options=fast(1), scenario_ids=[TL01]
    ).report

    [record] = report.runs
    assert (record.outcome, record.status, record.infra_retries) == ("error", RunStatus.FAILED, [])
    assert record.error is not None
    assert "output retries" in record.error


class _CannedAdapter(TriageAdapter):
    """Answers every attempt with one canned status and no result."""

    status: RunStatus | None = RunStatus.BUDGET_EXHAUSTED
    attempts: int = 0

    async def attempt(
        self, scenario: ScenarioBase, *, run_id: str, model: Model, time_limit: float
    ) -> Attempt:
        type(self).attempts += 1
        now = datetime.now(UTC)
        return Attempt(
            run_id=run_id,
            started_at=now,
            ended_at=now,
            status=self.status,
            result=None,
            error="UsageLimitExceeded: the token budget is used up",
            infra_error=None,
            messages=[],
            exchanges=[],
            tokens=150_000,
            seconds=1.0,
        )


def test_a_budget_exhausted_run_is_an_error_without_a_retry() -> None:
    trust = suite("trust-layers")
    job = Job(suite=trust, scenario=trust.scenarios[0], number=1)
    _CannedAdapter.attempts = 0

    result: JobResult = asyncio.run(
        run_job(job, adapter=_CannedAdapter(triage_config()), model=TestModel(), options=fast())
    )

    assert result.outcome == "error"
    assert result.error == "budget_exhausted: UsageLimitExceeded: the token budget is used up"
    assert _CannedAdapter.attempts == 1


class _BrokenAdapter(TriageAdapter):
    async def attempt(
        self, scenario: ScenarioBase, *, run_id: str, model: Model, time_limit: float
    ) -> Attempt:
        raise LookupError("a harness bug")


def test_a_harness_error_is_recorded_as_the_run_error() -> None:
    trust = suite("trust-layers")
    job = Job(suite=trust, scenario=trust.scenarios[0], number=1)

    result = asyncio.run(
        run_job(job, adapter=_BrokenAdapter(triage_config()), model=TestModel(), options=fast())
    )

    assert (result.outcome, result.error) == ("error", "harness error: LookupError: a harness bug")


def _descend(depth: int) -> int:
    return _descend(depth + 1)


class _RecursingAdapter(_CannedAdapter):
    def evaluate(self, scenario: ScenarioBase, attempt: Attempt) -> Evaluation:
        _descend(0)
        raise AssertionError("unreachable")


def test_a_harness_error_keeps_its_traceback() -> None:
    trust = suite("trust-layers")
    job = Job(suite=trust, scenario=trust.scenarios[0], number=1)
    adapter = _RecursingAdapter(triage_config())

    result = asyncio.run(run_job(job, adapter=adapter, model=TestModel(), options=fast()))
    record = record_of(result, adapter=adapter, k=1, git=git_state(REPO_ROOT))
    run_file = run_file_of(result, record)

    assert result.outcome == "error"
    assert result.error is not None
    assert result.error.startswith("harness error: RecursionError: maximum recursion depth")
    assert run_file.error_traceback is not None
    assert "RecursionError" in run_file.error_traceback
    assert "in _descend" in run_file.error_traceback
    assert len(run_file.error_traceback.splitlines()) <= TRACEBACK_LINES
    # The report stays as it was: the traceback is only in the run's file.
    assert "error_traceback" not in record.model_dump()
    assert "in _descend" not in record.model_dump_json()
    assert "error_traceback" in json.loads(run_file.model_dump_json())


# --- quality suites -----------------------------------------------------------------------------


def test_a_quality_suite_reports_its_pass_rate_without_pass_k(tmp_path: Path) -> None:
    directory = write_suite(
        tmp_path, suite_id="quality", kind="quality", prefix="q-", sources=[TL01]
    )
    quality = load_suite(directory, root=REPO_ROOT)
    played = quality.scenarios[0].scenario
    assert isinstance(played, type(PLAYED))
    model = varying_model(played, [None, {"verdict": "fp"}, None, None, None])

    report = run([quality], only(model), options=fast(5)).report

    [summary] = report.scenarios
    assert (summary.kind, summary.pass_rate, summary.pass_k) == ("quality", 0.8, False)
    assert gate(report, "security_pass_k") == (False, True)
    assert report.passed
    assert report.suites[0].pass_rate == 0.8


# --- order and concurrency ----------------------------------------------------------------------


def test_runs_are_reported_by_run_number_not_by_completion() -> None:
    inner = varying_model(PLAYED, [None])
    slow_nonce: str | None = None

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal slow_nonce
        match = NONCE.search(info.instructions or "")
        assert match is not None
        if slow_nonce is None:
            slow_nonce = match[1]
        if match[1] == slow_nonce:
            await asyncio.sleep(0.2)
        return answer_of(inner, messages, info)

    options = fast(2, concurrency=2)
    report = run(
        [suite("trust-layers")], only(FunctionModel(respond)), options=options, scenario_ids=[TL01]
    ).report

    first, second = report.runs
    assert (first.envelope.run_number, second.envelope.run_number) == (1, 2)
    assert first.envelope.ended_at is not None
    assert second.envelope.ended_at is not None
    assert first.envelope.ended_at > second.envelope.ended_at
    assert {first.outcome, second.outcome} == {"pass"}


# --- the token ceiling --------------------------------------------------------------------------


def test_the_token_ceiling_stops_new_runs_and_leaves_the_scenario_incomplete() -> None:
    model = varying_model(PLAYED, [None])
    options = replace(fast(3), max_total_tokens=1)

    report = run([suite("trust-layers")], only(model), options=options, scenario_ids=[TL01]).report

    assert [record.outcome for record in report.runs] == ["pass", "not_run", "not_run"]
    skipped = report.runs[1]
    assert skipped.error == "the token ceiling of 1 was reached"
    assert (skipped.envelope.started_at, skipped.status, skipped.result) == (None, None, None)
    assert skipped.envelope.run_id == f"harness-{TL01}-2"
    [summary] = report.scenarios
    assert (summary.status, summary.pass_k) == ("incomplete", False)
    assert summary.outcomes["not_run"] == 2
    assert gate(report, "completeness") == (True, False)
    assert not report.passed
    assert report.total_tokens == report.runs[0].metrics.tokens > 1


def test_a_ceiling_above_the_spend_runs_everything() -> None:
    model = varying_model(PLAYED, [None])

    report = run(
        [suite("trust-layers")],
        only(model),
        options=replace(fast(3), max_total_tokens=10**9),
        scenario_ids=[TL01],
    ).report

    assert [record.outcome for record in report.runs] == ["pass", "pass", "pass"]
