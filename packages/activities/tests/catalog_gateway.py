"""The MCP Policy Gateway in process, serving only `qradar-inventory-read`, for the catalog sync.

The gateway is the real one (services/mcp-gateway) with the repository's config/connectors and
config/policies files: its profile check, ToolIntent and argument checks, page and size caps,
quota pool and records all run. The activity talks to it with the agents' HTTP client over
an in-process transport. Only the MCP side is replaced: `QRadarLists` answers the inventory
tools the way the qradar-mcp fork does, with QRadar's slice for `limit` and `offset` (its
Range header) and `fields` and `sort` applied, as `{"items": [...]}`. The lab test puts the
fork itself on that side.

The data is synthetic: made-up rule and log source names, QRadar's own type names.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

import httpx2
from pydantic import JsonValue, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_agents import ToolsetProfile
from ais0c_agents.gateway_http import HttpGatewayClient
from ais0c_mcp_gateway.app import create_app
from ais0c_mcp_gateway.auth import ProfileAuthenticator
from ais0c_mcp_gateway.logs import Redactor
from ais0c_mcp_gateway.pipeline import Gateway
from ais0c_mcp_gateway.registry import Registry, load_registry
from ais0c_mcp_gateway.upstream import Upstream, UpstreamFailure, UpstreamOutcome

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
CONFIG_DIR: Final = REPO_ROOT / "config"
INVENTORY_PROFILE: Final = "qradar-inventory-read"
READ_INSTANCE: Final = "qradar-mcp-read"
TOKEN: Final = "catalog-sync-test-token-0123456789abcdef"  # noqa: S105 - a test value

Row = dict[str, JsonValue]

WINDOWS_SECURITY: Final = "Microsoft Windows Security Event Log"
FORTIGATE: Final = "Fortinet FortiGate Security Gateway"
LINUX: Final = "Linux OS"
_TYPES: Final = {12: WINDOWS_SECURITY, 73: FORTIGATE, 11: LINUX}


def rule_rows(count: int, *, name_length: int = 40) -> list[Row]:
    """Rules from ID 100001 on; long names make the gateway cut pages for size."""
    return [
        {
            "id": 100001 + n,
            "name": f"AIS0C TEST - Rule {n:04d} ".ljust(name_length, "x"),
            "type": "EVENT",
            "enabled": n % 4 != 0,
            "origin": "SYSTEM",
        }
        for n in range(count)
    ]


def log_source_rows(count: int) -> list[Row]:
    """Log sources from ID 2001 on, listed newest first as QRadar may; their types cycle."""
    type_ids = list(_TYPES)
    return [
        {
            "id": 2001 + n,
            "name": f"SRV-{n:04d}.example.com",
            "type_id": type_ids[n % len(type_ids)],
            "enabled": True,
            "description": "Synthetic log source",
        }
        for n in reversed(range(count))
    ]


def type_rows(extra: int) -> list[Row]:
    """The three types the log sources use, after `extra` made-up ones."""
    made_up: list[Row] = [{"id": 5000 + n, "name": f"Test DSM {n:04d}"} for n in range(extra)]
    return [*made_up, *({"id": type_id, "name": name} for type_id, name in _TYPES.items())]


def type_name(type_id: int) -> str:
    return _TYPES[type_id]


@dataclass
class QRadarLists:
    """The fork's inventory tools over fixed lists; records every call it gets."""

    lists: dict[str, list[Row]]
    calls: list[tuple[str, dict[str, JsonValue]]] = field(default_factory=list)
    failing: set[str] = field(default_factory=set)
    """Tools whose calls fail, as an unreachable QRadar makes them."""

    async def call_tool(self, name: str, arguments: Mapping[str, JsonValue]) -> UpstreamOutcome:
        self.calls.append((name, dict(arguments)))
        if name in self.failing:
            return UpstreamOutcome.failed(UpstreamFailure.ERROR, "QRadar answered HTTP 503")
        rows = list(self.lists[name])
        if arguments.get("sort") == "+id":
            rows.sort(key=lambda row: as_int(row["id"]))
        offset, limit = as_int(arguments.get("offset", 0)), as_int(arguments.get("limit", 50))
        fields = arguments.get("fields")
        page = [_pick(row, fields) for row in rows[offset : offset + limit]]
        return UpstreamOutcome.ok({"items": list[JsonValue](page)})

    def calls_of(self, name: str) -> list[dict[str, JsonValue]]:
        return [arguments for tool, arguments in self.calls if tool == name]


def inventory_lists(
    rules: list[Row], log_sources: list[Row], types: list[Row]
) -> dict[str, list[Row]]:
    return {
        "list_rules": rules,
        "list_log_sources": log_sources,
        "list_log_source_types": types,
    }


def inventory_registry() -> Registry:
    """The real registry, with only the inventory profile enabled."""
    registry = load_registry(CONFIG_DIR, ["qradar"])
    return Registry(
        profiles={INVENTORY_PROFILE: registry.profiles[INVENTORY_PROFILE]},
        connectors=registry.connectors,
    )


async def inventory_client(
    sessions: async_sessionmaker[AsyncSession],
    upstream: Upstream,
    *,
    now: Callable[[], datetime],
) -> tuple[HttpGatewayClient, ToolsetProfile]:
    """The HTTP client of the inventory profile's token, and the profile as the gateway serves
    it (`GET /v1/tools`)."""
    gateway = Gateway(
        registry=inventory_registry(),
        upstreams={READ_INSTANCE: upstream},
        sessions=sessions,
        redactor=Redactor([TOKEN]),
        now=now,
    )
    app = create_app(gateway, ProfileAuthenticator({INVENTORY_PROFILE: SecretStr(TOKEN)}))
    client = HttpGatewayClient(
        "http://gateway.test", TOKEN, transport=httpx2.ASGITransport(app=app)
    )
    return client, await client.fetch_toolset()


def _pick(row: Row, fields: JsonValue) -> Row:
    if not isinstance(fields, str):
        return dict(row)
    wanted = [name.strip() for name in fields.split(",")]
    return {name: row[name] for name in wanted if name in row}


def as_int(value: JsonValue) -> int:
    assert isinstance(value, int)
    assert not isinstance(value, bool)
    return value
