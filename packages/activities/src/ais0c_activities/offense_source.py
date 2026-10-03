"""Where offenses come from.

`OffenseSource` is the interface the intake and case activities read offenses through. T-012
adds the implementation that reads the lab QRadar through the MCP Policy Gateway;
`FakeOffenseSource` serves the tests.
"""

from collections.abc import Collection, Iterable
from datetime import datetime
from typing import Protocol

from ais0c_contracts import OffenseSnapshot


class OffenseSource(Protocol):
    async def changed_offenses(
        self, *, after_time: datetime, after_id: int, limit: int
    ) -> list[OffenseSnapshot]:
        """Open offenses positioned after the cursor, oldest first, at most `limit`.

        Offenses are ordered by (`last_updated_time`, `offense_id`); the cursor is the position
        of the last offense already handled.
        """
        ...

    async def get_offense(self, offense_id: int) -> OffenseSnapshot | None:
        """The offense as it is now; None if the source does not know it."""
        ...

    async def closed_offenses(self, offense_ids: Collection[int]) -> list[int]:
        """The IDs among `offense_ids` whose offense is closed."""
        ...


class FakeOffenseSource:
    """In-memory offenses for tests."""

    def __init__(self, offenses: Iterable[OffenseSnapshot] = ()) -> None:
        self._offenses: dict[int, OffenseSnapshot] = {}
        self._closed: set[int] = set()
        self.put(*offenses)

    def put(self, *offenses: OffenseSnapshot) -> None:
        """Add offenses, or replace them with newer versions."""
        for offense in offenses:
            self._offenses[offense.offense_id] = offense

    def close(self, offense_id: int) -> None:
        self._closed.add(offense_id)

    async def changed_offenses(
        self, *, after_time: datetime, after_id: int, limit: int
    ) -> list[OffenseSnapshot]:
        cursor = (after_time, after_id)
        changed = [
            offense
            for offense in self._offenses.values()
            if offense.offense_id not in self._closed
            and (offense.last_updated_time, offense.offense_id) > cursor
        ]
        changed.sort(key=lambda offense: (offense.last_updated_time, offense.offense_id))
        return changed[:limit]

    async def get_offense(self, offense_id: int) -> OffenseSnapshot | None:
        return self._offenses.get(offense_id)

    async def closed_offenses(self, offense_ids: Collection[int]) -> list[int]:
        return sorted(set(offense_ids) & self._closed)
