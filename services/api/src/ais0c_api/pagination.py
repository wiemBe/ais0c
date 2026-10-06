"""Cursor pagination for the list endpoints (api.md).

    GET /cases?cursor=<opaque>&limit=50
    -> { "items": [...], "next_cursor": "<opaque>" | null }

`limit` is 1 to 200, 50 by default. A cursor is this module's business: it is base64url of the
last row's sort key plus a version tag, so a cursor the API did not write (or one from another
version) does not parse and the request is a 400 instead of silently listing the wrong page.
"""

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Final
from uuid import UUID

from ais0c_api.problems import Problem, invalid_cursor

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200
MIN_LIMIT: Final = 1
# Bumped when the encoding changes; an old cursor then does not parse and is a 400.
_CURSOR_VERSION: Final = 1


def check_limit(limit: int | None) -> int:
    """`limit` as the page size, or `DEFAULT_LIMIT`; outside 1..200 is a 400."""
    if limit is None:
        return DEFAULT_LIMIT
    if not MIN_LIMIT <= limit <= MAX_LIMIT:
        raise Problem(
            400,
            "pagination.invalid_limit",
            detail=f"limit must be between {MIN_LIMIT} and {MAX_LIMIT}",
        )
    return limit


def encode_cursor(key: Sequence[str]) -> str:
    """An opaque cursor for the page that ends at the row whose sort key is `key`.

    The key is a list of strings (a timestamp in ISO 8601 and a row's ID, a UUID, an integer as
    text), so the encoding is plain JSON and `decode_cursor` gives the same list back.
    """
    payload = json.dumps({"v": _CURSOR_VERSION, "k": list(key)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> object:
    """The sort key inside `cursor`; a cursor this API did not write is a 400."""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise invalid_cursor() from None
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _CURSOR_VERSION
        or "k" not in payload
        or not isinstance(payload["k"], list)
    ):
        raise invalid_cursor()
    return payload["k"]


def paginate[T](
    rows: list[T], limit: int, key_of: Callable[[T], Sequence[str]]
) -> tuple[list[T], str | None]:
    """The page and its next cursor: at most `limit` rows, and a cursor only when there is more.

    `rows` must be one more than the page: the caller fetches `limit + 1` rows and this drops the
    extra one. An empty result is an empty page with no cursor.
    """
    if not rows:
        return [], None
    if len(rows) <= limit:
        return rows, None
    page = rows[:limit]
    return page, encode_cursor(key_of(page[-1]))


def string_cursor(cursor: str | None) -> str | None:
    """A cursor whose sort key is one string (a QRadar rule or log source ID)."""
    key = _key(cursor)
    if key is None:
        return None
    if len(key) != 1:
        raise invalid_cursor()
    return key[0]


def uuid_cursor(cursor: str | None) -> UUID | None:
    """A cursor whose sort key is one UUID (a QA item)."""
    value = string_cursor(cursor)
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise invalid_cursor() from None


def pair_cursor(cursor: str | None) -> tuple[str, str] | None:
    """A cursor whose sort key is two strings (an ISO timestamp and a row's ID)."""
    key = _key(cursor)
    if key is None:
        return None
    if len(key) != 2:
        raise invalid_cursor()
    return key[0], key[1]


def timestamp_cursor(cursor: str | None, *, field: str) -> tuple[datetime, str] | None:
    """A cursor whose sort key is a timestamp and a row's ID; both must be readable."""
    pair = pair_cursor(cursor)
    if pair is None:
        return None
    first, row_id = pair
    try:
        moment = datetime.fromisoformat(first)
    except ValueError:
        raise invalid_cursor() from None
    if moment.tzinfo is None:
        raise invalid_cursor()
    return moment, row_id


def _key(cursor: str | None) -> list[str] | None:
    if cursor is None:
        return None
    key = decode_cursor(cursor)
    if not isinstance(key, list) or not all(isinstance(part, str) for part in key):
        raise invalid_cursor()
    return key


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "check_limit",
    "decode_cursor",
    "encode_cursor",
    "paginate",
    "pair_cursor",
    "string_cursor",
    "timestamp_cursor",
    "uuid_cursor",
]
