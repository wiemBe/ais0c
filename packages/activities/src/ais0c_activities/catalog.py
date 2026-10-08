"""The Analysis Catalog sync activity of KnowledgeSync (architecture §9, D-25; T-022).

`sync_analysis_catalog` reads QRadar's rules, log sources and log source types through the MCP
Policy Gateway with the `qradar-inventory-read` profile and then brings the catalog in line
(`ais0c_knowledge.catalog`). It returns the sync's counts.

The reads are one system run of the pseudo agent `catalog-sync` in the context
`knowledge-sync` (D-33, `ais0c_activities.gateway.system_run`): the gateway records every call
and its evidence under that run, as it does for the intake's reads. The catalog is written
only after every list has been read in full, in one transaction; an attempt that fails while
reading changes nothing in the catalog, so no entry is marked missing or listed again
(`missing_since`, T-37) on a partial read. A list QRadar's data makes unreadable fails the
attempt for good (`InventoryUnreadable`); other failures, such as an unreachable gateway or a
denied call, are retried by the workflow.

Calls are at least MIN_CALL_INTERVAL apart. The gateway runs calls that carry a case ID, as
these do, in the case quota pool, which the reactive work shares; a large QRadar takes many
pages, and the pause keeps the sync to at most half of that pool's rate. The attempt
heartbeats after every call.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final

from pydantic import JsonValue
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ais0c_activities.db import SessionFactory
from ais0c_activities.gateway import SystemRun, system_run, utc_now
from ais0c_activities.names import SYNC_ANALYSIS_CATALOG
from ais0c_agents import GatewayClient, ToolsetProfile
from ais0c_contracts import Budget, TimeWindow, ToolResult
from ais0c_knowledge.catalog import (
    MAX_PAGES,
    ClassDefaults,
    InventoryReadError,
    ListCall,
    read_inventory,
    sync_catalog,
)

CATALOG_SYNC_AGENT_ID: Final = "catalog-sync"
KNOWLEDGE_SYNC_CONTEXT: Final = "knowledge-sync"
INVENTORY_PROFILE: Final = "qradar-inventory-read"
INVENTORY_TOOLS: Final = frozenset({"list_rules", "list_log_sources", "list_log_source_types"})
# The time window the reads declare. The lists are QRadar's configuration now; the window is
# the day since the previous daily sync.
SYNC_WINDOW: Final = timedelta(days=1)
# At most 60 calls a minute; the case pool allows 120 (config/connectors/qradar.yaml).
MIN_CALL_INTERVAL: Final = timedelta(seconds=1)
# Declared, not enforced: three lists of at most MAX_PAGES pages each.
SYNC_BUDGET: Final = Budget(tokens=0, tool_calls=3 * MAX_PAGES, seconds=1800)
# For a sync that is given no classes: no log source type has a default class.
_NO_CLASS_DEFAULTS: Final[ClassDefaults] = MappingProxyType({})
# How many missing IDs one log line names.
_LOGGED_IDS: Final = 20

_log = logging.getLogger(__name__)

type Sleep = Callable[[float], Awaitable[None]]


class CatalogSyncActivities:
    """The catalog sync, reading with `gateway` and `profile`, the `qradar-inventory-read`
    profile and its token's client. `class_defaults` gives each log source the default
    telemetry classes of its type (T-95)."""

    def __init__(
        self,
        *,
        sessions: SessionFactory,
        gateway: GatewayClient,
        profile: ToolsetProfile,
        class_defaults: ClassDefaults = _NO_CLASS_DEFAULTS,
        clock: Callable[[], datetime] = utc_now,
        min_call_interval: timedelta = MIN_CALL_INTERVAL,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if min_call_interval < timedelta(0):
            raise ValueError("min_call_interval must not be negative")
        self._sessions = sessions
        self._gateway = gateway
        self._profile = profile
        self._class_defaults = class_defaults
        self._clock = clock
        self._interval = min_call_interval.total_seconds()
        self._sleep = sleep

    @property
    def class_defaults(self) -> ClassDefaults:
        """The default telemetry classes the sync writes to each log source."""
        return self._class_defaults

    def activities(self) -> list[Callable[..., object]]:
        return [self.sync_analysis_catalog]

    @activity.defn(name=SYNC_ANALYSIS_CATALOG)
    async def sync_analysis_catalog(self) -> dict[str, int]:
        """Read QRadar's inventory and sync the catalog; returns the sync's counts
        (`CatalogSyncReport.counts`)."""
        now = self._clock()
        async with system_run(
            sessions=self._sessions,
            gateway=self._gateway,
            profile=self._profile,
            agent_id=CATALOG_SYNC_AGENT_ID,
            case_id=KNOWLEDGE_SYNC_CONTEXT,
            objective="Read QRadar's rules and log sources for the Analysis Catalog.",
            window=TimeWindow(start=now - SYNC_WINDOW, end=now),
            budget=SYNC_BUDGET,
            clock=self._clock,
        ) as run:
            try:
                inventory = await read_inventory(self._paced(run))
            except InventoryReadError as error:
                raise ApplicationError(
                    str(error), type="InventoryUnreadable", non_retryable=True
                ) from None
        _heartbeat("catalog")
        async with self._sessions.begin() as session:
            report = await sync_catalog(
                session, inventory, synced_at=self._clock(), class_defaults=self._class_defaults
            )
        _log.info("catalog sync in run %s: %s", run.run_id, report.counts())
        for message, ids in (
            (
                "%d catalog rules are no longer listed by QRadar and were marked missing: %s",
                report.rules_marked_missing,
            ),
            (
                "%d catalog log sources are no longer listed by QRadar and were marked missing: %s",
                report.log_sources_marked_missing,
            ),
            (
                "%d log sources have a type missing from QRadar's type list and were left "
                "as they are: %s",
                report.log_sources_untyped,
            ),
        ):
            if ids:
                _log.warning(message, len(ids), _some(ids))
        return report.counts()

    def _paced(self, run: SystemRun) -> ListCall:
        """`run.call`, at least the call interval after the previous call returned, followed by
        a heartbeat."""
        first = True

        async def call(
            tool_id: str,
            arguments: dict[str, JsonValue],
            *,
            reason: str,
            expected_evidence: str,
        ) -> ToolResult:
            nonlocal first
            if not first and self._interval > 0:
                await self._sleep(self._interval)
            first = False
            result = await run.call(
                tool_id, arguments, reason=reason, expected_evidence=expected_evidence
            )
            _heartbeat(tool_id)
            return result

        return call


def _heartbeat(step: str) -> None:
    if activity.in_activity():
        activity.heartbeat(step)


def _some(ids: tuple[int, ...]) -> str:
    shown = ", ".join(str(i) for i in ids[:_LOGGED_IDS])
    return shown if len(ids) <= _LOGGED_IDS else f"{shown}, ..."
