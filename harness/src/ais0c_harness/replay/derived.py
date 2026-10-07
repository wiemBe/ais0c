"""Answers derived from a scenario's offense and enrichment (T-052 criterion 6, decision T-67 (3)).

A scenario or recording rarely holds a result for each read tool an agent may call. Three of them
can be answered from what the run is already given, deterministically, so a model that asks gets
the same facts every time instead of an `upstream_error`:

- `list_source_addresses`: one row per source IP of the offense;
- `list_local_destination_addresses`: one row per destination IP;
- `get_log_source`: the log source named by `log_source_id` when the offense or the catalog
  knows it; any other ID gets the error QRadar gives for a missing one.

The filter of a list call is not applied: the rows are always the offense's own. `fields`
projects the rows and `limit` cuts them, as the real tools do. Every other tool is not derived.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue

from ais0c_contracts import (
    EnrichmentContext,
    OffenseSnapshot,
    ToolCoverage,
    ToolResult,
    ToolStatus,
)

DERIVED_TOOLS: Final = frozenset(
    {"list_source_addresses", "list_local_destination_addresses", "get_log_source"}
)
DEFAULT_LIMIT: Final = 10
EVIDENCE_PREFIX: Final = "ev_derived_"


class DerivedAnswers:
    """The derived answers of one offense and its enrichment."""

    def __init__(self, offense: OffenseSnapshot, enrichment: EnrichmentContext) -> None:
        self._offense = offense
        self._enrichment = enrichment

    def answer(
        self, tool_id: str, arguments: Mapping[str, JsonValue], number: int
    ) -> ToolResult | None:
        """The result of the `number`-th call of `tool_id`, or None for a tool not derived."""
        if tool_id not in DERIVED_TOOLS:
            return None
        if tool_id == "get_log_source":
            return self._log_source(arguments, number)
        offense = self._offense
        key = "source_ip" if tool_id == "list_source_addresses" else "local_destination_ip"
        ips = offense.source_ips if tool_id == "list_source_addresses" else offense.destination_ips
        rows: list[dict[str, JsonValue]] = [
            {
                "id": position,
                key: ip,
                "offense_ids": [offense.offense_id],
                "domain_id": 0,
                "magnitude": offense.magnitude,
            }
            for position, ip in enumerate(ips, start=1)
        ]
        limit = arguments.get("limit")
        size = limit if isinstance(limit, int) and not isinstance(limit, bool) else DEFAULT_LIMIT
        shown = [_project(row, arguments.get("fields")) for row in rows[:size]]
        return _ok(tool_id, number, shown, truncated=len(rows) > size)

    def _log_source(self, arguments: Mapping[str, JsonValue], number: int) -> ToolResult:
        wanted = arguments.get("log_source_id")
        known = {entry.log_source_id: entry for entry in self._enrichment.catalog.log_sources}
        if (
            not isinstance(wanted, int)
            or isinstance(wanted, bool)
            or (wanted not in self._offense.log_source_ids and wanted not in known)
        ):
            return _error("Error executing get_log_source: the log source does not exist")
        row: dict[str, JsonValue] = {"id": wanted, "name": f"log source {wanted}"}
        if (entry := known.get(wanted)) is not None:
            if entry.description is not None:
                row["description"] = entry.description
            if entry.type_name is not None:
                row["type_name"] = entry.type_name
        return _ok(
            "get_log_source", number, [_project(row, arguments.get("fields"))], truncated=False
        )


def _project(row: dict[str, JsonValue], fields: JsonValue | None) -> dict[str, JsonValue]:
    if not isinstance(fields, str) or not fields.strip():
        return row
    wanted = {name.strip() for name in fields.split(",")}
    return {name: value for name, value in row.items() if name in wanted}


def _ok(
    tool_id: str, number: int, rows: list[dict[str, JsonValue]], *, truncated: bool
) -> ToolResult:
    return ToolResult(
        status=ToolStatus.OK,
        evidence_id=f"{EVIDENCE_PREFIX}{tool_id}_{number}",
        data=rows,
        truncated=truncated,
        coverage=ToolCoverage(complete=not truncated, gaps=[]),
    )


def _error(detail: str) -> ToolResult:
    return ToolResult(
        status=ToolStatus.ERROR,
        deny_reason=f"upstream_error: {detail}",
        data=[],
        truncated=False,
        coverage=ToolCoverage(complete=False, gaps=[]),
    )
