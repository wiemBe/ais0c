"""AQL Guard: cost and scope rules for an AQL query before it runs (architecture §14).

QRadar reports syntax errors itself, so the guard does not validate the grammar. It splits
the query into tokens, skipping string literals and quoted names correctly, and checks the
clauses that matter for cost and scope at parenthesis depth 0:

- the query is a single `SELECT` statement without nested `SELECT`s,
- `FROM` names one allowed table,
- `LIMIT` is present, within the profile, and precedes the time clause,
- `LAST` or `START ... STOP` is present and the window is within the profile,
- a wide window filters on at least one indexed field.

Anything the tokenizer cannot read unambiguously (comments, backslash escapes, unknown
characters) is rejected rather than guessed.
"""

import dataclasses
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


class AqlRejectReason(StrEnum):
    """Why the guard rejected a query. Returned to the agent as structured data."""

    EMPTY_QUERY = "empty_query"
    UNEXPECTED_CHARACTER = "unexpected_character"
    UNTERMINATED_LITERAL = "unterminated_literal"
    AMBIGUOUS_ESCAPE = "ambiguous_escape"
    COMMENT_NOT_ALLOWED = "comment_not_allowed"
    UNBALANCED_PARENTHESES = "unbalanced_parentheses"
    NOT_SELECT = "not_select"
    MULTIPLE_STATEMENTS = "multiple_statements"
    NESTED_SELECT = "nested_select"
    FROM_INVALID = "from_invalid"
    TABLE_NOT_ALLOWED = "table_not_allowed"
    MISSING_LIMIT = "missing_limit"
    LIMIT_INVALID = "limit_invalid"
    LIMIT_EXCEEDS_PROFILE = "limit_exceeds_profile"
    LIMIT_AFTER_TIME_BOUND = "limit_after_time_bound"
    MISSING_TIME_BOUND = "missing_time_bound"
    TIME_BOUND_INVALID = "time_bound_invalid"
    WINDOW_EXCEEDS_PROFILE = "window_exceeds_profile"
    WIDE_WINDOW_UNINDEXED_FILTER = "wide_window_unindexed_filter"


def _lowercase_tables(tables: frozenset[str]) -> frozenset[str]:
    return frozenset(table.lower() for table in tables)


_PositiveTimedelta = Annotated[timedelta, Field(gt=timedelta(0))]


class AqlProfile(BaseModel):
    """The AQL rules of one gateway profile (architecture §11.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_window: _PositiveTimedelta
    max_limit: Annotated[int, Field(ge=1)]
    allowed_tables: Annotated[
        frozenset[str], Field(min_length=1), AfterValidator(_lowercase_tables)
    ]
    # A window longer than this must filter on at least one indexed field.
    wide_window_threshold: _PositiveTimedelta


class QueryWindow(BaseModel):
    """The time window a query covers.

    Numeric START/STOP values are epoch milliseconds and therefore UTC-aware. Text values keep
    their historical naive representation because QRadar supplies their interpretation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    clause: Literal["last", "start_stop"]
    duration: timedelta
    start: datetime | None = None
    stop: datetime | None = None


class AqlGuardResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reasons: tuple[AqlRejectReason, ...]
    # Tokens joined by single spaces with keywords in upper case; None if the query could
    # not be tokenized. Queries that differ only in whitespace or keyword case share it.
    normalized_query: str | None
    # SHA-256 of `normalized_query`, hex encoded; used for caching and idempotency.
    query_hash: str | None
    window: QueryWindow | None


def check_aql(query: str, profile: AqlProfile, indexed_fields: Iterable[str]) -> AqlGuardResult:
    """Check `query` against `profile`. `indexed_fields` comes from the telemetry inventory."""
    try:
        tokens = _tokenize(query)
    except _TokenizeError as error:
        return _result([error.reason], None, None)
    if not tokens:
        return _result([AqlRejectReason.EMPTY_QUERY], None, None)

    normalized = _join(tokens)
    reasons: list[AqlRejectReason] = []
    if not tokens[0].is_word("SELECT"):
        reasons.append(AqlRejectReason.NOT_SELECT)
    if any(token.is_symbol(";") for token in tokens):
        reasons.append(AqlRejectReason.MULTIPLE_STATEMENTS)
    if any(token.is_word("SELECT") for token in tokens[1:]):
        reasons.append(AqlRejectReason.NESTED_SELECT)
    _check_from(tokens, profile, reasons)
    _check_limit(tokens, profile, reasons)
    window = _time_window(tokens, reasons)
    if window is not None:
        if window.duration > profile.max_window:
            reasons.append(AqlRejectReason.WINDOW_EXCEEDS_PROFILE)
        if window.duration > profile.wide_window_threshold:
            indexed = {field.casefold() for field in indexed_fields}
            if not any(field.casefold() in indexed for field in _filtered_fields(tokens)):
                reasons.append(AqlRejectReason.WIDE_WINDOW_UNINDEXED_FILTER)
    return _result(reasons, normalized, window)


def _result(
    reasons: list[AqlRejectReason], normalized: str | None, window: QueryWindow | None
) -> AqlGuardResult:
    unique = tuple(dict.fromkeys(reasons))
    return AqlGuardResult(
        allowed=not unique,
        reasons=unique,
        normalized_query=normalized,
        query_hash=(
            hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if normalized is not None
            else None
        ),
        window=window,
    )


# --- tokenizer ----------------------------------------------------------------------------

# Upper-cased in the normalized query, like function names. Not a full keyword list: a word
# missing here only keeps its case in the normalized query and counts as a field name in
# the index check.
_KEYWORDS = frozenset(
    "AND AS ASC BETWEEN BY DAY DAYS DESC DISTINCT FALSE FROM GROUP HAVING HOUR HOURS ILIKE "
    "IMATCHES IN IS LAST LIKE LIMIT MATCHES MINUTE MINUTES NOT NULL OR ORDER SEARCH SELECT "
    "START STOP TEXT TRUE WHERE".split()
)
_WHITESPACE = frozenset(" \t\r\n")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_TWO_CHAR_SYMBOLS = frozenset({"<=", ">=", "!=", "<>"})
_ONE_CHAR_SYMBOLS = frozenset("(),=<>+-*/%;")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class _Kind(StrEnum):
    WORD = "word"
    NUMBER = "number"
    STRING = "string"  # 'single quoted', '' is an escaped quote
    NAME = "name"  # "double quoted" field name
    SYMBOL = "symbol"


_QUOTED = frozenset({_Kind.STRING, _Kind.NAME})


@dataclass(frozen=True, slots=True)
class _Token:
    kind: _Kind
    text: str  # keywords and function names upper-cased, everything else as written
    depth: int  # parenthesis depth; a parenthesis has the depth outside it
    start: int
    end: int

    def is_word(self, keyword: str) -> bool:
        return self.kind is _Kind.WORD and self.text == keyword

    def is_symbol(self, symbol: str) -> bool:
        return self.kind is _Kind.SYMBOL and self.text == symbol


class _TokenizeError(Exception):
    def __init__(self, reason: AqlRejectReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _tokenize(query: str) -> list[_Token]:
    if _CONTROL_CHARACTERS.search(query):
        raise _TokenizeError(AqlRejectReason.UNEXPECTED_CHARACTER)
    tokens: list[_Token] = []
    depth = 0
    position = 0
    while position < len(query):
        char = query[position]
        if char in _WHITESPACE:
            position += 1
            continue
        if query.startswith(("--", "/*"), position):
            raise _TokenizeError(AqlRejectReason.COMMENT_NOT_ALLOWED)

        token_depth = depth
        if char == "'":
            kind, end = _Kind.STRING, _quoted_end(query, position, "'", doubled_quote=True)
            text = query[position:end]
        elif char == '"':
            kind, end = _Kind.NAME, _quoted_end(query, position, '"', doubled_quote=False)
            text = query[position:end]
        elif match := _WORD.match(query, position):
            kind, end, text = _Kind.WORD, match.end(), match.group()
            if text.upper() in _KEYWORDS:
                text = text.upper()
        elif match := _NUMBER.match(query, position):
            kind, end, text = _Kind.NUMBER, match.end(), match.group()
        elif query[position : position + 2] in _TWO_CHAR_SYMBOLS:
            kind, end, text = _Kind.SYMBOL, position + 2, query[position : position + 2]
        elif char in _ONE_CHAR_SYMBOLS:
            kind, end, text = _Kind.SYMBOL, position + 1, char
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                token_depth = depth
                if depth < 0:
                    raise _TokenizeError(AqlRejectReason.UNBALANCED_PARENTHESES)
        else:
            raise _TokenizeError(AqlRejectReason.UNEXPECTED_CHARACTER)

        if tokens and tokens[-1].end == position and _may_be_one_token(tokens[-1], kind):
            raise _TokenizeError(AqlRejectReason.UNEXPECTED_CHARACTER)
        if kind is _Kind.SYMBOL and text == "(" and tokens and _is_identifier(tokens[-1]):
            tokens[-1] = dataclasses.replace(tokens[-1], text=tokens[-1].text.upper())
        tokens.append(_Token(kind, text, token_depth, position, end))
        position = end

    if depth != 0:
        raise _TokenizeError(AqlRejectReason.UNBALANCED_PARENTHESES)
    return tokens


def _may_be_one_token(previous: _Token, kind: _Kind) -> bool:
    """Whether QRadar might read `previous` and a `kind` token right after it as one token.

    The normalized query separates them with a space, which would change such a query.
    """
    if kind in _QUOTED:
        # "a""b" with doubled quotes, or a literal prefix such as N'...'.
        return previous.kind in _QUOTED or _is_identifier(previous)
    return previous.kind is _Kind.NUMBER and kind is _Kind.WORD  # 5MINUTES


def _quoted_end(query: str, start: int, quote: str, *, doubled_quote: bool) -> int:
    """Return the index after the closing quote of the literal that opens at `start`.

    Whether QRadar also accepts backslash escapes is not documented. A backslash before a
    quote would end the literal for one reading and not for the other, so it is rejected.
    """
    position = start + 1
    while position < len(query):
        char = query[position]
        if char == "\\" and query[position + 1 : position + 2] == quote:
            raise _TokenizeError(AqlRejectReason.AMBIGUOUS_ESCAPE)
        if char == quote:
            if doubled_quote and query[position + 1 : position + 2] == quote:
                position += 2
                continue
            return position + 1
        position += 1
    raise _TokenizeError(AqlRejectReason.UNTERMINATED_LITERAL)


def _join(tokens: list[_Token]) -> str:
    parts: list[str] = []
    previous: _Token | None = None
    for token in tokens:
        if previous is not None and not (
            token.is_symbol(")")
            or token.is_symbol(",")
            or previous.is_symbol("(")
            or (token.is_symbol("(") and _is_identifier(previous))
        ):
            parts.append(" ")
        parts.append(token.text)
        previous = token
    return "".join(parts)


def _is_identifier(token: _Token) -> bool:
    """Whether `token` is a word that is not a keyword: a field or function name."""
    return token.kind is _Kind.WORD and token.text not in _KEYWORDS


# --- clauses ------------------------------------------------------------------------------

# Words that can follow the table name in `FROM <table>`.
_AFTER_FROM = frozenset({"WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "LAST", "START"})
# Words that end the WHERE clause.
_AFTER_WHERE = frozenset({"GROUP", "HAVING", "ORDER", "LIMIT", "LAST", "START"})
_LAST_UNITS = {
    "MINUTE": timedelta(minutes=1),
    "MINUTES": timedelta(minutes=1),
    "HOUR": timedelta(hours=1),
    "HOURS": timedelta(hours=1),
    "DAY": timedelta(days=1),
    "DAYS": timedelta(days=1),
}
# Larger LAST counts overflow timedelta (at most 999999999 days) and exceed any profile.
_MAX_LAST_DIGITS = 9
_MAX_LIMIT_DIGITS = 18
_MIN_EPOCH_MILLIS_DIGITS = 13
# START/STOP formats QRadar documents, except the ones with a time zone suffix.
_START_STOP_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d-%H:%M:%S",
    "%Y:%m:%d-%H:%M:%S",
)


def _top_level(tokens: list[_Token], keyword: str) -> list[int]:
    return [i for i, token in enumerate(tokens) if token.depth == 0 and token.is_word(keyword)]


def _at(tokens: list[_Token], index: int) -> _Token | None:
    return tokens[index] if index < len(tokens) else None


def _check_from(tokens: list[_Token], profile: AqlProfile, reasons: list[AqlRejectReason]) -> None:
    positions = _top_level(tokens, "FROM")
    if len(positions) != 1:
        reasons.append(AqlRejectReason.FROM_INVALID)
        return
    table = _at(tokens, positions[0] + 1)
    following = _at(tokens, positions[0] + 2)
    if table is None or table.kind is not _Kind.WORD:
        reasons.append(AqlRejectReason.FROM_INVALID)
        return
    if following is not None and not (
        following.kind is _Kind.WORD and following.text in _AFTER_FROM
    ):
        reasons.append(AqlRejectReason.FROM_INVALID)
    if table.text.lower() not in profile.allowed_tables:
        reasons.append(AqlRejectReason.TABLE_NOT_ALLOWED)


def _check_limit(tokens: list[_Token], profile: AqlProfile, reasons: list[AqlRejectReason]) -> None:
    positions = _top_level(tokens, "LIMIT")
    if not positions:
        reasons.append(AqlRejectReason.MISSING_LIMIT)
        return
    value = _at(tokens, positions[0] + 1)
    if len(positions) > 1 or value is None or not _is_integer(value):
        reasons.append(AqlRejectReason.LIMIT_INVALID)
        return
    if len(value.text) > _MAX_LIMIT_DIGITS or int(value.text) > profile.max_limit:
        reasons.append(AqlRejectReason.LIMIT_EXCEEDS_PROFILE)
    elif int(value.text) < 1:
        reasons.append(AqlRejectReason.LIMIT_INVALID)
    time_positions = _top_level(tokens, "LAST") + _top_level(tokens, "START")
    if any(position < positions[0] for position in time_positions):
        reasons.append(AqlRejectReason.LIMIT_AFTER_TIME_BOUND)


def _is_integer(token: _Token) -> bool:
    return token.kind is _Kind.NUMBER and token.text.isdigit()


def _time_window(tokens: list[_Token], reasons: list[AqlRejectReason]) -> QueryWindow | None:
    last = _top_level(tokens, "LAST")
    start = _top_level(tokens, "START")
    stop = _top_level(tokens, "STOP")
    if not (last or start or stop):
        reasons.append(AqlRejectReason.MISSING_TIME_BOUND)
        return None
    if len(last) + len(start) > 1 or len(stop) != len(start):
        reasons.append(AqlRejectReason.TIME_BOUND_INVALID)
        return None
    window = _last_window(tokens, last[0]) if last else _start_stop_window(tokens, start[0])
    if isinstance(window, AqlRejectReason):
        reasons.append(window)
        return None
    return window


def _last_window(tokens: list[_Token], index: int) -> QueryWindow | AqlRejectReason:
    count = _at(tokens, index + 1)
    unit = _at(tokens, index + 2)
    if count is None or not _is_integer(count) or unit is None or unit.text not in _LAST_UNITS:
        return AqlRejectReason.TIME_BOUND_INVALID
    if len(count.text) > _MAX_LAST_DIGITS:
        return AqlRejectReason.WINDOW_EXCEEDS_PROFILE
    if int(count.text) < 1:
        return AqlRejectReason.TIME_BOUND_INVALID
    return QueryWindow(clause="last", duration=int(count.text) * _LAST_UNITS[unit.text])


def _start_stop_window(tokens: list[_Token], index: int) -> QueryWindow | AqlRejectReason:
    start = _at(tokens, index + 1)
    stop_keyword = _at(tokens, index + 2)
    stop = _at(tokens, index + 3)
    if start is None or stop is None or stop_keyword is None or not stop_keyword.is_word("STOP"):
        return AqlRejectReason.TIME_BOUND_INVALID
    if start.kind is not stop.kind or start.kind not in {_Kind.STRING, _Kind.NUMBER}:
        return AqlRejectReason.TIME_BOUND_INVALID
    start_time = _parse_time(start)
    stop_time = _parse_time(stop)
    if start_time is None or stop_time is None or stop_time <= start_time:
        return AqlRejectReason.TIME_BOUND_INVALID
    return QueryWindow(
        clause="start_stop", duration=stop_time - start_time, start=start_time, stop=stop_time
    )


def _parse_time(token: _Token) -> datetime | None:
    if token.kind is _Kind.NUMBER:
        if not token.text.isdigit() or len(token.text) < _MIN_EPOCH_MILLIS_DIGITS:
            return None
        milliseconds = int(token.text)
        if milliseconds <= 0:
            return None
        try:
            return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=milliseconds)
        except OverflowError:
            return None
    if token.kind is not _Kind.STRING:
        return None  # PARSEDATETIME(...) and other expressions cannot be checked
    value = token.text[1:-1]
    for time_format in _START_STOP_FORMATS:
        try:
            # QRadar's time zone applies; only the difference between START and STOP is used.
            return datetime.strptime(value, time_format)  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _filtered_fields(tokens: list[_Token]) -> list[str]:
    """Field names referenced in the WHERE clause, at any depth."""
    positions = _top_level(tokens, "WHERE")
    if len(positions) != 1:
        return []
    fields: list[str] = []
    for index in range(positions[0] + 1, len(tokens)):
        token = tokens[index]
        if token.depth == 0 and token.kind is _Kind.WORD and token.text in _AFTER_WHERE:
            break
        if token.kind is _Kind.NAME:
            fields.append(token.text[1:-1])
        elif token.kind is _Kind.WORD and token.text not in _KEYWORDS:
            following = _at(tokens, index + 1)
            if following is None or not following.is_symbol("("):  # not a function name
                fields.append(token.text)
    return fields
