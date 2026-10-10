# SPDX-License-Identifier: Apache-2.0
"""Tool contract snapshots: registered tools per profile, input and output schemas.

    python -m qradar_mcp.fork.schema_export            # rewrite snapshots/
    python -m qradar_mcp.fork.schema_export --check    # exit 1 if snapshots/ is stale

The export reads ``tools/list`` from each profile's server, i.e. exactly what an MCP client
sees. tests/fork/test_schema_snapshots.py runs the check, so CI fails when a schema
changes and the snapshot is not regenerated and reviewed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from fastmcp import Client, FastMCP
from pydantic import SecretStr

from qradar_mcp.fork.app import build_server
from qradar_mcp.fork.client import PlatformQRadarClient
from qradar_mcp.fork.tool_profiles import PROFILE_NAMES

DEFAULT_SNAPSHOT_DIR = Path("snapshots")


def export_snapshots() -> dict[str, str]:
    """Snapshot files as ``{relative path: content}``."""
    files: dict[str, str] = {}
    tools: dict[str, dict[str, Any]] = {}
    for profile in PROFILE_NAMES:
        listed = asyncio.run(_list_tools(build_server(profile, _offline_client())))
        files[f"profiles/{profile}.json"] = _dump(
            {"profile": profile, "tools": sorted(tool["name"] for tool in listed)}
        )
        for tool in listed:
            entry = tools.setdefault(tool["name"], {**tool, "profiles": []})
            if (
                entry["inputSchema"] != tool["inputSchema"]
                or entry["outputSchema"] != tool["outputSchema"]
            ):
                raise RuntimeError(f"tool {tool['name']} differs between profiles")
            entry["profiles"].append(profile)
    for name, entry in tools.items():
        files[f"tools/{name}.json"] = _dump(entry)
    return files


def write_snapshots(directory: Path) -> None:
    expected = export_snapshots()
    for stale in _existing(directory) - expected.keys():
        (directory / stale).unlink()
    for relative, content in expected.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def snapshot_differences(directory: Path) -> list[str]:
    """Human-readable list of files that are missing, changed or no longer exported."""
    expected = export_snapshots()
    existing = _existing(directory)
    problems = [f"missing: {path}" for path in sorted(expected.keys() - existing)]
    problems += [f"stale: {path}" for path in sorted(existing - expected.keys())]
    problems += [
        f"changed: {path}"
        for path in sorted(expected.keys() & existing)
        if (directory / path).read_text(encoding="utf-8") != expected[path]
    ]
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m qradar_mcp.fork.schema_export")
    parser.add_argument("--dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--check", action="store_true", help="only report differences")
    args = parser.parse_args(argv)
    if not args.check:
        write_snapshots(args.dir)
        return 0
    problems = snapshot_differences(args.dir)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


async def _list_tools(server: FastMCP) -> list[dict[str, Any]]:
    async with Client(server) as client:
        listed = await client.list_tools()
    return [
        {"name": tool.name, "inputSchema": tool.inputSchema, "outputSchema": tool.outputSchema}
        for tool in listed
    ]


def _offline_client() -> PlatformQRadarClient:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"schema export must not call QRadar ({request.url.path})")

    return PlatformQRadarClient(
        console_host="qradar.invalid",
        token=SecretStr("schema-export"),
        api_version="0.0",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)),
    )


def _existing(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {path.relative_to(directory).as_posix() for path in directory.rglob("*.json")}


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.exit(main())
