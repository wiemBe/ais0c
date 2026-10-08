"""QRadar's rules and log sources, read for the Analysis Catalog (architecture §9, D-25).

`read_inventory` reads three lists with the tools of the MCP Policy Gateway's
`qradar-inventory-read` profile: the rules with their enabled state (`list_rules`), the log
sources, also with their enabled state (`list_log_sources`), and the log source types (`list_log_source_types`), which name
each log source's type. The caller hands in the call function: `ais0c_activities` makes the
calls in a run of the pseudo agent `catalog-sync` (D-33), so this package needs no gateway
client.

Paging: the gateway caps the rows of one result (`max_rows`, 200 unless the profile says
otherwise) and their size (`max_result_bytes`), so a long list comes in pages. Each list is
read with `limit` and `offset` until a page comes back short and whole. After a page the
gateway cut (`truncated`), the next page starts at the first row it left out. Rows are keyed
by ID, so a row that moves to the next page while the list is read counts once. A list that
does not advance or needs more than `max_pages` pages is an error, and so is a result other
than `ok` or a row without a usable ID, name or type: the inventory is complete or there is
none.

Rules come in QRadar's order (by ID; `list_rules` takes no sort), log sources sorted by ID.
QRadar lists the log source types in an order of its own, which stays the same from page to
page.

QRadar's names become display text in the catalog, so they are cleaned on the way in
(`clean_name`). Format characters, such as zero-width spaces and bidirectional overrides, are
dropped; line breaks and other control characters become spaces; white space is collapsed;
and a name is cut to MAX_NAME_LENGTH characters, the limit of `CatalogLogSource.type_name`.
"""

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ais0c_contracts import ToolResult, ToolStatus
from ais0c_storage.repositories import SyncedLogSource, SyncedRule

# Rows per call: the gateway's default row cap (config/connectors/qradar.yaml, limits.max_rows).
# A profile with a lower cap only makes the pages shorter.
INVENTORY_PAGE_SIZE: Final = 200
# Pages per list: 100,000 rows at the default page size, far above any QRadar.
MAX_PAGES: Final = 500
# `CatalogLogSource.type_name` allows 255 characters; names get the same limit.
MAX_NAME_LENGTH: Final = 255
# PostgreSQL bigint, the type of the catalog's IDs.
_MAX_ID: Final = 2**63 - 1
# Invisible characters that change how text looks: format characters (zero-width characters,
# bidirectional overrides) and lone surrogates. They are dropped.
_DROPPED_CATEGORIES: Final = frozenset({"Cf", "Cs"})
# Line breaks, tabs and other controls. They become spaces.
_SPACE_CATEGORIES: Final = frozenset({"Cc", "Zl", "Zp"})

_Id = Annotated[int, Field(ge=0, le=_MAX_ID)]


class InventoryReadError(RuntimeError):
    """A list could not be read in full; nothing may be synced from it."""


class ListCall(Protocol):
    """One call of a gateway tool. `ais0c_activities.SystemRun.call` is one."""

    async def __call__(
        self,
        tool_id: str,
        arguments: dict[str, JsonValue],
        *,
        reason: str,
        expected_evidence: str,
    ) -> ToolResult: ...


@dataclass(frozen=True)
class QRadarInventory:
    """QRadar's rules and log sources, as the catalog stores them, sorted by ID."""

    rules: tuple[SyncedRule, ...]
    log_sources: tuple[SyncedLogSource, ...]
    untyped_log_sources: tuple[int, ...] = ()
    """Log sources whose type is not in QRadar's type list. They are left out of
    `log_sources`, so the catalog keeps what it has for them."""

    def log_source_ids(self) -> frozenset[int]:
        """Every log source QRadar listed, typed or not."""
        return frozenset(
            [*(source.log_source_id for source in self.log_sources), *self.untyped_log_sources]
        )


@dataclass(frozen=True)
class _List:
    tool_id: str
    arguments: Mapping[str, JsonValue]
    reason: str
    expected_evidence: str


_RULES: Final = _List(
    "list_rules",
    {"fields": "id,name,enabled"},
    reason="Read QRadar's rules for the Analysis Catalog sync.",
    expected_evidence="The ID, name and enabled state of every rule.",
)
_LOG_SOURCES: Final = _List(
    "list_log_sources",
    {"fields": "id,name,type_id,enabled", "sort": "+id"},
    reason="Read QRadar's log sources for the Analysis Catalog sync.",
    expected_evidence="The ID, name, type ID and enabled state of every log source.",
)
_LOG_SOURCE_TYPES: Final = _List(
    "list_log_source_types",
    {"fields": "id,name"},
    reason="Name the log source types of the Analysis Catalog's log sources.",
    expected_evidence="The ID and name of every log source type.",
)


class _Named(BaseModel):
    """A rule or a log source type. Fields that were not asked for are ignored."""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: _Id
    name: str


class _Rule(_Named):
    # Synced as `catalog_rules.qradar_enabled` (T-37).
    enabled: bool


class _LogSource(_Named):
    type_id: _Id
    # Synced as `catalog_log_sources.qradar_enabled` (T-95).
    enabled: bool


async def read_inventory(
    call: ListCall, *, page_size: int = INVENTORY_PAGE_SIZE, max_pages: int = MAX_PAGES
) -> QRadarInventory:
    """Every rule and log source QRadar lists. Raises InventoryReadError when a list cannot be
    read in full; errors of the call function itself pass through."""
    if page_size < 1 or max_pages < 1:
        raise ValueError("page_size and max_pages must be at least 1")
    rules = _parse(_Rule, await _read_all(call, _RULES, page_size, max_pages), _RULES)
    sources = _parse(
        _LogSource, await _read_all(call, _LOG_SOURCES, page_size, max_pages), _LOG_SOURCES
    )
    # Read after the log sources, so a type added in between is known.
    types = _parse(
        _Named,
        await _read_all(call, _LOG_SOURCE_TYPES, page_size, max_pages),
        _LOG_SOURCE_TYPES,
    )
    type_names = {item.id: clean_name(item.name) for item in types}
    return QRadarInventory(
        rules=tuple(
            SyncedRule(rule.id, clean_name(rule.name), qradar_enabled=rule.enabled)
            for rule in _by_id(rules)
        ),
        log_sources=tuple(
            SyncedLogSource(
                source.id,
                clean_name(source.name),
                type_names[source.type_id],
                qradar_enabled=source.enabled,
            )
            for source in _by_id(sources)
            if source.type_id in type_names
        ),
        untyped_log_sources=tuple(
            source.id for source in _by_id(sources) if source.type_id not in type_names
        ),
    )


def clean_name(value: str) -> str:
    """`value` as display text: format characters dropped, controls turned into spaces, white
    space collapsed, at most MAX_NAME_LENGTH characters."""
    kept: list[str] = []
    for char in value:
        category = unicodedata.category(char)
        if category in _DROPPED_CATEGORIES:
            continue
        kept.append(" " if category in _SPACE_CATEGORIES else char)
    return " ".join("".join(kept).split())[:MAX_NAME_LENGTH].rstrip()


async def _read_all(
    call: ListCall, listing: _List, page_size: int, max_pages: int
) -> list[dict[str, JsonValue]]:
    """Every row of one list, page by page; a row read twice counts once, as last read."""
    rows: dict[int, dict[str, JsonValue]] = {}
    offset = 0
    for _ in range(max_pages):
        result = await call(
            listing.tool_id,
            {**listing.arguments, "limit": page_size, "offset": offset},
            reason=listing.reason,
            expected_evidence=listing.expected_evidence,
        )
        if result.status is not ToolStatus.OK:
            raise InventoryReadError(
                f"{listing.tool_id} at offset {offset}: {result.status.value}: "
                f"{result.deny_reason or ''}"
            )
        page = result.data
        if not page:
            if result.truncated:
                # The gateway left out the page's first row: that row alone is over its limit.
                raise InventoryReadError(
                    f"{listing.tool_id} at offset {offset}: a row is over the gateway's size limit"
                )
            break
        ids = [_row_id(row, listing) for row in page]
        if rows.keys() >= set(ids):
            raise InventoryReadError(
                f"{listing.tool_id} at offset {offset}: the list does not advance"
            )
        rows.update(zip(ids, page, strict=True))
        offset += len(page)
        if len(page) < page_size and not result.truncated:
            break
    else:
        raise InventoryReadError(f"{listing.tool_id}: more than {max_pages} pages")
    return list(rows.values())


def _row_id(row: Mapping[str, JsonValue], listing: _List) -> int:
    value = row.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_ID:
        raise InventoryReadError(f"{listing.tool_id} returned a row without a usable ID")
    return value


def _parse[M: _Named](
    model: type[M], rows: Sequence[Mapping[str, JsonValue]], listing: _List
) -> list[M]:
    parsed: list[M] = []
    for row in rows:
        try:
            parsed.append(model.model_validate(row))
        except ValidationError as error:
            # Field names only: the values are QRadar's data.
            fields = sorted(
                {".".join(str(part) for part in item["loc"]) for item in error.errors()}
            )
            raise InventoryReadError(
                f"{listing.tool_id} returned a row that cannot be read; check {', '.join(fields)}"
            ) from None
    return parsed


def _by_id[M: _Named](items: Iterable[M]) -> list[M]:
    return sorted(items, key=lambda item: item.id)
