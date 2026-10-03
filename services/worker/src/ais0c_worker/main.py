"""The case worker process: `python -m ais0c_worker`.

| Variable | Meaning | Default |
|---|---|---|
| `TEMPORAL_ADDRESS` | Temporal frontend | `127.0.0.1:7233` |
| `TEMPORAL_NAMESPACE` | Namespace | `default` |
| `AIS0C_INTAKE_SCHEDULE` | `off` leaves the intake Schedule alone, e.g. on a second worker | `on` |

The database, the gateway, LiteLLM and the configuration files are the runtime's settings
(`ais0c_activities.runtime`). The worker stops on SIGINT or SIGTERM. Work it leaves unfinished
is not lost: the next worker continues each workflow from its history.

At start-up the worker warns about every model alias whose release in the model registry
differs from the one its last agent run recorded: a new model release, for which the model gate
must run again (T-24, docs/agent-harness.md §5, B2).
"""

import asyncio
import logging
import os
import signal
from collections.abc import Mapping
from typing import Final

from ais0c_activities import (
    ModelRelease,
    ModelReleaseChange,
    SessionFactory,
    load_case_runtime,
    model_release_changes,
)
from ais0c_worker.case_worker import build_case_worker, connect
from ais0c_worker.schedule import ensure_intake_schedule

TEMPORAL_ADDRESS_ENV: Final = "TEMPORAL_ADDRESS"
TEMPORAL_NAMESPACE_ENV: Final = "TEMPORAL_NAMESPACE"
INTAKE_SCHEDULE_ENV: Final = "AIS0C_INTAKE_SCHEDULE"

logger = logging.getLogger("ais0c.worker")


async def run_case_worker(stop: asyncio.Event, environ: Mapping[str, str] | None = None) -> None:
    """Run the case worker until `stop` is set; `environ` defaults to `os.environ`."""
    env = os.environ if environ is None else environ
    runtime = await load_case_runtime(env)
    try:
        await warn_on_model_release_changes(runtime.sessions, runtime.model_releases)
        client = await connect(
            env.get(TEMPORAL_ADDRESS_ENV, "").strip() or "127.0.0.1:7233",
            namespace=env.get(TEMPORAL_NAMESPACE_ENV, "").strip() or "default",
        )
        if env.get(INTAKE_SCHEDULE_ENV, "").strip().lower() != "off":
            await ensure_intake_schedule(client)
        worker = build_case_worker(
            client,
            sessions=runtime.sessions,
            source=runtime.source,
            triage=runtime.triage,
            settings=runtime.settings,
            ioc_matcher=runtime.ioc_matcher,
        )
        async with worker:
            logger.info("case worker running")
            await stop.wait()
        logger.info("case worker stopped")
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_run_until_signal())


async def _run_until_signal() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop.set)
    await run_case_worker(stop)
