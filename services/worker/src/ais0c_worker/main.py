"""The worker processes and deployment commands of ``python -m ais0c_worker``.

| Variable | Meaning | Default |
|---|---|---|
| `TEMPORAL_ADDRESS` | Temporal frontend | `127.0.0.1:7233` |
| `TEMPORAL_NAMESPACE` | Namespace | `default` |
| `AIS0C_INTAKE_SCHEDULE` | `off` leaves the intake Schedule alone, e.g. on a second worker | `on` |
| `AIS0C_KNOWLEDGE_SYNC_SCHEDULE` | `off` leaves the KnowledgeSync Schedule alone | `on` |
| `AIS0C_HEALTH_SCHEDULE` | `off` leaves the HealthCheck Schedule alone, e.g. on a second batch worker | `on` |

Without a command the process is the case worker, as it has been: `run_case_worker` reads the
offenses and runs the agent chain of each case (`ais0c_worker.case_worker`). With the command `batch` it is
the batch worker of the `soc-batch` queue, `run_batch_worker`, which runs KnowledgeSync's
catalog sync and the HealthCheck workflow's checks (T-032), and warns at start-up when
`AIS0C_ALARM_SYSLOG_HOST` is unset, so alarms reach QRadar by no syslog. With the command `executor` it is the executor worker of the `soc-executor` queue,
`run_executor_worker`, which runs only the QRadar note and the alert e-mail, with the executor's
own secrets (T-33 (1), T-045): no agent token, no model, no Schedule.

The database, the gateway, LiteLLM and the configuration files are the runtime's settings
(`ais0c_activities.runtime`); the batch worker needs only the database and the gateway, the
executor worker the database, the note profile's gateway token and the SMTP relay. Either
process stops on SIGINT or SIGTERM. Work it leaves unfinished is not lost: the next worker
continues each workflow from its history.

The case worker warns at start-up about every model alias whose release in the model registry
differs from the one its last agent run recorded: a new model release, for which the model gate
must run again (T-24, docs/agent-harness.md §5, B2). The batch and executor workers call no
model and have no warning.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Final

from ais0c_activities import (
    ModelRelease,
    ModelReleaseChange,
    RuntimeConfigError,
    SessionFactory,
    deploy,
    load_batch_runtime,
    load_case_runtime,
    load_executor_runtime,
    model_release_changes,
)
from ais0c_worker import preflight
from ais0c_worker.batch_worker import build_batch_worker
from ais0c_worker.case_worker import build_case_worker, connect
from ais0c_worker.executor_worker import build_executor_worker
from ais0c_worker.schedule import (
    ensure_health_check_schedule,
    ensure_intake_schedule,
    ensure_knowledge_sync_schedule,
)

TEMPORAL_ADDRESS_ENV: Final = "TEMPORAL_ADDRESS"
TEMPORAL_NAMESPACE_ENV: Final = "TEMPORAL_NAMESPACE"
INTAKE_SCHEDULE_ENV: Final = "AIS0C_INTAKE_SCHEDULE"
KNOWLEDGE_SYNC_SCHEDULE_ENV: Final = "AIS0C_KNOWLEDGE_SYNC_SCHEDULE"
HEALTH_SCHEDULE_ENV: Final = "AIS0C_HEALTH_SCHEDULE"

CASE_COMMAND: Final = "case"
BATCH_COMMAND: Final = "batch"
EXECUTOR_COMMAND: Final = "executor"
MIGRATE_COMMAND: Final = "migrate"
PREFLIGHT_COMMAND: Final = "preflight"

# A setting, a file or a secret the worker needs is missing or invalid.
EXIT_CONFIG_ERROR: Final = 2
EXIT_DATABASE_ERROR: Final = 1
EXIT_DATABASE_BEHIND: Final = 3

type Run = Callable[[asyncio.Event, Mapping[str, str]], Awaitable[None]]

logger = logging.getLogger("ais0c.worker")


async def run_case_worker(stop: asyncio.Event, environ: Mapping[str, str] | None = None) -> None:
    """Run the case worker until `stop` is set; `environ` defaults to `os.environ`."""
    env = os.environ if environ is None else environ
    runtime = await load_case_runtime(env)
    try:
        await warn_on_model_release_changes(runtime.sessions, runtime.model_releases)
        client = await connect(_address(env), namespace=_namespace(env))
        if not _schedule_left_alone(env, INTAKE_SCHEDULE_ENV):
            await ensure_intake_schedule(client)
        worker = build_case_worker(
            client,
            sessions=runtime.sessions,
            source=runtime.source,
            triage=runtime.triage,
            chain=runtime.chain,
            settings=runtime.settings,
            ioc_matcher=runtime.ioc_matcher,
        )
        async with worker:
            logger.info("case worker running")
            await stop.wait()
        logger.info("case worker stopped")
    finally:
        await runtime.close()


async def run_batch_worker(stop: asyncio.Event, environ: Mapping[str, str] | None = None) -> None:
    """Run the `soc-batch` worker until `stop` is set; `environ` defaults to `os.environ`.

    Raises `RuntimeConfigError` before it runs anything when the runtime cannot be built: the
    inventory token is missing, the gateway serves another profile than the sync and the health
    checks read with, or a health setting is invalid.
    """
    env = os.environ if environ is None else environ
    runtime = await load_batch_runtime(env)
    try:
        if runtime.syslog is None:
            logger.warning(
                "health alarms go out by e-mail only: AIS0C_ALARM_SYSLOG_HOST is not set, so no "
                "syslog message reaches QRadar (T-23, T-032)"
            )
        client = await connect(_address(env), namespace=_namespace(env))
        if not _schedule_left_alone(env, KNOWLEDGE_SYNC_SCHEDULE_ENV):
            await ensure_knowledge_sync_schedule(client)
        if not _schedule_left_alone(env, HEALTH_SCHEDULE_ENV):
            await ensure_health_check_schedule(client, every=runtime.health.interval)
        async with build_batch_worker(client, runtime):
            logger.info("batch worker running")
            await stop.wait()
        logger.info("batch worker stopped")
    finally:
        await runtime.close()


async def run_executor_worker(
    stop: asyncio.Event, environ: Mapping[str, str] | None = None
) -> None:
    """Run the `soc-executor` worker until `stop` is set; `environ` defaults to `os.environ`.

    Raises `RuntimeConfigError` before it runs anything when the runtime cannot be built: a
    missing executor secret, an invalid SMTP setting, or a gateway that does not serve the note
    profile (T-045 criterion 1).
    """
    env = os.environ if environ is None else environ
    runtime = await load_executor_runtime(env)
    try:
        client = await connect(_address(env), namespace=_namespace(env))
        async with build_executor_worker(client, runtime):
            logger.info("executor worker running")
            await stop.wait()
        logger.info("executor worker stopped")
    finally:
        await runtime.close()


async def warn_on_model_release_changes(
    sessions: SessionFactory, releases: Mapping[str, ModelRelease]
) -> list[ModelReleaseChange]:
    """Log a warning for every alias whose release in `releases` differs from the one its last
    agent run recorded; returns those changes."""
    changes = await model_release_changes(sessions, releases)
    for change in changes:
        logger.warning(
            "model release of %s differs from the one its last agent run recorded (%s); "
            "the model gate must run again (docs/agent-harness.md §5, B2)",
            change.alias,
            change.describe(),
        )
    return changes


_RUNNERS: Final[Mapping[str, Run]] = {
    CASE_COMMAND: run_case_worker,
    BATCH_COMMAND: run_batch_worker,
    EXECUTOR_COMMAND: run_executor_worker,
}


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run the worker the command line names; returns the exit status.

    Without a command it is the case worker, `batch` the batch worker, `executor` the executor
    worker. Exit status: 0 when the process stopped on SIGINT or SIGTERM; 2 when a setting, a
    file or a secret it needs is missing or invalid.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    env = os.environ if environ is None else environ
    args = _arguments(argv)
    try:
        if args.command == MIGRATE_COMMAND:
            return _migrate(env, check=args.check)
        if args.command == PREFLIGHT_COMMAND:
            options = [
                *(("--skip-models",) if args.skip_models else ()),
                *(("--json",) if args.json else ()),
            ]
            return preflight.main(options, env)
        command = CASE_COMMAND if args.command is None else str(args.command)
        asyncio.run(_run_until_signal(_RUNNERS[command], env))
    except RuntimeConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    return 0


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse a worker or deployment command."""
    parser = argparse.ArgumentParser(
        prog="python -m ais0c_worker",
        description="Temporal workers and deployment checks of the ais0c platform.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        CASE_COMMAND,
        help="the soc-case worker: offenses, cases and their agents (the default)",
    )
    commands.add_parser(BATCH_COMMAND, help="the soc-batch worker: KnowledgeSync's catalog sync")
    commands.add_parser(
        EXECUTOR_COMMAND,
        help="the soc-executor worker: the QRadar note and the alert e-mail",
    )
    migrate = commands.add_parser(MIGRATE_COMMAND, help="upgrade the application database")
    migrate.add_argument(
        "--check", action="store_true", help="report whether the database is at head"
    )
    before = commands.add_parser(PREFLIGHT_COMMAND, help="check production shadow prerequisites")
    before.add_argument("--skip-models", action="store_true", help="do not call model aliases")
    before.add_argument("--json", action="store_true", help="write the check list as JSON")
    return parser.parse_args(argv)


def _migrate(env: Mapping[str, str], *, check: bool) -> int:
    database_url = env.get("AIS0C_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeConfigError("AIS0C_DATABASE_URL is not set")
    try:
        if check:
            current, head = deploy.database_revision(database_url)
            if current == head:
                print(f"at head {head}")
                return 0
            print(f"behind: {current or 'none'} -> {head}")
            return EXIT_DATABASE_BEHIND
        revision = deploy.migrate_to_head(database_url)
    except Exception as error:  # noqa: BLE001 - a failed migration is a defined CLI outcome.
        print(
            f"error: database migration failed ({type(error).__name__})",
            file=sys.stderr,
        )
        return EXIT_DATABASE_ERROR
    print(f"migrated to {revision}")
    return 0


def _address(env: Mapping[str, str]) -> str:
    return env.get(TEMPORAL_ADDRESS_ENV, "").strip() or "127.0.0.1:7233"


def _namespace(env: Mapping[str, str]) -> str:
    return env.get(TEMPORAL_NAMESPACE_ENV, "").strip() or "default"


def _schedule_left_alone(env: Mapping[str, str], name: str) -> bool:
    """`<variable>=off` leaves the Schedule to the worker that owns it, such as a second one."""
    return env.get(name, "").strip().lower() == "off"


async def _run_until_signal(run: Run, env: Mapping[str, str]) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop.set)
    await run(stop, env)
