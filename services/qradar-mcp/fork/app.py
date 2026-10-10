# SPDX-License-Identifier: Apache-2.0
"""Building the MCP server and its streamable HTTP application for one profile."""

from __future__ import annotations

import httpx
from fastmcp import FastMCP
from fastmcp.server.http import StarletteWithLifespan
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from qradar_mcp.client.qradar_rest_client import QRadarRestClient
from qradar_mcp.fork import __version__
from qradar_mcp.fork.ariel import (
    OwnedCreateArielSearchTool,
    OwnedDeleteArielSearchTool,
    SearchOwnership,
)
from qradar_mcp.fork.auth import BearerTokenMiddleware
from qradar_mcp.fork.client import PlatformQRadarClient
from qradar_mcp.fork.json_output import JsonGetRuleTool
from qradar_mcp.fork.mcp_tools import PlatformTool
from qradar_mcp.fork.redaction import Redactor
from qradar_mcp.fork.settings import Settings
from qradar_mcp.fork.tool_profiles import (
    ProfileError,
    load_profile_toggles,
    select_profile_tools,
    upstream_tool_catalog,
)
from qradar_mcp.fork.tool_specs import TOOL_SPECS
from qradar_mcp.tools.analytics.get_rule import GetRuleTool
from qradar_mcp.tools.ariel.create_ariel_search import CreateArielSearchTool
from qradar_mcp.tools.ariel.delete_ariel_search import DeleteArielSearchTool
from qradar_mcp.tools.base import MCPTool

SERVER_NAME = "qradar-mcp"
MCP_PATH = "/mcp"
HEALTH_PATH = "/healthz"


def build_server(
    profile: str, client: QRadarRestClient, redactor: Redactor | None = None
) -> FastMCP:
    """FastMCP server with exactly the tools of ``profile``, all using ``client``."""
    selected = select_profile_tools(profile, load_profile_toggles(profile), upstream_tool_catalog())
    redactor = redactor or Redactor(())
    ownership = SearchOwnership()
    server = FastMCP(SERVER_NAME, version=__version__)
    for upstream in selected:
        tool = _platform_variant(upstream, ownership)
        spec = TOOL_SPECS.get(tool.name)
        if spec is None:
            raise ProfileError(
                f"tool {tool.name!r} of profile {profile!r} has no entry in TOOL_SPECS"
            )
        # Per-instance client: MCPTool.set_qradar_client() would set it for every tool class.
        tool.client = client
        server.add_tool(PlatformTool.wrap(tool, spec, redactor))

    @server.custom_route(HEALTH_PATH, methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return server


def create_server(
    profile: str,
    settings: Settings,
    api_version: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastMCP:
    """The server for ``profile`` talking to the configured QRadar console."""
    http_client = httpx.AsyncClient(
        verify=settings.ssl_verify(),
        timeout=settings.timeout_seconds,
        transport=transport,
        follow_redirects=False,
    )
    client = PlatformQRadarClient(
        console_host=settings.console_host,
        token=settings.qradar_token,
        api_version=api_version,
        http_client=http_client,
    )
    return build_server(profile, client, Redactor(settings.secrets()))


def create_http_app(server: FastMCP, settings: Settings) -> StarletteWithLifespan:
    """Streamable HTTP application; every path except the health check needs the bearer token."""
    return server.http_app(
        path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        middleware=[
            Middleware(
                BearerTokenMiddleware,
                token=settings.mcp_auth_token,
                exempt_paths=(HEALTH_PATH,),
            )
        ],
    )


def _platform_variant(tool: MCPTool, ownership: SearchOwnership) -> MCPTool:
    """Swap upstream tools whose platform behaviour differs for the fork's subclass."""
    if type(tool) is CreateArielSearchTool:
        return OwnedCreateArielSearchTool(ownership)
    if type(tool) is DeleteArielSearchTool:
        return OwnedDeleteArielSearchTool(ownership)
    if type(tool) is GetRuleTool:
        return JsonGetRuleTool()
    return tool
