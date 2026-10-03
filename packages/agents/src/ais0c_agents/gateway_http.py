"""The HTTP client of the MCP Policy Gateway (services/mcp-gateway; T-011).

`HttpGatewayClient` implements `GatewayClient`: it posts the ToolIntent to the gateway with the
profile's token and returns the gateway's ToolResult, `denied` and `error` included. When no
ToolResult can be had it raises: `GatewayUnavailableError` when the gateway cannot be reached,
`GatewayError` for any other failure. The agent run then ends as `failed` (runner.py) and no
tool call is made (fail closed, architecture §13.3).

Every call belongs to an agent run: the ToolIntent names it in `run_id`, and the gateway records
the call in `tool_calls` under that run (contracts v0.2, decision T-19).
"""

from typing import Final

import httpx2
from pydantic import SecretStr, ValidationError

from ais0c_agents.gateway import GatewayClient, GatewayError, GatewayUnavailableError
from ais0c_agents.toolset import ToolsetProfile
from ais0c_contracts import ToolIntent, ToolResult

TOOL_CALLS_PATH: Final = "/v1/tool-calls"
TOOLS_PATH: Final = "/v1/tools"
# Above the gateway's own limits: a quota wait (at most 60 s) plus one MCP call (60 s).
DEFAULT_TIMEOUT_SECONDS: Final = 180.0
_MAX_PROBLEM_LENGTH: Final = 200


class HttpGatewayClient(GatewayClient):
    """Calls the gateway at `base_url` with one profile's token."""

    def __init__(
        self,
        base_url: str,
        token: SecretStr | str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an http(s) URL")
        self._base_url = base_url.rstrip("/")
        self._token = token if isinstance(token, SecretStr) else SecretStr(token)
        if not self._token.get_secret_value():
            raise ValueError("token must not be empty")
        self._timeout = timeout_seconds
        self._transport = transport

    def __repr__(self) -> str:
        return f"HttpGatewayClient(base_url={self._base_url!r})"

    async def call(self, intent: ToolIntent) -> ToolResult:
        response = await self._request(
            "POST",
            TOOL_CALLS_PATH,
            content=intent.model_dump_json().encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            return ToolResult.model_validate_json(response.content)
        except ValidationError:
            raise GatewayError("the gateway's answer is not a ToolResult") from None

    async def fetch_toolset(self) -> ToolsetProfile:
        """The token's profile with its tools, descriptions and schemas, as the gateway serves it."""
        response = await self._request("GET", TOOLS_PATH)
        try:
            return ToolsetProfile.model_validate_json(response.content)
        except ValidationError:
            raise GatewayError("the gateway's tool list is not a ToolsetProfile") from None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx2.Response:
        all_headers = {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            **(headers or {}),
        }
        try:
            async with httpx2.AsyncClient(
                base_url=self._base_url,
                timeout=httpx2.Timeout(self._timeout),
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = await client.request(method, path, content=content, headers=all_headers)
        except (httpx2.ConnectError, httpx2.ConnectTimeout) as error:
            raise GatewayUnavailableError(
                f"the gateway cannot be reached ({type(error).__name__})"
            ) from None
        except httpx2.HTTPError as error:
            raise GatewayError(f"the gateway call failed ({type(error).__name__})") from None
        if response.status_code != 200:
            raise GatewayError(
                f"the gateway answered HTTP {response.status_code}{_problem(response)}"
            )
        return response


def _problem(response: httpx2.Response) -> str:
    """`: <title>` from an application/problem+json answer, if there is one."""
    try:
        title = response.json().get("title")
    except (ValueError, AttributeError):
        return ""
    if not isinstance(title, str) or not title.isprintable():
        return ""
    return f": {title[:_MAX_PROBLEM_LENGTH]}"
