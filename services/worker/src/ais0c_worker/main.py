"""The worker processes: `python -m ais0c_worker` and `python -m ais0c_worker batch`.

| Variable | Meaning | Default |
|---|---|---|
| `TEMPORAL_ADDRESS` | Temporal frontend | `127.0.0.1:7233` |
| `TEMPORAL_NAMESPACE` | Namespace | `default` |
| `AIS0C_INTAKE_SCHEDULE` | `off` leaves the intake Schedule alone, e.g. on a second worker | `on` |
| `AIS0C_KNOWLEDGE_SYNC_SCHEDULE` | `off` leaves the KnowledgeSync Schedule alone | `on` |

Without a command the process is the case worker, as it has been: `run_case_worker` reads the
offenses and runs the agent chain of each case (`ais0c_worker.case_worker`). With the command `batch` it is
the batch worker of the `soc-batch` queue, `run_batch_worker`, which runs KnowledgeSync's
catalog sync.

The database, the gateway, LiteLLM and the configuration files are the runtime's settings
(`ais0c_activities.runtime`); the batch worker needs only the database and the gateway. Either
process stops on SIGINT or SIGTERM. Work it leaves unfinished is not lost: the next worker
continues each workflow from its history.

The case worker warns at start-up about every model alias whose release in the model registry
differs from the one its last agent run recorded: a new model release, for which the model gate
must run again (T-24, docs/agent-harness.md §5, B2). The batch worker calls no model and has no
warning.
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
    load_batch_runtime,
    load_case_runtime,
    model_release_changes,
)
from ais0c_worker.batch_worker import build_batch_worker
from ais0c_worker.case_worker import build_case_worker, connect
from ais0c_worker.schedule import ensure_intake_schedule, ensure_knowledge_sync_schedule

TEMPORAL_ADDRESS_ENV: Final = "TEMPORAL_ADDRESS"
TEMPORAL_NAMESPACE_ENV: Final = "TEMPORAL_NAMESPACE"
INTAKE_SCHEDULE_ENV: Final = "AIS0C_INTAKE_SCHEDULE"
KNOWLEDGE_SYNC_SCHEDULE_ENV: Final = "AIS0C_KNOWLEDGE_SYNC_SCHEDULE"

CASE_COMMAND: Final = "case"
BATCH_COMMAND: Final = "batch"

# A setting, a file or a secret the worker needs is missing or invalid.
EXIT_CONFIG_ERROR: Final = 2

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
    inventory token is missing, or the gateway serves another profile than the catalog sync
    reads with.
    """
    env = os.environ if environ is None else environ
    runtime = await load_batch_runtime(env)
    try:
        client = await connect(_address(env), namespace=_namespace(env))
        if not _schedule_left_alone(env, KNOWLEDGE_SYNC_SCHEDULE_ENV):
            await ensure_knowledge_sync_schedule(client)
        async with build_batch_worker(client, runtime):
            logger.info("batch worker running")
            await stop.wait()
        logger.info("batch worker stopped")
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
}


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run the worker the command line names; returns the exit status.

    Without a command it is the case worker, `batch` the batch worker. Exit status: 0 when the
    process stopped on SIGINT or SIGTERM; 2 when a setting, a file or a secret it needs is
    missing or invalid.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    env = os.environ if environ is None else environ
    try:
        asyncio.run(_run_until_signal(_RUNNERS[_command(argv)], env))
    except RuntimeConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    return 0


def _command(argv: Sequence[str] | None) -> str:
    """The worker the command line asks for; the case worker when it asks for none."""
    parser = argparse.ArgumentParser(
        prog="python -m ais0c_worker",
        description="Temporal workers of the ais0c platform (architecture §6).",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        CASE_COMMAND,
        help="the soc-case worker: offenses, cases and their agents (the default)",
    )
    commands.add_parser(BATCH_COMMAND, help="the soc-batch worker: KnowledgeSync's catalog sync")
    args = parser.parse_args(argv)
    return CASE_COMMAND if args.command is None else str(args.command)


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
