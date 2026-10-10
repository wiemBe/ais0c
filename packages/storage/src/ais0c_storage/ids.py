"""Row identifiers.

IDs derived from a workflow are text (`case-12345`); every other ID is a UUIDv7
(docs/impl/data-model.md).
"""

import secrets
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Final

_UNIX_MS_MASK = (1 << 48) - 1
_RAND_B_BITS = 62
_RAND_BITS = 12 + _RAND_B_BITS


class Uuid7Generator:
    """UUIDv7s that strictly increase within the process (RFC 9562 §6.2, monotonic random).

    A new millisecond starts from fresh random bits; within the same millisecond, or when the
    clock steps back, the previous ID's 74 random bits are incremented by one. If they overflow,
    the millisecond moves on by one and the random bits start from zero.
    """

    def __init__(
        self,
        clock: Callable[[], int] = time.time_ns,
        randbits: Callable[[int], int] = secrets.randbits,
    ) -> None:
        self._clock = clock
        self._randbits = randbits
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_rand = 0

    def __call__(self) -> uuid.UUID:
        with self._lock:
            unix_ms = (self._clock() // 1_000_000) & _UNIX_MS_MASK
            if unix_ms > self._last_ms:
                rand = self._randbits(_RAND_BITS)
            else:
                unix_ms = self._last_ms
                rand = self._last_rand + 1
                if rand == 1 << _RAND_BITS:
                    unix_ms += 1
                    rand = 0

            self._last_ms = unix_ms
            self._last_rand = rand

            rand_a = rand >> _RAND_B_BITS
            rand_b = rand & ((1 << _RAND_B_BITS) - 1)
            # unix_ts_ms (48) | version 7 (4) | rand_a (12) | variant 0b10 (2) | rand_b (62)
            value = (unix_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
            return uuid.UUID(int=value)


_DEFAULT: Final = Uuid7Generator()


def new_uuid7() -> uuid.UUID:
    """A UUIDv7 (RFC 9562 §5.7) whose IDs made in this process strictly increase."""
    return _DEFAULT()


def uuid7_floor(moment: datetime) -> uuid.UUID:
    """The smallest UUIDv7 created at `moment` or later: every ID `new_uuid7` makes from that
    millisecond on is greater or equal. For a row's creation time, which a UUIDv7 key carries."""
    unix_ms = int(moment.timestamp() * 1000) & _UNIX_MS_MASK
    return uuid.UUID(int=unix_ms << 80)
