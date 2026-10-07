"""`python -m ais0c_harness.eval`: list suites, run them, compare reports, list changed model
releases, write a scenario draft from a recorded run (T-030 criterion 11, T-053 criterion 5),
record a lab offense (T-052).

    list     [--suite ID ...]
    run      --suite ID [--suite ID ...] [--scenario ID ...] --out DIR
             [--k 5] [--registry FILE] [--concurrency 2] [--max-total-tokens 3000000]
    gate     --baseline REPORT --candidate REPORT [--max-pass-rate-drop 0.10]
    releases [--registry FILE]
    scenario --run RUN_ID --suite ID --id SCENARIO_ID --out FILE [--kind orchestrator|reporting|turkish]
    record   --offense ID --out harness/recordings/<id> [--domain NAME ...] [--host NAME ...]
             [--exclude-type NAME ... | --keep-all-types]

`record` reads a closed lab offense through the dev stack's gateway (AIS0C_GATEWAY_URL,
AIS0C_WORKER_SECRETS_DIR, AIS0C_DATABASE_URL) and writes nothing to the lab; `run` writes each
run's file `runs/<scenario>/<n>.json` as the run ends.

`--root` (default: the current directory) is the repository root with `config/`, `prompts/`
and `harness/suites/`. `run` needs LITELLM_API_KEY; LITELLM_BASE_URL defaults to
http://127.0.0.1:4000. `releases` and `scenario` read the database at AIS0C_DATABASE_URL (the
latter read-only).

Exit codes: `run` 0 when every hard gate passes, 1 when one fails; `record` 0 when the recording
was written, 1 when it could not be made (the replay engine disagrees with the lab, an address
survived anonymization); `gate` 0 pass, 1 block,
2 not comparable; `releases` 0 when no release changed, 1 when one did; every command 2 for a
setting, file or argument error. Errors go to stderr and never repeat environment values.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, TextIO

from pydantic import ValidationError
from pydantic_ai.models import Model

from ais0c_activities.db import SessionFactory
from ais0c_activities.model_release import (
    ModelReleaseChange,
    ModelReleaseError,
    load_model_releases,
    model_release_changes,
)
from ais0c_agents import LITELLM_API_KEY_ENV, ModelConfigError
from ais0c_contracts import ModelRelease
from ais0c_harness.eval.config import (
    DEFAULT_REGISTRY,
    AgentConfig,
    ConfigError,
    agent_aliases,
    litellm_model,
)
from ais0c_harness.eval.from_records import build_scenario
from ais0c_harness.eval.gate import DEFAULT_MAX_PASS_RATE_DROP, compare_reports, parse_drop
from ais0c_harness.eval.releases import agents_by_alias, describe_release_changes
from ais0c_harness.eval.report import REPORT_JSON, load_report, write_report, write_run_file
from ais0c_harness.eval.runner import (
    DEFAULT_CONCURRENCY,
    DEFAULT_K,
    DEFAULT_MAX_TOTAL_TOKENS,
    INFRA_RETRY_DELAY_SECONDS,
    RunOptions,
    run_eval,
)
from ais0c_harness.eval.scenario import ScenarioBase
from ais0c_harness.eval.suites import SuiteError, load_suites
from ais0c_harness.replay.record import (
    DEFAULT_EXCLUDED,
    RecordError,
    record_offense,
)
from ais0c_harness.replay.recording import RecordingManifest
from ais0c_storage import ConfigurationError, create_engine, create_session_factory, database_url

OK: Final = 0
FAILED: Final = 1
SETTINGS_ERROR: Final = 2

type ReleaseChanges = Callable[
    [SessionFactory, Mapping[str, ModelRelease]], Awaitable[list[ModelReleaseChange]]
]
type Recorder = Callable[..., Coroutine[Any, Any, RecordingManifest]]


@dataclass(frozen=True)
class Dependencies:
    """What the commands reach outside the process; tests replace them."""

    model_factory: Callable[[AgentConfig, ScenarioBase, Mapping[str, str]], Model] = (
        lambda config, _scenario, env: litellm_model(config, env)
    )
    release_changes: ReleaseChanges = model_release_changes
    recorder: Recorder = record_offense
    retry_delay_seconds: float = INFRA_RETRY_DELAY_SECONDS


class _UsageError(Exception):
    """A command-line or settings error: exit SETTINGS_ERROR."""


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    deps: Dependencies | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    env = os.environ if environ is None else environ
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    dependencies = Dependencies() if deps is None else deps
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return OK if exit_.code in (0, None) else SETTINGS_ERROR
    root = Path(args.root)
    try:
        match args.command:
            case "list":
                return _list(root, args.suite, out)
            case "run":
                return _run(root, args, env, dependencies, out)
            case "gate":
                return _gate(args, out)
            case "releases":
                return _releases(root, args, env, dependencies, out)
            case "scenario":
                return _scenario(root, args, env, out)
            case "record":
                return _record(root, args, env, dependencies, out, err)
            case _:  # pragma: no cover - argparse requires a command
                parser.error("no command")
    except (_UsageError, SuiteError, ConfigError, ModelConfigError, ValueError) as error:
        print(f"error: {error}", file=err)
        return SETTINGS_ERROR


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ais0c_harness.eval")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    commands = parser.add_subparsers(dest="command", required=True)

    list_ = commands.add_parser("list", help="list suites, scenarios and their versions")
    list_.add_argument("--suite", action="append", help="only this suite (repeatable)")

    run = commands.add_parser("run", help="run suites k times against the model")
    run.add_argument("--suite", action="append", required=True, help="suite ID (repeatable)")
    run.add_argument("--scenario", action="append", help="only this scenario (repeatable)")
    run.add_argument("--out", required=True, help="output directory; must be empty")
    run.add_argument("--k", type=_positive, default=DEFAULT_K)
    run.add_argument("--registry", default=DEFAULT_REGISTRY, help="model registry file")
    run.add_argument("--concurrency", type=_positive, default=DEFAULT_CONCURRENCY)
    run.add_argument("--max-total-tokens", type=_positive, default=DEFAULT_MAX_TOTAL_TOKENS)

    gate = commands.add_parser("gate", help="compare a candidate report with a baseline")
    gate.add_argument("--baseline", required=True)
    gate.add_argument("--candidate", required=True)
    gate.add_argument(
        "--max-pass-rate-drop",
        type=_drop,
        default=DEFAULT_MAX_PASS_RATE_DROP,
        help="largest allowed drop of a suite's pass rate (default 0.10)",
    )

    releases = commands.add_parser(
        "releases", help="list model releases changed since the last runs"
    )
    releases.add_argument("--registry", default=DEFAULT_REGISTRY, help="model registry file")

    scenario = commands.add_parser(
        "scenario", help="write a scenario draft from a recorded dev chain run"
    )
    scenario.add_argument("--run", required=True, help="the agent_runs.run_id of a chain run")
    scenario.add_argument("--suite", required=True, help="the suite the scenario joins")
    scenario.add_argument("--id", required=True, help="the scenario ID (its file's name)")
    scenario.add_argument(
        "--kind",
        choices=["orchestrator", "reporting", "turkish"],
        default=None,
        help="which suite the draft is for (default: the run's own agent)",
    )
    scenario.add_argument("--out", required=True, help="the file to write")
    record = commands.add_parser("record", help="record a closed lab offense for replay")
    record.add_argument("--offense", type=_positive, required=True)
    record.add_argument("--out", required=True, help="harness/recordings/<recording-id>")
    record.add_argument("--domain", action="append", default=[], help="a lab domain name")
    record.add_argument("--host", action="append", default=[], help="a lab host name")
    exclude = record.add_mutually_exclusive_group()
    exclude.add_argument(
        "--exclude-type",
        action="append",
        help=f"leave out this log source type (repeatable; default {', '.join(DEFAULT_EXCLUDED)})",
    )
    exclude.add_argument("--keep-all-types", action="store_true")
    return parser


def _list(root: Path, suite_ids: list[str] | None, out: TextIO) -> int:
    for suite in load_suites(root, suite_ids):
        print(
            f"{suite.id}  {suite.kind}  agent {suite.agent}  version {suite.version}  "
            f"{len(suite.scenarios)} scenarios",
            file=out,
        )
        for scenario in suite.scenarios:
            print(f"  {scenario.id}  {scenario.version}  {scenario.scenario.title}", file=out)
    return OK


def _run(
    root: Path,
    args: argparse.Namespace,
    env: Mapping[str, str],
    deps: Dependencies,
    out: TextIO,
) -> int:
    if not env.get(LITELLM_API_KEY_ENV, "").strip():
        raise _UsageError(f"{LITELLM_API_KEY_ENV} is not set")
    directory = Path(args.out)
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise _UsageError(f"{directory} is not an empty directory; a report is never overwritten")
    registry = _under(root, args.registry)
    if not registry.is_file():
        raise _UsageError(f"no model registry {registry}")
    suites = load_suites(root, args.suite)
    options = RunOptions(
        k=args.k,
        concurrency=args.concurrency,
        max_total_tokens=args.max_total_tokens,
        retry_delay_seconds=deps.retry_delay_seconds,
    )
    result = asyncio.run(
        run_eval(
            root=root,
            suites=suites,
            scenario_ids=args.scenario,
            registry_path=registry,
            model_factory=lambda config, scenario: deps.model_factory(config, scenario, env),
            options=options,
            on_run=lambda run_file: write_run_file(directory, run_file),
        )
    )
    write_report(directory, result.report, [])
    report = result.report
    print(f"report: {directory / REPORT_JSON}", file=out)
    for gate in report.hard_gates:
        state = "n/a" if not gate.applies else "pass" if gate.passed else "FAIL"
        print(f"  {gate.id}: {state} ({gate.detail})", file=out)
    print(f"{'passed' if report.passed else 'failed'}; {report.total_tokens:,} tokens", file=out)
    return OK if report.passed else FAILED


def _gate(args: argparse.Namespace, out: TextIO) -> int:
    reports = []
    for path in (args.baseline, args.candidate):
        try:
            reports.append(load_report(Path(path)))
        except (OSError, ValidationError) as error:
            raise _UsageError(f"cannot read the report {path}: {type(error).__name__}") from None
    baseline, candidate = reports
    result = compare_reports(baseline, candidate, max_pass_rate_drop=args.max_pass_rate_drop)
    print(result.text, end="", file=out)
    return result.exit_code


def _releases(
    root: Path,
    args: argparse.Namespace,
    env: Mapping[str, str],
    deps: Dependencies,
    out: TextIO,
) -> int:
    try:
        url = database_url(env)
        current = load_model_releases(_under(root, args.registry))
    except (ConfigurationError, ModelReleaseError) as error:
        raise _UsageError(str(error)) from None
    agents = agents_by_alias(agent_aliases(root), load_suites(root))

    async def changes() -> list[ModelReleaseChange]:
        engine = create_engine(url)
        try:
            return await deps.release_changes(create_session_factory(engine), current)
        finally:
            await engine.dispose()

    found = asyncio.run(changes())
    print(describe_release_changes(found, agents), end="", file=out)
    return FAILED if found else OK


def _scenario(
    root: Path,
    args: argparse.Namespace,
    env: Mapping[str, str],
    out: TextIO,
) -> int:
    """Write a scenario draft from a recorded dev chain run (T-053 criterion 5)."""
    from ais0c_storage import ConfigurationError

    try:
        url = database_url(env)
    except ConfigurationError as error:
        raise _UsageError(str(error)) from None
    kind = args.kind or "reporting"

    async def build() -> tuple[str, str]:
        engine = create_engine(url)
        try:
            session_factory = create_session_factory(engine)
            async with session_factory() as session:
                return await build_scenario(
                    session,
                    root=root,
                    run_id=args.run,
                    suite=args.suite,
                    scenario_id=args.id,
                    kind=kind,
                )
        finally:
            await engine.dispose()

    text, summary = asyncio.run(build())
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    print(f"{destination}: {summary}", file=out)
    return OK


def _record(
    root: Path,
    args: argparse.Namespace,
    env: Mapping[str, str],
    deps: Dependencies,
    out: TextIO,
    err: TextIO,
) -> int:
    names = {"AIS0C_GATEWAY_URL": "", "AIS0C_WORKER_SECRETS_DIR": "", "AIS0C_DATABASE_URL": ""}
    for name in names:
        names[name] = env.get(name, "").strip()
    if missing := [name for name, value in names.items() if not value]:
        raise _UsageError(f"set {', '.join(missing)} to record from the lab")
    directory = _under(root, args.out)
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise _UsageError(
            f"{directory} is not an empty directory; a recording is never overwritten"
        )
    excluded = [] if args.keep_all_types else args.exclude_type or list(DEFAULT_EXCLUDED)
    try:
        manifest = asyncio.run(
            deps.recorder(
                root=root,
                offense_id=args.offense,
                directory=directory,
                gateway_url=names["AIS0C_GATEWAY_URL"],
                secrets_dir=Path(names["AIS0C_WORKER_SECRETS_DIR"]),
                database_url=names["AIS0C_DATABASE_URL"],
                domains=args.domain,
                hosts=args.host,
                excluded=excluded,
            )
        )
    except RecordError as error:
        print(f"error: {error}", file=err)
        return FAILED
    print(
        f"recording {manifest.recording_id}: offense {manifest.offense_id}, "
        f"{manifest.events} events, {len(manifest.files)} files in {directory}",
        file=out,
    )
    return OK


def _under(root: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _drop(value: str) -> Fraction:
    try:
        return parse_drop(value)
    except (ValueError, ZeroDivisionError) as error:
        raise argparse.ArgumentTypeError(str(error)) from None
