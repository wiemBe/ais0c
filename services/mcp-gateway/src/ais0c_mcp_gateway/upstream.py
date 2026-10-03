"""Calls to the MCP servers: the MCP Python SDK client over streamable HTTP (architecture §13.4).

Each call opens its own MCP session with the initialize handshake (the fork serves stateless
HTTP), calls one tool and closes. One deadline covers connecting and the call, and a call is
never retried: an MCP server that cannot be reached or does not answer in time gives an
`error` result (fail closed, §13.3). The client keeps the SDK's defaults for requests from the
server: sampling, elicitation and roots are refused, so an MCP server cannot make the gateway
call a model.

Only the structured result of a call is used. Tool metadata is never read from the server; it
comes from the registry (§13.3). The SDK's `call_tool` would list the server's tools to check
the result against the server's own output schema, so the gateway sends the `tools/call`
request itself: no `tools/list` is ever sent.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import (
    REQUEST_TIMEOUT,
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    Implementation,
    TextContent,
)
from pydantic import JsonValue, SecretStr, TypeAdapter, ValidationError

# Extra time for the outer deadline, so the client's own timeouts fire first.
_DEADLINE_GRACE_SECONDS: Final = 1.0
MAX_DETAIL_LENGTH: Final = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_STRUCTURED: Final = TypeAdapter(dict[str, JsonValue])
_CLIENT_INFO: Final = Implementation(name="ais0c-mcp-gateway", version="0.1.0")


class UpstreamFailure(StrEnum):
    """Why a call to an MCP server produced no result. Returned to the agent."""

    UNREACHABLE = "upstream_unreachable"
    TIMEOUT = "upstream_timeout"
    ERROR = "upstream_error"
    INVALID_RESULT = "upstream_result_invalid"


@dataclass(frozen=True)
class UpstreamOutcome:
    """Either the tool's structured result or why there is none."""

    structured: dict[str, JsonValue] | None
    failure: UpstreamFailure | None = None
    # Short and single-line; for the agent and the logs. Never holds a secret the gateway knows
    # (the caller redacts it), but it may echo the server's text.
    detail: str = ""

    @classmethod
    def ok(cls, structured: dict[str, JsonValue]) -> "UpstreamOutcome":
        return cls(structured=structured)

    @classmethod
    def failed(cls, failure: UpstreamFailure, detail: str) -> "UpstreamOutcome":
        return cls(structured=None, failure=failure, detail=one_line(detail))


class Upstream(Protocol):
    async def call_tool(self, name: str, arguments: Mapping[str, JsonValue]) -> UpstreamOutcome:
        """Call `name` once. Never raises for a failed call; the outcome says what went wrong."""
        ...


class McpUpstream:
    """One MCP instance, e.g. qradar-mcp-read."""

    def __init__(
        self,
        url: str,
        token: SecretStr,
        *,
        timeout_seconds: float,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout_seconds
        self._transport = transport

    def __repr__(self) -> str:
        return f"McpUpstream(url={self._url!r})"

    async def call_tool(self, name: str, arguments: Mapping[str, JsonValue]) -> UpstreamOutcome:
        try:
            with anyio.fail_after(self._timeout + _DEADLINE_GRACE_SECONDS):
                async with (
                    httpx2.AsyncClient(
                        headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
                        timeout=httpx2.Timeout(self._timeout),
                        transport=self._transport,
                        follow_redirects=False,
                    ) as http,
                    Client(
                        streamable_http_client(self._url, http_client=http),
                        mode="legacy",
                        cache=None,
                        read_timeout_seconds=self._timeout,
                        client_info=_CLIENT_INFO,
                    ) as client,
                ):
                    result = await client.session.send_request(
                        CallToolRequest(
                            params=CallToolRequestParams(name=name, arguments=dict(arguments))
                        ),
                        CallToolResult,
                        request_read_timeout_seconds=self._timeout,
                    )
        except TimeoutError:
            return UpstreamOutcome.failed(
                UpstreamFailure.TIMEOUT, "the MCP server did not answer in time"
            )
        except Exception as error:  # noqa: BLE001  # any failure is an `error` result (fail closed)
            failure = classify(error)
            return UpstreamOutcome.failed(failure, _FAILURE_DETAIL[failure])

        if result.is_error:
            text = " ".join(
                block.text for block in result.content if isinstance(block, TextContent)
            )
            return UpstreamOutcome.failed(UpstreamFailure.ERROR, text or f"{name} failed")
        try:
            structured = _STRUCTURED.validate_python(result.structured_content)
        except ValidationError:
            return UpstreamOutcome.failed(
                UpstreamFailure.INVALID_RESULT, f"{name} returned no structured JSON object"
            )
        return UpstreamOutcome.ok(structured)


_FAILURE_DETAIL: Final = {
    UpstreamFailure.UNREACHABLE: "the MCP server cannot be reached",
    UpstreamFailure.TIMEOUT: "the MCP server did not answer in time",
    UpstreamFailure.ERROR: "the MCP call failed",
    UpstreamFailure.INVALID_RESULT: "the MCP server returned an invalid response",
}


def classify(error: BaseException) -> UpstreamFailure:
    """Map an exception from the MCP client, possibly an exception group, to a failure."""
    leaves = list(_leaves(error))
    if any(isinstance(leaf, httpx2.ConnectError | ConnectionError) for leaf in leaves):
        return UpstreamFailure.UNREACHABLE
    if any(
        isinstance(leaf, httpx2.TimeoutException | TimeoutError)
        or (isinstance(leaf, MCPError) and leaf.code == REQUEST_TIMEOUT)
        for leaf in leaves
    ):
        return UpstreamFailure.TIMEOUT
    return UpstreamFailure.ERROR


def _leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaves(inner)]
    return [error]


def one_line(text: str, limit: int = MAX_DETAIL_LENGTH) -> str:
    """`text` without control characters and runs of whitespace, cut to `limit` characters."""
    flat = " ".join(_CONTROL.sub(" ", text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
