"""The kill switch: one flag stops every external write (architecture §26, T-23).

The flag is `writes_enabled` in `platform_flags`. While it is off, the executor writes nothing
outside the platform (no QRadar note, no e-mail) and the analysis goes on. A flag that was never
set is off, so a new platform runs in shadow mode until an admin switches writes on.

A write checks the switch after its content is ready and right before it leaves:

    await kill_switch.guarded(lambda: send(content))

The flag is read from the database on every check; nothing is cached, so a switch-off holds for
the next write, well within the 5 seconds T-23 allows. If the flag cannot be read, the error
propagates and nothing is written.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_executor.common.errors import WritesDisabled
from ais0c_executor.common.text import clean_text
from ais0c_storage.enums import PlatformFlag
from ais0c_storage.models import PlatformFlagRow
from ais0c_storage.repositories import get_platform_flag


class KillSwitch:
    """Reads the `writes_enabled` flag through `sessions`."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def writes_enabled(self) -> bool:
        """Whether writes are on now."""
        flag = await self._read()
        return flag is not None and flag.enabled

    async def check(self) -> None:
        """Raise `WritesDisabled` unless writes are on.

        Call it right before every external write, after the content is ready, and start the
        write at once; `guarded` does both.
        """
        flag = await self._read()
        if flag is None:
            raise WritesDisabled(
                "external writes are off: writes_enabled was never switched on (shadow mode)"
            )
        if not flag.enabled:
            # The reason is an admin's free text; in the message it is one line.
            why = f": {clean_text(flag.reason, 200)}" if flag.reason else ""
            raise WritesDisabled(
                f"external writes are off: writes_enabled was switched off by {flag.changed_by}"
                f" at {flag.changed_at.isoformat()}{why}",
                reason=flag.reason,
                changed_by=flag.changed_by,
                changed_at=flag.changed_at,
            )

    async def guarded[T](self, write: Callable[[], Awaitable[T]]) -> T:
        """Check the switch, then run `write` with nothing in between.

        `write` is not called if writes are off; `WritesDisabled` is raised instead.
        """
        await self.check()
        return await write()

    async def _read(self) -> PlatformFlagRow | None:
        # A session of its own: the caller's transaction may have begun before the switch-off.
        async with self._sessions() as session:
            return await get_platform_flag(session, PlatformFlag.WRITES_ENABLED)
