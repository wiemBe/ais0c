"""The case worker process: `python -m ais0c_worker`.

| Variable | Meaning | Default |
|---|---|---|
| `TEMPORAL_ADDRESS` | Temporal frontend | `127.0.0.1:7233` |
| `TEMPORAL_NAMESPACE` | Namespace | `default` |
| `AIS0C_INTAKE_SCHEDULE` | `off` leaves the intake Schedule alone, e.g. on a second worker | `on` |

The database, the gateway, LiteLLM and the configuration files are the runtime's settings
(`ais0c_activities.runtime`). The worker stops on SIGINT or SIGTERM. Work it leaves unfinished
is not lost: the next worker continues each workflow from its history.
"""

import asyncio
import logging
import os
import signal
from collections.abc import Mapping
from typing import Final

from ais0c_activities import load_case_runtime
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
