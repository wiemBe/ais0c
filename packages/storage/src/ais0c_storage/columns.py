"""Column types that check values on every write, wherever the write comes from.

- `UtcDateTime`: `timestamptz`; naive datetimes are rejected and values come back in UTC.
- `EnumText`: `text` holding a value of a `StrEnum`.
- `ContractJSONB`: `jsonb` that must validate against a contract model (data-model.md names the
  model of each JSON column).

SQLAlchemy wraps an error raised here in `sqlalchemy.exc.StatementError`; the original error is
its `orig`.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, override

import pydantic_core
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import DateTime, Dialect, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


def revalidate[M: BaseModel](model: type[M], value: object) -> M:
    """`value` (a model instance or plain data) validated as `model` from its JSON form.

    Going through JSON means an instance built with `model_construct()` or changed after
    validation is checked again, and an instance of another model is not accepted as `model`.
    """
    return model.model_validate_json(pydantic_core.to_json(value))


class UtcDateTime(TypeDecorator[datetime]):
    """`timestamptz` (data-model.md: every time column is timestamptz, UTC)."""

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    @override
    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected a datetime, got {type(value).__name__}")
        if value.utcoffset() is None:
            raise ValueError("naive datetime: timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @override
    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class EnumText[E: StrEnum](TypeDecorator[E]):
    """`text` holding one of the values of `enum`, or of `allowed` when given."""

    impl = Text
    cache_ok = True

    def __init__(self, enum: type[E], allowed: Iterable[E] | None = None) -> None:
        super().__init__()
        self.enum = enum
        self.allowed: frozenset[E] = frozenset(enum if allowed is None else allowed)

    @override
    def process_bind_param(self, value: E | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        member = self.enum(value)
        if member not in self.allowed:
            expected = ", ".join(sorted(self.allowed))
            raise ValueError(f"{member.value!r} is not allowed here; expected one of: {expected}")
        return member.value

    @override
    def process_result_value(self, value: str | None, dialect: Dialect) -> E | None:
        return None if value is None else self.enum(value)


class ContractJSONB[T](TypeDecorator[T]):
    """`jsonb` whose content must validate against `model` on every write and every read.

    `model` is a contract model or a type built from them (`list[Claim]`); a union of models is
    passed as a `TypeAdapter`. Python `None` is stored as SQL NULL, never as JSON `null`.
    """

    impl = JSONB
    cache_ok = True

    def __init__(self, model: type[T] | TypeAdapter[T]) -> None:
        super().__init__(none_as_null=True)
        self.model = model
        self._adapter: TypeAdapter[T] = (
            model if isinstance(model, TypeAdapter) else TypeAdapter(model)
        )

    def validate(self, value: object) -> T:
        """`value` validated from its JSON form; see `revalidate`."""
        return self._adapter.validate_json(pydantic_core.to_json(value))

    @override
    def process_bind_param(self, value: T | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        return self._adapter.dump_python(self.validate(value), mode="json")

    @override
    def process_result_value(self, value: Any, dialect: Dialect) -> T | None:
        return None if value is None else self._adapter.validate_python(value)
