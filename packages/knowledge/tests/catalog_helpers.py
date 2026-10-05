"""A stand-in for the gateway's inventory lists, and synthetic QRadar data.

`FakeInventory` answers `list_rules`, `list_log_sources` and `list_log_source_types` the way the
MCP Policy Gateway answers them (services/mcp-gateway, pipeline.py): QRadar's slice for `limit`
and `offset`, with `fields` and `sort` applied, then the gateway's caps. A `limit` above
`max_rows` is lowered to it, and the result is marked truncated when it comes back full; rows
are cut once their JSON is over `max_bytes`, and the result is marked truncated.

Names and IDs are made up; the type names are QRadar product names.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from ais0c_contracts import ToolCoverage, ToolResult, ToolStatus

Row = dict[str, JsonValue]

WINDOWS_SECURITY = "Microsoft Windows Security Event Log"
FORTIGATE = "Fortinet FortiGate Security Gateway"
LINUX = "Linux OS"


def rule_rows(count: int, *, first_id: int = 100001) -> list[Row]:
    return [
        {"id": first_id + n, "name": f"AIS0C TEST - Rule {n:04d}", "enabled": n % 3 != 0}
        for n in range(count)
    ]


def type_rows(count: int, *, first_id: int = 1000) -> list[Row]:
    named = [{"id": 12, "name": WINDOWS_SECURITY}, {"id": 73, "name": FORTIGATE}]
    named.append({"id": 11, "name": LINUX})
    return [*named, *({"id": first_id + n, "name": f"Test DSM {n:04d}"} for n in range(count))]


def log_source_rows(count: int, *, first_id: int = 2001) -> list[Row]:
    type_ids = [12, 73, 11]
    return [
        {
            "id": first_id + n,
            "name": f"SRV-{n:04d}.example.com",
            "type_id": type_ids[n % len(type_ids)],
            "enabled": True,
        }
        for n in range(count)
    ]


@dataclass
class FakeInventory:
    """Callable like `ais0c_knowledge.catalog.ListCall`; records every call."""

    lists: dict[str, list[Row]]
    max_rows: int = 200
    max_bytes: int = 32768
    calls: list[tuple[str, dict[str, JsonValue]]] = field(default_factory=list)
    answers: dict[str, ToolResult] = field(default_factory=dict)
    """A fixed answer for a tool, e.g. a denial."""
    before_call: Callable[[str, int], None] | None = None
    """Runs before each call with the tool and its offset, e.g. to change a list."""
    ignore_offset: bool = False

    async def __call__(
        self,
        tool_id: str,
        arguments: dict[str, JsonValue],
        *,
        reason: str,
        expected_evidence: str,
    ) -> ToolResult:
        assert reason.strip()
        assert expected_evidence.strip()
        self.calls.append((tool_id, dict(arguments)))
        if tool_id in self.answers:
            return self.answers[tool_id]
        offset = as_int(arguments.get("offset", 0))
        if self.before_call is not None:
            self.before_call(tool_id, offset)
        requested = as_int(arguments.get("limit", 10))
        page_size = min(requested, self.max_rows)
        rows = list(self.lists[tool_id])
        if arguments.get("sort") == "+id":
            rows.sort(key=lambda row: as_int(row["id"]))
        start = 0 if self.ignore_offset else offset
        page = [_pick(row, arguments.get("fields")) for row in rows[start : start + page_size]]
        kept, cut = _cap(page, self.max_bytes)
        clamped = requested > self.max_rows and len(page) >= page_size
        return ok(*kept, truncated=cut or clamped)

    def offsets(self, tool_id: str) -> list[int]:
        return [as_int(arguments["offset"]) for tool, arguments in self.calls if tool == tool_id]


def ok(*rows: Row, truncated: bool = False) -> ToolResult:
    return ToolResult(
        status=ToolStatus.OK,
        evidence_id="ev_01JBCATALOGTEST0001",
        data=list(rows),
        truncated=truncated,
        coverage=ToolCoverage(complete=not truncated, gaps=[]),
    )


def denied(reason: str) -> ToolResult:
    return ToolResult(
        status=ToolStatus.DENIED,
        deny_reason=reason,
        data=[],
        truncated=False,
        coverage=ToolCoverage(complete=False, gaps=[]),
    )


def inventory_lists(
    rules: list[Row], log_sources: list[Row], types: list[Row]
) -> dict[str, list[Row]]:
    return {
        "list_rules": rules,
        "list_log_sources": log_sources,
        "list_log_source_types": types,
    }


def _pick(row: Row, fields: JsonValue) -> Row:
    if not isinstance(fields, str):
        return dict(row)
    wanted = [name.strip() for name in fields.split(",")]
    return {name: row[name] for name in wanted if name in row}


def _cap(rows: Sequence[Row], max_bytes: int) -> tuple[list[Row], bool]:
    """The gateway's byte cap: rows whose JSON array fits in `max_bytes`."""
    kept: list[Row] = []
    size = 2
    for row in rows:
        encoded = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size + encoded + 1 > max_bytes:
            return kept, True
        kept.append(row)
        size += encoded + 1
    return kept, False


def as_int(value: JsonValue) -> int:
    assert isinstance(value, int)
    assert not isinstance(value, bool)
    return value
