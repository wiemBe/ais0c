"""KnowledgeSync: the platform's periodic imports (architecture §6, §9, §18).

A Temporal Schedule starts a run every day on the `soc-batch` task queue
(`ais0c_workflows.schedules`). To sync at once, as `POST /catalog/sync` does (T-028), trigger
that Schedule (`ScheduleHandle.trigger`): the Schedule skips a start while a run is still
going, so a run started by hand never overlaps a scheduled one.

Today a run has one step, the Analysis Catalog sync (D-25, T-022): the activity
`sync_analysis_catalog` reads QRadar's rules and log sources through the gateway, brings the
catalog in line and returns its counts, which become the run's result. ATT&CK and CTI imports
(§18) are to be further steps.

A failed attempt is retried a few times, after a growing pause. A sync that still fails ends
the run as failed, the catalog as it was; the next day's run starts over. An attempt
heartbeats after every gateway call, so one that hangs is given up long before its timeout.
"""

from datetime import timedelta
from typing import Final

from temporalio import workflow
from temporalio.common import RetryPolicy

from ais0c_workflows._activity import call
from ais0c_workflows.names import KNOWLEDGE_SYNC, SYNC_ANALYSIS_CATALOG

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, ConfigDict

# One attempt of the catalog sync: three lists of pages, one gateway call at a time, each call
# up to three minutes with its quota wait (ais0c_agents.gateway_http).
CATALOG_SYNC_TIMEOUT: Final = timedelta(minutes=30)
# Longer than one gateway call.
CATALOG_SYNC_HEARTBEAT: Final = timedelta(minutes=5)
# Five attempts in about a quarter of an hour.
CATALOG_SYNC_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(minutes=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=15),
    maximum_attempts=5,
)


class KnowledgeSyncResult(BaseModel):
    """What a run did."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog: dict[str, int]
    """The catalog sync's counts: the rules and log sources QRadar listed, and how many of them
    were added, changed, missing from QRadar or of an unknown type."""


@workflow.defn(name=KNOWLEDGE_SYNC)
class KnowledgeSync:
    @workflow.run
    async def run(self) -> KnowledgeSyncResult:
        catalog = await call(
            SYNC_ANALYSIS_CATALOG,
            result_type=dict[str, int],
            attempt_timeout=CATALOG_SYNC_TIMEOUT,
            heartbeat_timeout=CATALOG_SYNC_HEARTBEAT,
            retry_policy=CATALOG_SYNC_RETRY,
        )
        workflow.logger.info("catalog sync: %s", catalog)
        return KnowledgeSyncResult(catalog=catalog)
