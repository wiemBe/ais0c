"""Temporal, only to trigger the KnowledgeSync Schedule (`POST /catalog/sync`).

The API does not import `ais0c_workflows` (criterion 8): the Schedule's ID is a constant of this
module and a test in `tests/` checks it against `ais0c_workflows.names.KNOWLEDGE_SYNC_SCHEDULE_ID`.

`ScheduleTrigger` is the seam: a test passes a fake, so the route is exercised without a Temporal
server. Temporal being unreachable is a 503, not a 500.
"""

from typing import Final, Protocol

from temporalio.client import Client

# `ais0c_workflows.names.KNOWLEDGE_SYNC_SCHEDULE_ID` (criterion 8, T-037).
KNOWLEDGE_SYNC_SCHEDULE_ID: Final = "knowledge-sync"


class ScheduleTrigger(Protocol):
    """What the API needs from Temporal: start a Schedule's next run now."""

    async def trigger(self, schedule_id: str) -> None:
        """Start `schedule_id` now; raises `TemporalUnavailable` when it cannot."""


class TemporalUnavailable(RuntimeError):
    """Temporal could not be reached, or refused the trigger."""


class TemporalScheduleTrigger:
    """Triggers a Schedule through a connected Temporal client.

    The client is connected once, lazily, and kept. A connection attempt that fails raises
    `TemporalUnavailable` without caching the failure, so a later request tries again.
    """

    def __init__(self, address: str, namespace: str) -> None:
        self._address = address
        self._namespace = namespace
        self._client: Client | None = None

    async def connect(self) -> Client:
        if self._client is None:
            try:
                self._client = await Client.connect(self._address, namespace=self._namespace)
            except Exception as error:
                raise TemporalUnavailable(f"cannot reach Temporal at {self._address}") from error
        return self._client

    async def trigger(self, schedule_id: str) -> None:
        client = await self.connect()
        try:
            await client.get_schedule_handle(schedule_id).trigger()
        except Exception as error:
            raise TemporalUnavailable(
                f"Temporal refused to trigger the {schedule_id} Schedule"
            ) from error
