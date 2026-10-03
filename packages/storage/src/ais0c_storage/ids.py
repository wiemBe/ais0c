"""Row identifiers.

IDs derived from a workflow are text (`case-12345`); every other ID is a UUIDv7
(docs/impl/data-model.md).
"""

import secrets
import time
import uuid

_UNIX_MS_MASK = (1 << 48) - 1
_RAND_B_BITS = 62


def new_uuid7() -> uuid.UUID:
    """A UUIDv7 (RFC 9562 §5.7): 48-bit Unix time in milliseconds, then 74 random bits.

    IDs created in different milliseconds sort by creation time.
    """
    unix_ms = (time.time_ns() // 1_000_000) & _UNIX_MS_MASK
    rand = secrets.randbits(12 + _RAND_B_BITS)
    rand_a, rand_b = rand >> _RAND_B_BITS, rand & ((1 << _RAND_B_BITS) - 1)
    # unix_ts_ms (48) | version 7 (4) | rand_a (12) | variant 0b10 (2) | rand_b (62)
    value = (unix_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)
