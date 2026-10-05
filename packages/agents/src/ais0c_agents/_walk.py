"""Rebuilding a model with some of the models nested in it replaced."""

from collections.abc import Callable, Sequence
from typing import cast

from pydantic import BaseModel


def replace_models[M: BaseModel, T: BaseModel](
    model: M, kind: type[T], replace: Callable[[T], T]
) -> M:
    """`model` with every `kind` instance in it, at any depth, replaced by `replace(instance)`.

    Models are walked through their fields, and through lists, tuples and dict values. A model
    is copied only when something in it changed.
    """
    rebuilt = _rebuild(model, kind, replace)
    return cast(M, rebuilt)


def _rebuild[T: BaseModel](value: object, kind: type[T], replace: Callable[[T], T]) -> object:
    if isinstance(value, BaseModel):
        updates: dict[str, object] = {}
        for name in type(value).model_fields:
            current: object = getattr(value, name)
            rebuilt = _rebuild(current, kind, replace)
            if rebuilt is not current:
                updates[name] = rebuilt
        model = value.model_copy(update=updates) if updates else value
        return replace(model) if isinstance(model, kind) else model
    if isinstance(value, list | tuple):
        original = cast(Sequence[object], value)
        items = [_rebuild(item, kind, replace) for item in original]
        if all(new is old for new, old in zip(items, original, strict=True)):
            return value
        return tuple(items) if isinstance(value, tuple) else items
    if isinstance(value, dict):
        entries = cast(dict[object, object], value)
        mapped = {key: _rebuild(item, kind, replace) for key, item in entries.items()}
        return value if all(mapped[key] is item for key, item in entries.items()) else mapped
    return value
