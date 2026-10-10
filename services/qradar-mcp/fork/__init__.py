# SPDX-License-Identifier: Apache-2.0
"""Platform additions to the upstream IBM QRadar MCP server.

Everything that differs from upstream in behaviour lives in this package, so a monthly
upstream review only has to look at upstream files plus the few spots where this package
plugs into them (see README.md, "Upstream ile senkronizasyon").

Entry point: ``python -m qradar_mcp.fork --profile <qradar-read|qradar-note>``.
"""

__version__ = "0.1.0"
