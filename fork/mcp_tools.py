# SPDX-License-Identifier: Apache-2.0
"""Exposing upstream ``MCPTool`` objects as FastMCP tools with a strict contract.

Upstream's adapter (tools/fastmcp_adapter.py) builds a Python signature from the input
schema and returns the tool's text as is. The platform needs more than that:

- Arguments are validated against the advertised JSON Schema; unknown ones are rejected.
- The output is JSON, returned as MCP structured content that matches the output schema.
- An upstream error becomes an MCP error result (``isError``) instead of ordinary text.
- Secret values are scrubbed from everything the tool returns.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import Tool, ToolResult
from jsonschema import Draft202012Validator
from pydantic import PrivateAttr

from qradar_mcp.fork.redaction import Redactor
from qradar_mcp.fork.tool_specs import JsonSchema, ToolSpec
from qradar_mcp.tools.base import MCPTool


def exposed_input_schema(upstream_schema: Mapping[str, Any], spec: ToolSpec) -> JsonSchema:
    """Upstream input schema with the platform's patches applied."""
    schema: JsonSchema = copy.deepcopy(dict(upstream_schema))
    properties: dict[str, Any] = schema.setdefault("properties", {})
    for name in spec.fixed_arguments:
        properties.pop(name, None)
    if "required" in schema:
        schema["required"] = [n for n in schema["required"] if n not in spec.fixed_arguments]
    for name, patch in spec.property_patches.items():
        if name not in properties:
            raise ValueError(f"cannot patch unknown input property {name!r}")
        properties[name] = {**properties[name], **patch}
    schema["additionalProperties"] = False
    return schema


class PlatformTool(Tool):
    """Runs one upstream tool behind a validated, structured MCP interface."""

    _upstream: MCPTool = PrivateAttr()
    _spec: ToolSpec = PrivateAttr()
    _validator: Draft202012Validator = PrivateAttr()
    _redactor: Redactor = PrivateAttr()

    @classmethod
    def wrap(cls, upstream: MCPTool, spec: ToolSpec, redactor: Redactor) -> PlatformTool:
        parameters = exposed_input_schema(upstream.input_schema, spec)
        Draft202012Validator.check_schema(parameters)
        Draft202012Validator.check_schema(spec.output_schema)
        tool = cls(
            name=upstream.name,
            description=spec.description or upstream.description,
            parameters=parameters,
            output_schema=copy.deepcopy(spec.output_schema),
            meta={"approval_required": upstream.approval_required},
        )
        tool._upstream = upstream
        tool._spec = spec
        tool._validator = Draft202012Validator(parameters)
        tool._redactor = redactor
        return tool

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        error = next(iter(sorted(self._validator.iter_errors(arguments), key=str)), None)
        if error is not None:
            where = "/".join(str(part) for part in error.absolute_path) or "arguments"
            raise ToolError(
                f"Invalid arguments for {self.name} ({where}): {self._redactor.text(error.message)}"
            )
        call_arguments = self._with_defaults(arguments)
        call_arguments.update(self._spec.fixed_arguments)
        try:
            result = await self._upstream.execute(call_arguments)
        except httpx.HTTPError as exc:
            raise ToolError(f"QRadar request failed: {type(exc).__name__}") from None
        text = self._redactor.text(_result_text(result))
        if result.get("isError"):
            raise ToolError(text or f"{self.name} failed")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise ToolError(f"{self.name} returned output that is not JSON") from None
        if self._spec.list_output:
            if not isinstance(data, list):
                raise ToolError(f"{self.name} returned {type(data).__name__}, expected a list")
            data = {"items": data}
        elif not isinstance(data, dict):
            raise ToolError(f"{self.name} returned {type(data).__name__}, expected an object")
        return ToolResult(structured_content=data)

    def _with_defaults(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        merged = dict(arguments)
        for name, prop in self.parameters.get("properties", {}).items():
            if name not in merged and isinstance(prop, dict) and "default" in prop:
                merged[name] = copy.deepcopy(prop["default"])
        return merged


def _result_text(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            return text
    return ""
