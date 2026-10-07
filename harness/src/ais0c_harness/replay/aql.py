"""The AQL subset the replay engine runs on a recorded event table (T-052 criterion 4,
decision T-70).

The engine is not an AQL interpreter. It grows with the queries the agents really write: the
list below is what offenses 30-35's runs in the dev database used, plus the operators of the
task. A query it cannot run raises `Unsupported`, which the replay gateway answers as an error
and counts as `replay_unsupported`; a query QRadar itself would refuse (an unknown column, a
function on the wrong column) raises `QueryError`, which the gateway answers as QRadar's 422.

    SELECT <item> [AS <alias>], ... FROM events
    [WHERE <condition>] [GROUP BY <expression>, ...] [ORDER BY <expression> [ASC|DESC], ...]
    [LIMIT <n>] [START <t> STOP <t> | LAST <n> MINUTES|HOURS|DAYS]

- Items and operands: a column, a string or number literal, QIDNAME(qid), CATEGORYNAME(category),
  LOGSOURCENAME(logsourceid), LOGSOURCETYPENAME(devicetype), UTF8(payload),
  DATEFORMAT(starttime, '<pattern>'), and the aggregates COUNT(*), COUNT(<x>), SUM, MIN, MAX.
  No `*`, no arithmetic, no sub-select, no HAVING.
- Conditions: = != <> < > <= >= IN NOT IN LIKE ILIKE BETWEEN IS [NOT] NULL AND OR NOT and
  parentheses. A comparison with NULL is unknown and an unknown row is not selected.
- START/STOP bound the events' `starttime` (both inclusive). A number is epoch milliseconds
  (UTC, decision T-55); a string is read as UTC, the zone the lab's Ariel API reads it in
  (T-048). LAST counts back from the `now` the engine is given (the scenario's evaluation).
- Without ORDER BY the newest event comes first, as in QRadar.

A result row is keyed by the item's alias, else by its text as written (`QIDNAME(qid)`,
`COUNT(*)`); the audit queries of a recording compare the engine with the lab on this.
"""

import operator
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from pydantic import JsonValue

from ais0c_harness.replay.qradar_fields import BUILTIN_FIELDS, CUSTOM_PROPERTIES

Row = Mapping[str, JsonValue]
type Scalar = str | int | float | None

EVENT_TABLE: Final = "events"
DEFAULT_ORDER_COLUMN: Final = "starttime"
LAST_UNITS: Final = {
    "MINUTE": timedelta(minutes=1),
    "MINUTES": timedelta(minutes=1),
    "HOUR": timedelta(hours=1),
    "HOURS": timedelta(hours=1),
    "DAY": timedelta(days=1),
    "DAYS": timedelta(days=1),
}
TIME_FORMATS: Final = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)

# The recorded columns and the names a query may use for them. The recording's column list is
# fixed (recording.py); `payload` is what UTF8(payload) selects.
COLUMNS: Final = frozenset(
    {
        "starttime",
        "endtime",
        "qid",
        "category",
        "logsourceid",
        "devicetype",
        "sourceip",
        "destinationip",
        "sourceport",
        "destinationport",
        "username",
        "eventcount",
        "magnitude",
        "payload",
    }
)
# function -> (its argument column, the recorded column holding its value)
LOOKUPS: Final = {
    "QIDNAME": ("qid", "qidname"),
    "CATEGORYNAME": ("category", "categoryname"),
    "LOGSOURCENAME": ("logsourceid", "logsourcename"),
    "LOGSOURCETYPENAME": ("devicetype", "logsourcetypename"),
}
AGGREGATES: Final = frozenset({"COUNT", "SUM", "MIN", "MAX"})
FUNCTIONS: Final = frozenset({*LOOKUPS, "UTF8", "DATEFORMAT"})
KEYWORDS: Final = frozenset(
    {
        "SELECT", "DISTINCT", "FROM", "WHERE", "GROUP", "ORDER", "BY", "LIMIT", "START", "STOP",
        "LAST", "AS", "AND", "OR", "NOT", "IN", "LIKE", "ILIKE", "BETWEEN", "IS", "NULL", "ASC",
        "DESC", "HAVING", "UNION", "JOIN", "OFFSET",
    }
)  # fmt: skip


class Unsupported(Exception):
    """The query uses something the replay engine does not run (decision T-70)."""


class QueryError(Exception):
    """A query QRadar would refuse: the message is what its 422 would say."""


# --- tokens -------------------------------------------------------------------------------------


class _Kind(StrEnum):
    WORD = "word"
    QUOTED = "quoted"
    STRING = "string"
    NUMBER = "number"
    SYMBOL = "symbol"


@dataclass(frozen=True)
class _Token:
    kind: _Kind
    text: str
    start: int
    end: int

    def is_word(self, *words: str) -> bool:
        return self.kind is _Kind.WORD and self.text.upper() in words

    def is_symbol(self, *symbols: str) -> bool:
        return self.kind is _Kind.SYMBOL and self.text in symbols


_SYMBOLS: Final = ("<=", ">=", "<>", "!=", "=", "<", ">", "(", ")", ",", "*", ".", "+", "-", "/")
_NUMBER: Final = re.compile(r"\d+(?:\.\d+)?")
_WORD: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _tokenize(query: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(query):
        char = query[position]
        if char.isspace():
            position += 1
        elif char == "'":
            end = _string_end(query, position)
            tokens.append(
                _Token(
                    _Kind.STRING, query[position + 1 : end - 1].replace("''", "'"), position, end
                )
            )
            position = end
        elif char == '"':
            close = query.find('"', position + 1)
            if close < 0:
                raise QueryError("Error Parsing: unterminated quoted name")
            tokens.append(_Token(_Kind.QUOTED, query[position + 1 : close], position, close + 1))
            position = close + 1
        elif (match := _NUMBER.match(query, position)) is not None:
            tokens.append(_Token(_Kind.NUMBER, match.group(), position, match.end()))
            position = match.end()
        elif (match := _WORD.match(query, position)) is not None:
            tokens.append(_Token(_Kind.WORD, match.group(), position, match.end()))
            position = match.end()
        else:
            symbol = next((item for item in _SYMBOLS if query.startswith(item, position)), None)
            if symbol is None:
                raise Unsupported(f"the character {char!r}")
            tokens.append(_Token(_Kind.SYMBOL, symbol, position, position + len(symbol)))
            position += len(symbol)
    return tokens


def _string_end(query: str, start: int) -> int:
    position = start + 1
    while position < len(query):
        if query[position] == "'":
            if query.startswith("''", position):
                position += 2
                continue
            return position + 1
        position += 1
    raise QueryError("Error Parsing: unterminated string literal")


# --- syntax tree --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    value: Scalar


@dataclass(frozen=True)
class Column:
    name: str
    """Lower case."""


@dataclass(frozen=True)
class Call:
    name: str
    """Upper case."""
    args: tuple["Expr", ...]
    star: bool = False
    """COUNT(*)."""


@dataclass(frozen=True)
class Compare:
    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class InList:
    operand: "Expr"
    items: tuple["Expr", ...]
    negated: bool


@dataclass(frozen=True)
class Like:
    operand: "Expr"
    pattern: "Expr"
    ignore_case: bool
    negated: bool


@dataclass(frozen=True)
class Between:
    operand: "Expr"
    low: "Expr"
    high: "Expr"
    negated: bool


@dataclass(frozen=True)
class IsNull:
    operand: "Expr"
    negated: bool


@dataclass(frozen=True)
class Not:
    operand: "Expr"


@dataclass(frozen=True)
class Logic:
    op: str
    """AND or OR."""
    left: "Expr"
    right: "Expr"


type Expr = Literal | Column | Call | Compare | InList | Like | Between | IsNull | Not | Logic


@dataclass(frozen=True)
class SelectItem:
    expr: Expr
    key: str
    """The result column's name: the alias, else the name QRadar gives the item."""
    aliased: bool = False


@dataclass(frozen=True)
class OrderItem:
    expr: Expr
    descending: bool


@dataclass(frozen=True)
class TimeRange:
    start: datetime | None = None
    stop: datetime | None = None
    last: timedelta | None = None


@dataclass(frozen=True)
class Query:
    items: tuple[SelectItem, ...]
    where: Expr | None
    group_by: tuple[Expr, ...]
    order_by: tuple[OrderItem, ...]
    limit: int | None
    time: TimeRange
    aliases: Mapping[str, Expr] = field(default_factory=dict[str, Expr])


def _default_key(expr: Expr, text: str) -> str:
    """The name QRadar gives a result column without an alias, measured on the lab (T-052):
    a column keeps its name, a scalar function is its lower case name and its arguments with
    every run of other characters read as `_` (`qidname_qid`, `utf8_payload`,
    `dateformat_starttime_yyyy_MM_dd_HH_mm_ss`), an aggregate keeps its upper case name
    (`SUM_eventcount`, `COUNT` for COUNT(*))."""
    if not isinstance(expr, Call):
        return text
    inner = text[text.index("(") + 1 : text.rindex(")")]
    words = re.findall(r"[A-Za-z0-9]+", inner)
    name = expr.name if expr.name in AGGREGATES else expr.name.lower()
    return "_".join([name, *words])


# --- parser -------------------------------------------------------------------------------------


class _Parser:
    def __init__(self, query: str) -> None:
        self._query = query
        self._tokens = _tokenize(query)
        self._position = 0

    def parse(self) -> Query:
        self._expect_word("SELECT")
        first = self._peek()
        if first is not None and first.is_word("DISTINCT"):
            # AQL has no SELECT DISTINCT: QRadar reads DISTINCT as a field (measured on the lab).
            raise QueryError('Field "DISTINCT" does not exist in catalog "events"')
        items = self._select_items()
        self._expect_word("FROM")
        table = self._take()
        if table is None or not table.is_word(EVENT_TABLE.upper()):
            raise Unsupported("a table other than events")
        where = self._expression() if self._take_word("WHERE") else None
        group_by: list[Expr] = []
        if self._take_word("GROUP"):
            self._expect_word("BY")
            group_by = self._expression_list()
        order_by: list[OrderItem] = []
        if self._take_word("ORDER"):
            self._expect_word("BY")
            order_by = self._order_items()
        limit = self._limit()
        time = self._time_range()
        if (rest := self._peek()) is not None:
            if rest.kind is _Kind.WORD and rest.text.upper() in KEYWORDS:
                raise Unsupported(f"the clause {rest.text.upper()} here")
            raise QueryError(f"Error Parsing: unexpected {rest.text!r}")
        if group_by or any(_has_aggregate(item.expr) for item in items):
            items = [_grouped_item(item, group_by) for item in items]
        aliases = {item.key.lower(): item.expr for item in items if item.aliased}
        return Query(
            items=tuple(items),
            where=where,
            group_by=tuple(group_by),
            order_by=tuple(order_by),
            limit=limit,
            time=time,
            aliases=aliases,
        )

    # tokens

    def _peek(self, ahead: int = 0) -> _Token | None:
        index = self._position + ahead
        return self._tokens[index] if index < len(self._tokens) else None

    def _take(self) -> _Token | None:
        token = self._peek()
        if token is not None:
            self._position += 1
        return token

    def _take_word(self, *words: str) -> bool:
        token = self._peek()
        if token is not None and token.is_word(*words):
            self._position += 1
            return True
        return False

    def _take_symbol(self, *symbols: str) -> str | None:
        token = self._peek()
        if token is not None and token.is_symbol(*symbols):
            self._position += 1
            return token.text
        return None

    def _expect_word(self, word: str) -> None:
        if not self._take_word(word):
            following = self._peek()
            if following is not None and following.is_symbol("+", "-", "*", "/"):
                raise Unsupported("arithmetic")
            raise QueryError(f"Error Parsing: expected {word}")

    def _expect_symbol(self, symbol: str) -> None:
        if self._take_symbol(symbol) is None:
            raise QueryError(f"Error Parsing: expected {symbol!r}")

    # clauses

    def _select_items(self) -> list[SelectItem]:
        items: list[SelectItem] = []
        while True:
            first = self._peek()
            if first is not None and first.is_symbol("*"):
                raise Unsupported("SELECT *")
            if first is None:
                raise QueryError("Error Parsing: no select list")
            expr = self._operand()
            last = self._tokens[self._position - 1]
            text = self._query[first.start : last.end]
            alias = None
            if self._take_word("AS"):
                name = self._take()
                if name is None or name.kind not in (_Kind.WORD, _Kind.QUOTED, _Kind.STRING):
                    raise QueryError("Error Parsing: expected an alias")
                alias = name.text
            elif (token := self._peek()) is not None and token.kind is _Kind.QUOTED:
                alias = token.text
                self._position += 1
            key = alias if alias is not None else _default_key(expr, text)
            items.append(SelectItem(expr=expr, key=key, aliased=alias is not None))
            if self._take_symbol(",") is None:
                return items

    def _expression_list(self) -> list[Expr]:
        found = [self._operand()]
        while self._take_symbol(",") is not None:
            found.append(self._operand())
        return found

    def _order_items(self) -> list[OrderItem]:
        found: list[OrderItem] = []
        while True:
            expr = self._operand()
            descending = False
            if self._take_word("DESC"):
                descending = True
            else:
                self._take_word("ASC")
            found.append(OrderItem(expr=expr, descending=descending))
            if self._take_symbol(",") is None:
                return found

    def _limit(self) -> int | None:
        if not self._take_word("LIMIT"):
            return None
        token = self._take()
        if token is None or token.kind is not _Kind.NUMBER or not token.text.isdigit():
            raise QueryError("Error Parsing: LIMIT takes a whole number")
        return int(token.text)

    def _time_range(self) -> TimeRange:
        if self._take_word("LAST"):
            count = self._take()
            unit = self._take()
            if (
                count is None
                or count.kind is not _Kind.NUMBER
                or not count.text.isdigit()
                or unit is None
                or unit.text.upper() not in LAST_UNITS
            ):
                raise QueryError("Error Parsing: LAST takes a number and MINUTES, HOURS or DAYS")
            return TimeRange(last=int(count.text) * LAST_UNITS[unit.text.upper()])
        if self._take_word("START"):
            start = self._time_value()
            self._expect_word("STOP")
            return TimeRange(start=start, stop=self._time_value())
        return TimeRange()

    def _time_value(self) -> datetime:
        token = self._take()
        if token is None:
            raise QueryError("Error Parsing: a time value is missing")
        if token.kind is _Kind.NUMBER and token.text.isdigit():
            return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(token.text))
        if token.kind is _Kind.STRING:
            for time_format in TIME_FORMATS:
                try:
                    return datetime.strptime(token.text, time_format).replace(tzinfo=UTC)
                except ValueError:
                    continue
            raise QueryError("Error Parsing: the time value is not a date")
        raise Unsupported("a START/STOP value that is neither a number nor a date string")

    # expressions

    def _expression(self) -> Expr:
        left = self._and()
        while self._take_word("OR"):
            left = Logic("OR", left, self._and())
        return left

    def _and(self) -> Expr:
        left = self._not()
        while self._take_word("AND"):
            left = Logic("AND", left, self._not())
        return left

    def _not(self) -> Expr:
        if self._take_word("NOT"):
            return Not(self._not())
        return self._predicate()

    def _predicate(self) -> Expr:
        left = self._operand()
        negated = False
        if (token := self._peek()) is not None and token.is_word("NOT"):
            following = self._peek(1)
            if following is not None and following.is_word("IN", "LIKE", "ILIKE", "BETWEEN"):
                self._position += 1
                negated = True
        if self._take_word("IN"):
            return InList(left, self._in_items(), negated)
        if self._take_word("LIKE"):
            return Like(left, self._operand(), ignore_case=False, negated=negated)
        if self._take_word("ILIKE"):
            return Like(left, self._operand(), ignore_case=True, negated=negated)
        if self._take_word("BETWEEN"):
            low = self._operand()
            self._expect_word("AND")
            return Between(left, low, self._operand(), negated)
        if self._take_word("IS"):
            is_negated = self._take_word("NOT")
            self._expect_word("NULL")
            return IsNull(left, is_negated)
        if (op := self._take_symbol("=", "!=", "<>", "<", ">", "<=", ">=")) is not None:
            return Compare("!=" if op == "<>" else op, left, self._operand())
        return left

    def _in_items(self) -> tuple[Expr, ...]:
        self._expect_symbol("(")
        if (token := self._peek()) is not None and token.is_word("SELECT"):
            raise Unsupported("a sub-select")
        items = [self._operand()]
        while self._take_symbol(",") is not None:
            items.append(self._operand())
        self._expect_symbol(")")
        return tuple(items)

    def _operand(self) -> Expr:
        token = self._take()
        if token is None:
            raise QueryError("Error Parsing: an operand is missing")
        if token.kind is _Kind.STRING:
            return Literal(token.text)
        if token.kind is _Kind.NUMBER:
            return Literal(float(token.text) if "." in token.text else int(token.text))
        if token.is_symbol("("):
            inner = self._expression()
            self._expect_symbol(")")
            return inner
        if (
            token.is_symbol("-")
            and (number := self._peek()) is not None
            and number.kind is _Kind.NUMBER
        ):
            self._position += 1
            return Literal(-(float(number.text) if "." in number.text else int(number.text)))
        if token.is_symbol("+", "-", "*", "/"):
            raise Unsupported("arithmetic")
        if token.kind is _Kind.QUOTED:
            return Column(token.text.lower())
        if token.kind is not _Kind.WORD:
            raise QueryError(f"Error Parsing: unexpected {token.text!r}")
        if token.is_word("NULL"):
            return Literal(None)
        if (following := self._peek()) is not None and following.is_symbol("("):
            return self._call(token.text.upper())
        if token.text.upper() in KEYWORDS:
            raise QueryError(f"Error Parsing: unexpected {token.text.upper()}")
        return Column(token.text.lower())

    def _call(self, name: str) -> Call:
        self._expect_symbol("(")
        if self._take_symbol("*") is not None:
            self._expect_symbol(")")
            if name != "COUNT":
                raise QueryError(f"Error Parsing: {name}(*) is not valid")
            return Call(name, (), star=True)
        args: list[Expr] = []
        if self._take_symbol(")") is None:
            args.append(self._operand())
            while self._take_symbol(",") is not None:
                args.append(self._operand())
            self._expect_symbol(")")
        return Call(name, tuple(args))


def _grouped_item(item: SelectItem, group_by: Sequence[Expr]) -> SelectItem:
    """QRadar names a column that is neither grouped nor aggregated `FIRST_<column>` and
    returns its first value; it runs no other expression like that."""
    expr = item.expr
    if _has_aggregate(expr) or expr in group_by or _grouped_alias(expr, group_by):
        return item
    if isinstance(expr, Column):
        return SelectItem(
            expr=expr, key=item.key if item.aliased else f"FIRST_{item.key}", aliased=item.aliased
        )
    raise Unsupported("a selected expression that is neither grouped nor aggregated")


def _grouped_alias(expr: Expr, group_by: Sequence[Expr]) -> bool:
    return any(isinstance(grouped, Column) and grouped == expr for grouped in group_by)


def parse(query: str) -> Query:
    """The query's syntax tree. Raises Unsupported or QueryError."""
    return _Parser(query).parse()


# --- evaluation ---------------------------------------------------------------------------------


def _is_aggregate(expr: Expr) -> bool:
    return isinstance(expr, Call) and expr.name in AGGREGATES


def _columns_of(expr: Expr) -> Iterable[Column]:
    match expr:
        case Column():
            yield expr
        case Call(args=args):
            for arg in args:
                yield from _columns_of(arg)
        case Compare(_, left, right) | Logic(_, left, right):
            yield from _columns_of(left)
            yield from _columns_of(right)
        case InList(operand, items, _):
            yield from _columns_of(operand)
            for item in items:
                yield from _columns_of(item)
        case Like(operand, pattern, _, _):
            yield from _columns_of(operand)
            yield from _columns_of(pattern)
        case Between(operand, low, high, _):
            yield from _columns_of(operand)
            yield from _columns_of(low)
            yield from _columns_of(high)
        case IsNull(operand, _) | Not(operand):
            yield from _columns_of(operand)
        case Literal():
            return


def _scalar(value: JsonValue) -> Scalar:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str | int | float) or value is None:
        return value
    return str(value)


def _like(value: str, pattern: str, *, ignore_case: bool) -> bool:
    regex = "".join(
        ".*" if char == "%" else "." if char == "_" else re.escape(char) for char in pattern
    )
    return re.fullmatch(regex, value, re.DOTALL | (re.IGNORECASE if ignore_case else 0)) is not None


_OPERATORS: Final[Mapping[str, Callable[[Any, Any], bool]]] = {
    "=": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
}


def _compare(op: str, left: Scalar, right: Scalar) -> bool | None:
    if left is None or right is None:
        return None
    if isinstance(left, str) != isinstance(right, str):
        # A number against a string: QRadar compares them as text.
        left, right = str(left), str(right)
    return _OPERATORS[op](left, right)


_JAVA_FORMAT: Final = (
    ("yyyy", "%Y"),
    ("MM", "%m"),
    ("dd", "%d"),
    ("HH", "%H"),
    ("mm", "%M"),
    ("ss", "%S"),
    ("SSS", "%f"),
)


def format_time(milliseconds: int, pattern: str) -> str:
    """DATEFORMAT: the Java pattern letters yyyy MM dd HH mm ss SSS; the text between single
    quotes and any other character are copied. The time zone is UTC."""
    moment = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=milliseconds)
    out: list[str] = []
    position = 0
    while position < len(pattern):
        if pattern[position] == "'":
            close = pattern.find("'", position + 1)
            if close < 0:
                raise QueryError("Error Parsing: unterminated DATEFORMAT quote")
            out.append(pattern[position + 1 : close] or "'")
            position = close + 1
            continue
        for letters, directive in _JAVA_FORMAT:
            if pattern.startswith(letters, position):
                text = moment.strftime(directive)
                out.append(text[:3] if directive == "%f" else text)
                position += len(letters)
                break
        else:
            if pattern[position].isalpha():
                raise Unsupported(f"the DATEFORMAT letter {pattern[position]!r}")
            out.append(pattern[position])
            position += 1
    return "".join(out)


class _Evaluator:
    """Evaluates one expression on one row; an aggregate is looked up in `group`."""

    def __init__(self, aliases: Mapping[str, Expr]) -> None:
        self._aliases = aliases

    def value(self, expr: Expr, row: Row, group: Sequence[Row] | None = None) -> Scalar:
        match expr:
            case Literal(value):
                return value
            case Column(name):
                return self._column(name, row, group)
            case Call():
                return self._call(expr, row, group)
            case _:
                return self.truth(expr, row, group)

    def _column(self, name: str, row: Row, group: Sequence[Row] | None) -> Scalar:
        if name in COLUMNS:
            return _scalar(row.get(name))
        if name in self._aliases:
            return self.value(self._aliases[name], row, group)
        raise _unknown_column(Column(name))

    def _call(self, call: Call, row: Row, group: Sequence[Row] | None) -> Scalar:
        name = call.name
        if name in AGGREGATES:
            if group is None:
                raise QueryError(f"Error Parsing: {name} is not allowed here")
            return self._aggregate(call, group)
        if name in LOOKUPS:
            argument, recorded = LOOKUPS[name]
            if len(call.args) != 1 or call.args[0] != Column(argument):
                raise QueryError(f"Wrong argument type for {name}: it takes {argument}")
            return _scalar(row.get(recorded))
        if name == "UTF8":
            if len(call.args) != 1 or call.args[0] != Column("payload"):
                raise QueryError("Wrong argument type for UTF8: it takes payload")
            return _scalar(row.get("payload"))
        if name == "DATEFORMAT":
            if len(call.args) != 2 or not isinstance(call.args[1], Literal):
                raise QueryError("Wrong argument type for DATEFORMAT")
            moment = self.value(call.args[0], row, group)
            pattern = call.args[1].value
            if moment is None:
                return None
            if not isinstance(moment, int) or not isinstance(pattern, str):
                raise QueryError("Wrong argument type for DATEFORMAT")
            return format_time(moment, pattern)
        raise Unsupported(f"the function {name}")

    def _aggregate(self, call: Call, group: Sequence[Row]) -> Scalar:
        if call.name == "COUNT" and call.star:
            return float(len(group))
        if len(call.args) != 1:
            raise QueryError(f"Error Parsing: {call.name} takes one argument")
        values = [
            value for row in group if (value := self.value(call.args[0], row, None)) is not None
        ]
        if call.name == "COUNT":
            return float(len(values))
        if not values:
            return None
        if call.name == "SUM":
            numbers = [value for value in values if isinstance(value, int | float)]
            if len(numbers) != len(values):
                raise QueryError("Wrong argument type for SUM")
            return float(sum(numbers))
        try:
            best = min(values) if call.name == "MIN" else max(values)  # type: ignore[type-var]
        except TypeError:
            raise QueryError(f"Wrong argument type for {call.name}") from None
        # The Ariel API returns a number from MIN and MAX as a double (1.79E12).
        return float(best) if isinstance(best, int | float) else best

    def truth(self, expr: Expr, row: Row, group: Sequence[Row] | None = None) -> bool | None:
        match expr:
            case Logic("AND", left, right):
                a, b = self.truth(left, row, group), self.truth(right, row, group)
                if a is False or b is False:
                    return False
                return None if a is None or b is None else True
            case Logic(_, left, right):
                a, b = self.truth(left, row, group), self.truth(right, row, group)
                if a is True or b is True:
                    return True
                return None if a is None or b is None else False
            case Not(operand):
                inner = self.truth(operand, row, group)
                return None if inner is None else not inner
            case Compare(op, left, right):
                return _compare(op, self.value(left, row, group), self.value(right, row, group))
            case InList(operand, items, negated):
                return self._in_list(operand, items, negated, row, group)
            case Like(operand, pattern, ignore_case, negated):
                text, wanted = self.value(operand, row, group), self.value(pattern, row, group)
                if text is None or wanted is None:
                    return None
                matched = _like(str(text), str(wanted), ignore_case=ignore_case)
                return not matched if negated else matched
            case Between(operand, low, high, negated):
                value = self.value(operand, row, group)
                above = _compare(">=", value, self.value(low, row, group))
                below = _compare("<=", value, self.value(high, row, group))
                if above is False or below is False:
                    inside: bool | None = False
                else:
                    inside = None if above is None or below is None else True
                return None if inside is None else (not inside if negated else inside)
            case IsNull(operand, negated):
                missing = self.value(operand, row, group) is None
                return not missing if negated else missing
            case _:
                value = self.value(expr, row, group)
                if value is None:
                    return None
                raise QueryError("Error Parsing: a condition is expected here")

    def _in_list(
        self,
        operand: Expr,
        items: Sequence[Expr],
        negated: bool,
        row: Row,
        group: Sequence[Row] | None,
    ) -> bool | None:
        value = self.value(operand, row, group)
        if value is None:
            return None
        results = [_compare("=", value, self.value(item, row, group)) for item in items]
        if any(result is True for result in results):
            return not negated
        if any(result is None for result in results):
            return None
        return negated


def _sort_key(value: Scalar) -> tuple[int, float | str]:
    if value is None:
        return (0, 0)
    if isinstance(value, str):
        return (2, value)
    return (1, value)


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[dict[str, JsonValue], ...]


def run_query(
    query: str,
    events: Sequence[Row],
    *,
    now: datetime,
    check: Callable[[Query], None] | None = None,
) -> QueryResult:
    """Run `query` on the recorded event table.

    Raises Unsupported for a construct the engine does not run and QueryError for a query
    QRadar would refuse.
    """
    parsed = parse(query)
    if check is not None:
        check(parsed)
    return execute(parsed, events, now=now)


def execute(parsed: Query, events: Sequence[Row], *, now: datetime) -> QueryResult:
    evaluator = _Evaluator(parsed.aliases)
    _validate(parsed)
    candidates = [row for row in events if _in_time(row, parsed.time, now)]
    selected = [
        row
        for row in candidates
        if parsed.where is None or evaluator.truth(parsed.where, row) is True
    ]
    aggregated = parsed.group_by or any(_has_aggregate(item.expr) for item in parsed.items)
    if aggregated:
        output = _grouped(parsed, selected, evaluator)
    else:
        output = _plain(parsed, selected, evaluator)
    output = _ordered(parsed, output, evaluator)
    rows = tuple(entry[0] for entry in output)
    if parsed.limit is not None:
        rows = rows[: parsed.limit]
    return QueryResult(columns=tuple(item.key for item in parsed.items), rows=rows)


type _Entry = tuple[dict[str, JsonValue], Row, Sequence[Row] | None]


def _unknown_column(column: Column) -> Exception:
    """What QRadar says of a column the recording does not hold: its 422 when it has no such
    field, Unsupported when it has (the recording keeps the columns of EVENT_COLUMNS only)."""
    if column.name in BUILTIN_FIELDS or column.name in CUSTOM_PROPERTIES:
        return Unsupported(f"the column {column.name}, which the recording does not hold")
    return QueryError(f'Field "{column.name}" does not exist in catalog "events"')


def _validate(parsed: Query) -> None:
    """The column and function checks QRadar does before it runs a query."""
    exprs: list[Expr] = [item.expr for item in parsed.items]
    exprs += [parsed.where] if parsed.where is not None else []
    exprs += list(parsed.group_by) + [order.expr for order in parsed.order_by]
    problems: list[Exception] = [
        _unknown_column(column)
        for expr in exprs
        for column in _columns_of(expr)
        if column.name not in COLUMNS and column.name not in parsed.aliases
    ]
    for expr in exprs:
        try:
            _check_calls(expr)
        except (QueryError, Unsupported) as problem:
            problems.append(problem)
    # QRadar refuses the query for any fault of its own, whatever else is in it: that error comes
    # first; only a query whose every fault is something the engine lacks cannot be answered.
    if problems:
        raise next((item for item in problems if isinstance(item, QueryError)), problems[0])


def _check_calls(expr: Expr) -> None:
    """Reject a function QRadar rejects by its arguments, whatever the data."""
    if isinstance(expr, Call):
        if expr.name not in AGGREGATES and expr.name not in FUNCTIONS:
            raise Unsupported(f"the function {expr.name}")
        if expr.name in LOOKUPS:
            argument = LOOKUPS[expr.name][0]
            if len(expr.args) != 1 or expr.args[0] != Column(argument):
                raise QueryError(f"Wrong argument type for {expr.name}: it takes {argument}")
        if expr.name == "UTF8" and (len(expr.args) != 1 or expr.args[0] != Column("payload")):
            raise QueryError("Wrong argument type for UTF8: it takes payload")
        if expr.name == "DATEFORMAT":
            if len(expr.args) != 2 or not isinstance(expr.args[1], Literal):
                raise QueryError("Wrong argument type for DATEFORMAT")
            pattern = expr.args[1].value
            if isinstance(pattern, str):
                format_time(0, pattern)
        for arg in expr.args:
            _check_calls(arg)
    elif isinstance(expr, Compare | Logic):
        if isinstance(expr, Compare):
            _no_bytes(expr.left, expr.right)
        _check_calls(expr.left)
        _check_calls(expr.right)
    elif isinstance(expr, InList):
        _no_bytes(expr.operand, *expr.items)
        _check_calls(expr.operand)
        for item in expr.items:
            _check_calls(item)
    elif isinstance(expr, Like):
        _no_bytes(expr.operand, expr.pattern)
        _check_calls(expr.operand)
        _check_calls(expr.pattern)
    elif isinstance(expr, Between):
        _no_bytes(expr.operand, expr.low, expr.high)
        for part in (expr.operand, expr.low, expr.high):
            _check_calls(part)
    elif isinstance(expr, IsNull | Not):
        _check_calls(expr.operand)


def _no_bytes(*operands: Expr) -> None:
    """The raw `payload` is bytes; QRadar refuses it where a string is needed, UTF8(payload)
    being the string."""
    if any(operand == Column("payload") for operand in operands):
        raise QueryError('Wrong argument type: "byte[]". "String" is required.')


def _has_aggregate(expr: Expr) -> bool:
    if _is_aggregate(expr):
        return True
    if isinstance(expr, Call):
        return any(_has_aggregate(arg) for arg in expr.args)
    return False


def _in_time(row: Row, time: TimeRange, now: datetime) -> bool:
    started = row.get("starttime")
    if not isinstance(started, int):
        return False
    if time.last is not None:
        low = int((now - time.last).timestamp() * 1000)
        return low <= started <= int(now.timestamp() * 1000)
    if time.start is not None and time.stop is not None:
        return int(time.start.timestamp() * 1000) <= started <= int(time.stop.timestamp() * 1000)
    return True


def _plain(parsed: Query, rows: Sequence[Row], evaluator: _Evaluator) -> list[_Entry]:
    return [
        ({item.key: evaluator.value(item.expr, row) for item in parsed.items}, row, None)
        for row in rows
    ]


def _grouped(parsed: Query, rows: Sequence[Row], evaluator: _Evaluator) -> list[_Entry]:
    groups: dict[tuple[Scalar, ...], list[Row]] = {}
    if parsed.group_by:
        for row in rows:
            key = tuple(evaluator.value(expr, row) for expr in parsed.group_by)
            groups.setdefault(key, []).append(row)
    else:
        groups[()] = list(rows)
    out: list[_Entry] = []
    for members in groups.values():
        first: Row = members[0] if members else {}
        out.append(
            (
                {
                    item.key: _group_value(item.expr, first, members, parsed, evaluator)
                    for item in parsed.items
                },
                first,
                members,
            )
        )
    return out


def _group_value(
    expr: Expr, first: Row, members: Sequence[Row], parsed: Query, evaluator: _Evaluator
) -> Scalar:
    return evaluator.value(expr, first, members)


def _ordered(parsed: Query, entries: list[_Entry], evaluator: _Evaluator) -> list[_Entry]:
    if not parsed.order_by:
        if parsed.group_by or any(_has_aggregate(item.expr) for item in parsed.items):
            return entries
        return sorted(
            entries,
            key=lambda entry: _sort_key(_scalar(entry[1].get(DEFAULT_ORDER_COLUMN))),
            reverse=True,
        )
    ordered = list(entries)
    keys = {item.key.lower(): item.key for item in parsed.items}
    for item in reversed(parsed.order_by):

        def key(entry: _Entry, item: OrderItem = item) -> tuple[int, float | str]:
            output, row, group = entry
            expr = item.expr
            if isinstance(expr, Column) and expr.name not in COLUMNS and expr.name in keys:
                return _sort_key(_scalar(output.get(keys[expr.name])))
            return _sort_key(evaluator.value(expr, row, group))

        ordered.sort(key=key, reverse=item.descending)
    return ordered
