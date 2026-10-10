# SPDX-License-Identifier: Apache-2.0
"""Upstream tools whose output is a text report instead of JSON.

The platform consumes tool output as structured data (evidence, field filters), so these
tools return the QRadar object as JSON. List tools with a ``format_output`` switch need no
subclass: the platform always sends ``format_output=false`` (see ``tool_specs``).
"""

from __future__ import annotations

import json
from typing import Any

from qradar_mcp.tools.analytics.get_rule import GetRuleTool


class JsonGetRuleTool(GetRuleTool):
    """``get_rule`` returning the rule object instead of a formatted report."""

    @property
    def tool_group(self) -> str:
        return "analytics"

    def _format_rule(self, rule: dict[str, Any]) -> str:
        return json.dumps(rule)
