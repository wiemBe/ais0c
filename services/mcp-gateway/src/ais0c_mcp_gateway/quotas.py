"""Quota pools (architecture §8.2, §11.3; T-011 criterion 8).

A call with a case_id runs in the case pool, a call with only a hunt_id in the hunt pool. Each
pool has its own state and lock, so a full hunt pool never delays a case call. A pool limits:

- requests: calls to the MCP server in any 60 seconds;
- concurrent searches: Ariel searches open at once. A search holds its slot from just before
  it is created until it is deleted, reports a final status or reaches the pool's search TTL,
  so an abandoned search cannot hold a slot forever. The TTL only frees the slot; the search
  itself ends in QRadar on its own.

A call waits up to the pool's max_wait_seconds for room and is denied after that; a denial
only answers that call (T-007 report: a quota never locks anything). The state lives in the
gateway process, which runs as a single instance; a restart frees every slot.
"""

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from ais0c_mcp_gateway.registry import PoolName, QuotaPoolConfig

RATE_WINDOW_SECONDS: Final = 60.0


@dataclass(eq=False)
class Admission:
    """Room in a pool for one call; for a search start, also a reserved search slot."""

    pool: "QuotaPool"
    search_slot: object | None = None
    settled: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class QuotaDenial:
    pool: PoolName
    limit: str  # "requests_per_minute" or "concurrent_searches"


class QuotaPool:
    def __init__(
        self,
        name: PoolName,
        config: QuotaPoolConfig,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name: PoolName = name
        self.config = config
        self._monotonic = monotonic
        self._calls: deque[float] = deque()
        # Slot key (a reservation object, then the search ID) -> when it expires.
        self._searches: dict[object, float] = {}
        self._changed = asyncio.Condition()

    def allows_new_search(self, now: datetime) -> bool:
        """Whether a search may start at `now` (aware) under the pool's allowed hours."""
        hours, zone = self.config.allowed_hours, self.config.zone
        if hours is None or zone is None:
            return True
        return hours.contains(now.astimezone(zone).time())

    @property
    def open_searches(self) -> int:
        self._expire(self._monotonic())
        return len(self._searches)

    async def admit(self, *, starts_search: bool) -> Admission | QuotaDenial:
        """Wait for room for one call, at most max_wait_seconds."""
        deadline = self._monotonic() + self.config.max_wait_seconds
        async with self._changed:
            while True:
                now = self._monotonic()
                self._expire(now)
                rate_wait = self._rate_wait(now)
                search_wait = self._search_wait(now) if starts_search else 0.0
                if rate_wait <= 0 and search_wait <= 0:
                    self._calls.append(now)
                    admission = Admission(pool=self)
                    if starts_search:
                        admission.search_slot = object()
                        self._searches[admission.search_slot] = now + self.config.search_ttl_seconds
                    return admission
                remaining = deadline - now
                if remaining <= 0:
                    limit = "concurrent_searches" if search_wait > 0 else "requests_per_minute"
                    return QuotaDenial(pool=self.name, limit=limit)
                # Wake up when a slot is freed, when the earliest limit clears, or at the deadline.
                timeout = min(remaining, max(rate_wait, search_wait))
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout)
                except TimeoutError:
                    pass

    async def bind_search(self, admission: Admission, search_id: str) -> None:
        """The reserved slot now belongs to the created search."""
        async with self._changed:
            expires = self._searches.pop(admission.search_slot, None)
            admission.settled = True
            if expires is not None:
                self._searches[search_id] = expires
            self._changed.notify_all()

    async def release(self, admission: Admission) -> None:
        """Free a reserved slot whose search was not created. Safe to call twice."""
        if admission.search_slot is None or admission.settled:
            return
        async with self._changed:
            self._searches.pop(admission.search_slot, None)
            admission.settled = True
            self._changed.notify_all()

    async def finish_search(self, search_id: str) -> None:
        """The search was deleted or reached a final status: free its slot."""
        async with self._changed:
            if self._searches.pop(search_id, None) is not None:
                self._changed.notify_all()

    def _expire(self, now: float) -> None:
        while self._calls and self._calls[0] <= now - RATE_WINDOW_SECONDS:
            self._calls.popleft()
        for key in [key for key, expires in self._searches.items() if expires <= now]:
            del self._searches[key]

    def _rate_wait(self, now: float) -> float:
        if len(self._calls) < self.config.requests_per_minute:
            return 0.0
        return self._calls[0] + RATE_WINDOW_SECONDS - now

    def _search_wait(self, now: float) -> float:
        if len(self._searches) < self.config.concurrent_searches:
            return 0.0
        return min(self._searches.values()) - now
