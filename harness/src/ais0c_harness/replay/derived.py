"""Answers derived from a scenario's offense and enrichment (T-052 criterion 6, decision T-67 (3)).

A scenario or recording rarely holds a result for each read tool an agent may call. Four of them
can be answered from what the run is already given, deterministically, so a model that asks gets
the same facts every time instead of an `upstream_error`:

- `list_source_addresses`: one row per source IP of the offense;
- `list_local_destination_addresses`: one row per destination IP;
- `get_log_source`: the log source named by `log_source_id` when the offense or the catalog
  knows it; any other ID gets the error QRadar gives for a missing one.
- `list_assets`: one asset for each offense address matched as a critical asset by enrichment;
  with no match it is an empty successful result. The critical-asset label is copied verbatim
  into the asset's Description property. Like every tool result, the prompt wrapper treats it as
  untrusted text.

The filter of a list call is not applied: the rows are always the offense's own. `fields`
projects the rows and `limit` cuts them, as the real tools do. Every other tool is not derived.
"""

import ipaddress
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
    {"list_source_addresses", "list_local_destination_addresses", "get_log_source", "list_assets"}
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
        if tool_id == "list_assets":
            return self._assets(arguments, number)
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

    def _assets(self, arguments: Mapping[str, JsonValue], number: int) -> ToolResult:
        addresses = dict.fromkeys([*self._offense.source_ips, *self._offense.destination_ips])
        hits = {hit.value: hit for hit in self._enrichment.critical_asset_hits}
        rows: list[dict[str, JsonValue]] = []
        for position, address in enumerate(addresses, start=1):
            hit = hits.get(address)
            if hit is None:
                continue
            try:
                address_type = "IPV6" if ipaddress.ip_address(address).version == 6 else "IPV4"
            except ValueError:
                continue
            rows.append(
                {
                    "id": position,
                    "domain_id": 0,
                    "hostnames": [],
                    "interfaces": [{"ip_addresses": [{"value": address, "type": address_type}]}],
                    "properties": [
                        {"name": "Description", "value": hit.label},
                        {"name": "Criticality", "value": hit.level.value},
                    ],
                    "risk_score_sum": 0,
                    "vulnerability_count": 0,
                }
            )
        offset = _integer(arguments.get("offset"), 0)
        limit = _integer(arguments.get("limit"), 50)
        page = rows[offset : offset + limit]
        shown = [_project(row, arguments.get("fields")) for row in page]
        return _ok("list_assets", number, shown, truncated=offset + limit < len(rows))


def _project(row: dict[str, JsonValue], fields: JsonValue | None) -> dict[str, JsonValue]:
    if not isinstance(fields, str) or not fields.strip():
        return row
    wanted = {name.strip() for name in fields.split(",")}
    return {name: value for name, value in row.items() if name in wanted}


def _integer(value: JsonValue | None, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


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
