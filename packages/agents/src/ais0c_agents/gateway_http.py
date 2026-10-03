"""The HTTP client of the MCP Policy Gateway (services/mcp-gateway; T-011).

`HttpGatewayClient` implements `GatewayClient`: it posts the ToolIntent to the gateway with the
profile's token and returns the gateway's ToolResult, `denied` and `error` included. When no
ToolResult can be had it raises: `GatewayUnavailableError` when the gateway cannot be reached,
`GatewayError` for any other failure. The agent run then ends as `failed` (runner.py) and no
tool call is made (fail closed, architecture §13.3).

Every call belongs to an agent run: the gateway records it in `tool_calls` under the run's ID.
The ToolIntent has no field for it, so the client sends it in the `X-Ais0c-Run-Id` header and
takes it from `bind_run`:

    with bind_run(run_id):
        await agent.run(...)

A call outside `bind_run` raises GatewayError before anything is sent. The binding is a context
variable, so it follows the run into the tasks it starts.
"""

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

import httpx2
from pydantic import SecretStr, ValidationError

from ais0c_agents.gateway import GatewayClient, GatewayError, GatewayUnavailableError
from ais0c_agents.toolset import ToolsetProfile
from ais0c_contracts import ToolIntent, ToolResult

RUN_ID_HEADER: Final = "X-Ais0c-Run-Id"
TOOL_CALLS_PATH: Final = "/v1/tool-calls"
TOOLS_PATH: Final = "/v1/tools"
# Above the gateway's own limits: a quota wait (at most 60 s) plus one MCP call (60 s).
DEFAULT_TIMEOUT_SECONDS: Final = 180.0
_MAX_PROBLEM_LENGTH: Final = 200

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ais0c_gateway_run_id", default=None
)


@contextmanager
def bind_run(run_id: str) -> Iterator[None]:
    """Make gateway calls inside the block belong to the agent run `run_id`."""
    if not run_id:
        raise ValueError("run_id must not be empty")
    token = _run_id.set(run_id)
    try:
        yield
    finally:
        _run_id.reset(token)


def bound_run() -> str | None:
    """The run that gateway calls belong to here, if any."""
    return _run_id.get()


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
        run_id = bound_run()
        if run_id is None:
            raise GatewayError("no agent run is bound to this call; use bind_run(run_id)")
        response = await self._request(
            "POST",
            TOOL_CALLS_PATH,
            content=intent.model_dump_json().encode("utf-8"),
            headers={RUN_ID_HEADER: run_id, "Content-Type": "application/json"},
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
