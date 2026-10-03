"""Profile-based output filter (architecture §13.3, §22).

Some gateway profiles must not see free text: the verifier gets results without the payload
and free-text fields, so text an attacker wrote into a log cannot reach it. The filter removes
those fields from every row, at any depth.

Field names are compared after case folding and removing everything but letters and digits,
so `UTF8_Payload`, `utf8payload` and `"UTF8 Payload"` are the same name. A field is dropped
when its name equals a listed name or contains a listed fragment.

Removing fields from results alone is not enough: an AQL query could select the payload under
another name (`SELECT UTF8(payload) AS note ...`). A profile with a filter therefore must not
reference a filtered field in its queries at all; `aql_filtered_field_references` finds such
references.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue

# Single-quoted string literals (skipped), double-quoted names and bare words of an AQL query.
_AQL_TOKEN = re.compile(r"'(?:[^']|'')*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_]*")


def normalize_field_name(name: str) -> str:
    """`UTF8_Payload` and `"utf8 payload"` both become `utf8payload`."""
    return "".join(char for char in name.casefold() if char.isalnum())


def _normalize_all(names: frozenset[str]) -> frozenset[str]:
    normalized = frozenset(normalize_field_name(name) for name in names)
    if "" in normalized:
        raise ValueError("a field name must contain a letter or digit")
    return normalized


_FieldNames = Annotated[frozenset[str], AfterValidator(_normalize_all)]


class FieldFilter(BaseModel):
    """The output filter of a gateway profile. Names are stored normalized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    drop_fields: Annotated[_FieldNames, Field(min_length=1)]
    # Fragments: a field whose normalized name contains one of them is dropped too.
    drop_fields_containing: _FieldNames = frozenset()

    def drops(self, name: str) -> bool:
        normalized = normalize_field_name(name)
        return normalized in self.drop_fields or any(
            fragment in normalized for fragment in self.drop_fields_containing
        )


def filter_rows(
    rows: Sequence[Mapping[str, JsonValue]], field_filter: FieldFilter
) -> list[dict[str, JsonValue]]:
    """Copies of `rows` without the fields `field_filter` drops, at any depth."""
    return [_filter_object(row, field_filter) for row in rows]


def aql_filtered_field_references(query: str, field_filter: FieldFilter) -> tuple[str, ...]:
    """Names in `query` that `field_filter` drops, in order of appearance.

    String literals are skipped; bare words and double-quoted names are checked, so function
    names are checked too. A query that matches nothing returns an empty tuple.
    """
    found: dict[str, None] = {}
    for match in _AQL_TOKEN.finditer(query):
        token = match.group()
        if token.startswith("'"):
            continue
        name = token[1:-1] if token.startswith('"') else token
        if field_filter.drops(name):
            found[name] = None
    return tuple(found)


def _filter_object(
    value: Mapping[str, JsonValue], field_filter: FieldFilter
) -> dict[str, JsonValue]:
    return {
        key: _filter_value(item, field_filter)
        for key, item in value.items()
        if not field_filter.drops(key)
    }


def _filter_value(value: JsonValue, field_filter: FieldFilter) -> JsonValue:
    if isinstance(value, Mapping):
        return _filter_object(value, field_filter)
    if isinstance(value, list):
        return _filter_list(value, field_filter)
    return value


def _filter_list(values: Iterable[JsonValue], field_filter: FieldFilter) -> list[JsonValue]:
    return [_filter_value(item, field_filter) for item in values]
