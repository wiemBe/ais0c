"""The scenario runner (T-030 criteria 5-7, agent-harness.md §2, §7).

Every selected scenario runs k times (default 5), `concurrency` runs at a time (default 2).
Each run gets a fresh nonce and the run ID `harness-<scenario_id>-<n>`. Its outcome is one of:

- `pass`: the run completed and every check held;
- `fail`: at least one check did not hold;
- `error`: no result (`failed`, `budget_exhausted`, a second infrastructure failure);
- `not_run`: the token ceiling stopped it.

An infrastructure failure (HTTP 429 or 5xx from LiteLLM, no HTTP answer, the wall clock budget)
is retried once as `harness-<scenario_id>-<n>-retry`, as production retries a Triage run that
ended without a decision (D-33, T-30); the first attempt stays in the record's `infra_retries`.
An output validation failure or a `budget_exhausted` run is the model's own and is not retried.

The token ceiling (default 3,000,000) counts the tokens of the runs that have finished, retries
included. Once it is reached no run starts: the rest are `not_run`. A retry of a run already
started still goes ahead.

The report lists the runs in the order of the suites, their scenarios and the run numbers, not
in the order they finished.
"""

import asyncio
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Final, Literal

from pydantic_ai.models import Model

from ais0c_contracts import Budget
from ais0c_harness.eval.adapter import AgentAdapter, Attempt, EvaluatorIdentity
from ais0c_harness.eval.config import AgentConfig, load_agent_config, sha256_file
from ais0c_harness.eval.evaluate import Evaluation, RunMetrics
from ais0c_harness.eval.report import (
    SCHEMA_VERSION,
    AgentReport,
    GitState,
    InfraRetry,
    Outcome,
    Report,
    RunEnvelope,
    RunFile,
    RunnerSettings,
    RunRecord,
    attempt_file,
    hard_gates,
    scenario_report,
    suite_report,
)
from ais0c_harness.eval.scenario import ScenarioBase
from ais0c_harness.eval.suites import ScenarioFile, Suite, SuiteError, adapter_type

DEFAULT_K: Final = 5
DEFAULT_CONCURRENCY: Final = 2
DEFAULT_MAX_TOTAL_TOKENS: Final = 3_000_000
INFRA_RETRY_DELAY_SECONDS: Final = 10.0
"""The pause before an infrastructure failure is retried, so a rate limit can clear."""
RUN_ID_PREFIX: Final = "harness"
RETRY_SUFFIX: Final = "-retry"

type ModelFactory = Callable[[AgentConfig, ScenarioBase], Model]
"""The model a scenario runs against; a real model ignores the scenario, a scripted one plays it."""


@dataclass(frozen=True)
class RunOptions:
    k: int = DEFAULT_K
    concurrency: int = DEFAULT_CONCURRENCY
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS
    retry_delay_seconds: float = INFRA_RETRY_DELAY_SECONDS
    wall_clock_seconds: float | None = None
    """The run's time limit; None is the manifest's `wall_clock_seconds` (tests set less)."""

    def __post_init__(self) -> None:
        if self.k < 1 or self.concurrency < 1 or self.max_total_tokens < 1:
            raise ValueError("k, concurrency and max_total_tokens must be positive")


@dataclass(frozen=True)
class Job:
    suite: Suite
    scenario: ScenarioFile
    number: int

    @property
    def run_id(self) -> str:
        return f"{RUN_ID_PREFIX}-{self.scenario.id}-{self.number}"


@dataclass(frozen=True)
class JobResult:
    job: Job
    outcome: Outcome
    error: str | None
    attempts: tuple[Attempt, ...]
    """Infrastructure failures first, the final attempt last; empty when the run did not run."""
    evaluation: Evaluation | None
    description: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def tokens_spent(self) -> int:
        return sum(attempt.tokens for attempt in self.attempts)


@dataclass(frozen=True)
class EvalRun:
    """A finished `run`: the report and each run's file."""

    report: Report
    files: list[RunFile]


def select(
    suites: Sequence[Suite], scenario_ids: Sequence[str] | None
) -> list[tuple[Suite, ScenarioFile]]:
    """The scenarios to run, in suite and file order; only `scenario_ids` when given.

    Raises SuiteError for an ID that is in none of the suites.
    """
    pairs = [(suite, scenario) for suite in suites for scenario in suite.scenarios]
    if scenario_ids is None:
        return pairs
    known = {scenario.id for _, scenario in pairs}
    if unknown := [scenario_id for scenario_id in scenario_ids if scenario_id not in known]:
        raise SuiteError(f"no scenario {', '.join(unknown)} in the selected suites")
    wanted = set(scenario_ids)
    return [(suite, scenario) for suite, scenario in pairs if scenario.id in wanted]


def load_adapters(
    root: Path, suites: Sequence[Suite], registry_path: Path
) -> dict[str, AgentAdapter]:
    """An adapter for every agent the suites run against, built from the worker's files."""
    adapters: dict[str, AgentAdapter] = {}
    for suite in suites:
        if suite.agent in adapters:
            continue
        kind = adapter_type(suite.agent)
        adapters[suite.agent] = kind(load_agent_config(root, kind.manifest_path, registry_path))
    return adapters


async def run_jobs(
    jobs: Sequence[Job],
    *,
    adapters: Mapping[str, AgentAdapter],
    models: Mapping[str, Model],
    options: RunOptions,
    on_result: Callable[[JobResult], None] | None = None,
) -> list[JobResult]:
    """Run every job; the result list is in the order of `jobs`. `models` holds each
    scenario's model, by scenario ID. `on_result` is called with each run's result the moment the
    run ends, `not_run` runs included, so a `run` stopped half way leaves the runs it finished."""
    results: list[JobResult | None] = [None] * len(jobs)
    spent = 0
    pending = iter(enumerate(jobs))

    async def worker() -> None:
        nonlocal spent
        for index, job in pending:
            if spent >= options.max_total_tokens:
                skipped = JobResult(
                    job=job,
                    outcome="not_run",
                    error=f"the token ceiling of {options.max_total_tokens:,} was reached",
                    attempts=(),
                    evaluation=None,
                )
                results[index] = skipped
                if on_result is not None:
                    on_result(skipped)
                continue
            adapter = adapters[job.suite.agent]
            model = models[job.scenario.id]
            result = await run_job(job, adapter=adapter, model=model, options=options)
            spent += result.tokens_spent
            results[index] = result
            if on_result is not None:
                on_result(result)

    await asyncio.gather(*(worker() for _ in range(options.concurrency)))
    return [result for result in results if result is not None]


async def run_job(
    job: Job, *, adapter: AgentAdapter, model: Model, options: RunOptions
) -> JobResult:
    """One run: an attempt, a retry after an infrastructure failure, and the evaluation."""
    time_limit = options.wall_clock_seconds or adapter.wall_clock_seconds
    scenario = job.scenario.scenario
    attempts: list[Attempt] = []
    try:
        attempts.append(
            await adapter.attempt(scenario, run_id=job.run_id, model=model, time_limit=time_limit)
        )
        if attempts[-1].infra_error is not None:
            await asyncio.sleep(options.retry_delay_seconds)
            attempts.append(
                await adapter.attempt(
                    scenario, run_id=job.run_id + RETRY_SUFFIX, model=model, time_limit=time_limit
                )
            )
        final = attempts[-1]
        evaluation = adapter.evaluate(scenario, final)
    except Exception as failure:  # noqa: BLE001 - one broken run must not lose the others
        return JobResult(
            job=job,
            outcome="error",
            error=f"harness error: {type(failure).__name__}: {failure}"[:1000],
            attempts=tuple(attempts),
            evaluation=None,
        )
    outcome: Outcome
    error: str | None = None
    if final.infra_error is not None:
        outcome, error = "error", f"infrastructure failure on the retry too: {final.error}"
    elif final.result is None:
        status = final.status.value if final.status is not None else "no status"
        outcome, error = "error", f"{status}: {final.error}"
    else:
        outcome = "pass" if evaluation.passed else "fail"
    return JobResult(
        job=job,
        outcome=outcome,
        error=error,
        attempts=tuple(attempts),
        evaluation=evaluation,
        description={} if final.result is None else adapter.describe(final.result),
    )


async def run_eval(
    *,
    root: Path,
    suites: Sequence[Suite],
    scenario_ids: Sequence[str] | None,
    registry_path: Path,
    model_factory: ModelFactory,
    options: RunOptions,
    on_run: Callable[[RunFile], None] | None = None,
) -> EvalRun:
    """Run the suites' scenarios k times each and build the report.

    `on_run` receives each run's file when the run ends (T-52 criterion 9), long before the
    report: `run` writes it at once, so an interrupted command keeps the runs it finished.

    Raises SuiteError for an unknown scenario ID and ConfigError (config.py) when an agent's
    files are invalid; both before any model call.
    """
    pairs = select(suites, scenario_ids)
    adapters = load_adapters(root, suites, registry_path)
    # Every model is built before the first run, so a settings error stops the command first.
    models = {
        scenario.id: model_factory(adapters[suite.agent].config, scenario.scenario)
        for suite, scenario in pairs
    }
    git = git_state(root)
    jobs = [
        Job(suite=suite, scenario=scenario, number=number)
        for suite, scenario in pairs
        for number in range(1, options.k + 1)
    ]

    def finished(result: JobResult) -> None:
        if on_run is not None:
            adapter = adapters[result.job.suite.agent]
            record = record_of(result, adapter=adapter, k=options.k, git=git)
            on_run(run_file_of(result, record))

    results = await run_jobs(
        jobs, adapters=adapters, models=models, options=options, on_result=finished
    )
    records = [
        record_of(result, adapter=adapters[result.job.suite.agent], k=options.k, git=git)
        for result in results
    ]
    settings = RunnerSettings(
        k=options.k,
        concurrency=options.concurrency,
        max_total_tokens=options.max_total_tokens,
        execution_mode=_execution_mode(pairs),
        registry=_display_path(registry_path, root),
        registry_sha256=sha256_file(registry_path),
        suites=[suite.id for suite in suites],
        scenarios=None if scenario_ids is None else list(scenario_ids),
    )
    report = build_report(
        settings=settings,
        git=git,
        suites=suites,
        pairs=pairs,
        results=results,
        records=records,
        adapters=adapters,
    )
    files = [run_file_of(result, record) for result, record in zip(results, records, strict=True)]
    return EvalRun(report=report, files=files)


def _execution_mode(
    pairs: Sequence[tuple[Suite, ScenarioFile]],
) -> Literal["fixture", "replay", "mixed"]:
    modes = {scenario.scenario.execution_mode() for _, scenario in pairs}
    if len(modes) > 1:
        return "mixed"
    return "replay" if modes == {"replay"} else "fixture"


def run_file_of(result: JobResult, record: RunRecord) -> RunFile:
    return RunFile(record=record, attempts=[attempt_file(attempt) for attempt in result.attempts])


def record_of(result: JobResult, *, adapter: AgentAdapter, k: int, git: GitState) -> RunRecord:
    job = result.job
    final = result.attempts[-1] if result.attempts else None
    retries = result.attempts[:-1]
    evaluation = result.evaluation
    return RunRecord(
        envelope=envelope(
            job,
            config=adapter.config,
            run_id=job.run_id if final is None else final.run_id,
            k=k,
            started_at=result.attempts[0].started_at if result.attempts else None,
            ended_at=None if final is None else final.ended_at,
            git=git,
            evaluator=adapter.evaluator(),
        ),
        outcome=result.outcome,
        error=result.error,
        status=None if final is None else final.status,
        result=None
        if final is None or final.result is None
        else final.result.model_dump(mode="json"),
        checks=[] if evaluation is None else evaluation.checks,
        metrics=RunMetrics(tokens=0 if final is None else final.tokens)
        if evaluation is None
        else evaluation.metrics,
        infra_retries=[
            InfraRetry(
                run_id=attempt.run_id,
                reason=attempt.infra_error or "",
                error=attempt.error,
                tokens=attempt.tokens,
                seconds=attempt.seconds,
            )
            for attempt in retries
        ],
    )


def envelope(
    job: Job,
    *,
    config: AgentConfig,
    run_id: str,
    k: int,
    started_at: datetime | None,
    ended_at: datetime | None,
    git: GitState,
    evaluator: EvaluatorIdentity | None = None,
) -> RunEnvelope:
    manifest = config.manifest
    return RunEnvelope(
        run_id=run_id,
        suite_id=job.suite.id,
        suite_version=job.suite.version,
        scenario_id=job.scenario.id,
        scenario_version=job.scenario.version,
        agent_id=manifest.id,
        agent_version=manifest.version,
        prompt_version=config.prompt.version,
        prompt_hash=config.prompt.sha256,
        shared_rules=config.prompt.shared_rules_path,
        model_alias=manifest.model_alias,
        model_release=config.model_release,
        toolset_profile=config.toolset_profile_name,
        toolset_sha256=config.toolset_sha256,
        execution_mode=job.scenario.scenario.execution_mode(),
        budget=Budget(
            tokens=manifest.budgets.tokens,
            tool_calls=manifest.budgets.tool_calls,
            seconds=manifest.budgets.wall_clock_seconds,
        ),
        k=k,
        run_number=job.number,
        started_at=started_at,
        ended_at=ended_at,
        git_commit=git.commit,
        git_dirty=git.dirty,
        evaluator_id=None if evaluator is None else evaluator.id,
        evaluator_version=None if evaluator is None else evaluator.version,
        evaluator_prompt_sha256=None if evaluator is None else evaluator.prompt_sha256,
        evaluator_model_alias=None if evaluator is None else evaluator.model_alias,
    )


def build_report(
    *,
    settings: RunnerSettings,
    git: GitState,
    suites: Sequence[Suite],
    pairs: Sequence[tuple[Suite, ScenarioFile]],
    results: Sequence[JobResult],
    records: Sequence[RunRecord],
    adapters: Mapping[str, AgentAdapter],
) -> Report:
    by_scenario: dict[str, list[tuple[JobResult, RunRecord]]] = {}
    for result, record in zip(results, records, strict=True):
        by_scenario.setdefault(result.job.scenario.id, []).append((result, record))
    scenarios = [
        scenario_report(
            suite_id=suite.id,
            kind=suite.kind,
            scenario_id=scenario.id,
            scenario_version=scenario.version,
            title=scenario.scenario.title,
            k=settings.k,
            runs=[record for _, record in by_scenario.get(scenario.id, [])],
            descriptions=[result.description for result, _ in by_scenario.get(scenario.id, [])],
        )
        for suite, scenario in pairs
    ]
    suite_reports = [
        suite_report(
            id=suite.id,
            title=suite.definition.title,
            kind=suite.kind,
            agent=suite.agent,
            version=suite.version,
            scenarios=[scenario for scenario in scenarios if scenario.suite_id == suite.id],
        )
        for suite in suites
    ]
    gates = hard_gates(scenarios, records)
    return Report(
        schema_version=SCHEMA_VERSION,
        created_at=datetime.now(UTC),
        settings=settings,
        git=git,
        agents=[agent_report(adapter.config) for adapter in adapters.values()],
        suites=suite_reports,
        scenarios=scenarios,
        runs=list(records),
        hard_gates=gates,
        passed=all(gate.passed for gate in gates),
        total_tokens=sum(record.tokens_spent for record in records),
    )


def agent_report(config: AgentConfig) -> AgentReport:
    manifest = config.manifest
    return AgentReport(
        agent_id=manifest.id,
        agent_version=manifest.version,
        prompt_version=config.prompt.version,
        prompt_hash=config.prompt.sha256,
        shared_rules=config.prompt.shared_rules_path,
        model_alias=manifest.model_alias,
        model_release=config.model_release,
        toolset_profile=config.toolset_profile_name,
        toolset_sha256=config.toolset_sha256,
    )


def git_state(root: Path) -> GitState:
    """The commit the harness runs from and whether the working tree has changes."""
    git = which("git")
    if git is None:
        return GitState(commit=None, dirty=None)
    try:
        commit = subprocess.run(  # noqa: S603 - fixed arguments, no input
            [git, "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(  # noqa: S603 - fixed arguments, no input
            [git, "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return GitState(commit=None, dirty=None)
    return GitState(commit=commit or None, dirty=bool(status.strip()))


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)
