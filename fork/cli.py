# SPDX-License-Identifier: Apache-2.0
"""``qradar-mcp-fork --profile <name>``: the container entry point.

Startup order, each step fail-closed:

1. ``--profile`` must name a known profile (argparse exits with status 2 otherwise).
2. Settings come from the environment; a missing or ambiguous secret stops the process.
3. Logging is configured with the secrets, before anything else can log.
4. The QRadar API version is negotiated; without a known version the server does not start.
5. The profile's tools are checked and registered, then the streamable HTTP app is served.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

import uvicorn

from qradar_mcp.fork import __version__
from qradar_mcp.fork.api_version import ApiVersionError, discover_api_version
from qradar_mcp.fork.app import MCP_PATH, create_http_app, create_server
from qradar_mcp.fork.logging_setup import configure_logging
from qradar_mcp.fork.settings import SettingsError, load_settings
from qradar_mcp.fork.tool_profiles import PROFILE_NAMES, ProfileError

logger = logging.getLogger("qradar_mcp.fork")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qradar-mcp-fork",
        description="QRadar MCP server for the AI SOC platform (streamable HTTP).",
    )
    parser.add_argument("--profile", required=True, choices=PROFILE_NAMES)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"qradar-mcp-fork: configuration error: {exc}", file=sys.stderr)
        return 1
    configure_logging(settings.log_level, settings.secrets())

    try:
        api_version = discover_api_version(settings)
    except ApiVersionError as exc:
        logger.error("Refusing to start: %s", exc)
        return 1
    if api_version.deprecated:
        logger.warning("QRadar marks API version %s as deprecated", api_version.version)
    logger.info(
        "Using QRadar API version %s (known versions: %s)",
        api_version.version,
        ", ".join(settings.known_api_versions),
    )

    try:
        server = create_server(args.profile, settings, api_version.version)
    except ProfileError as exc:
        logger.error("Refusing to start: %s", exc)
        return 1
    logger.info(
        "Serving profile %s on http://%s:%s%s", args.profile, args.host, args.port, MCP_PATH
    )
    uvicorn.run(
        create_http_app(server, settings),
        host=args.host,
        port=args.port,
        log_config=None,
        server_header=False,
    )
    return 0


def run() -> None:
    """Console script entry point."""
    sys.exit(main())
