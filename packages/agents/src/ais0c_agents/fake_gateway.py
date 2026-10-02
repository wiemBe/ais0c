"""A gateway stand-in for unit tests and the harness's unit mode (agent-harness.md §5.A)."""

from collections import deque
from collections.abc import Mapping, Sequence

from ais0c_agents.gateway import GatewayClient, GatewayError
from ais0c_contracts import ToolIntent, ToolResult

CannedResponse = ToolResult | GatewayError


class FakeGatewayClient(GatewayClient):
    """Returns canned results per tool and records every intent it receives.

    A single response answers every call to its tool. A sequence answers one call per item, in
    order, and a call after the last item is an error. A GatewayError item is raised instead of
    returned, e.g. to simulate an unreachable gateway.

    Every intent is checked against the ToolIntent contract and must carry a non-blank reason
    and expected evidence. A violation raises ValueError: the fake is stricter than a real
    gateway, which would answer `denied`, so a test cannot miss it.
    """

    def __init__(
        self, responses: Mapping[str, CannedResponse | Sequence[CannedResponse]] | None = None
    ) -> None:
        self._responses: dict[str, CannedResponse | deque[CannedResponse]] = {
            tool_id: response if isinstance(response, CannedResponse) else deque(response)
            for tool_id, response in (responses or {}).items()
        }
        self.intents: list[ToolIntent] = []

    async def call(self, intent: ToolIntent) -> ToolResult:
        checked = ToolIntent.model_validate(intent.model_dump())
        for field in ("reason", "expected_evidence"):
            if not getattr(checked, field).strip():
                raise ValueError(f"ToolIntent.{field} is blank")
        self.intents.append(checked)

        response = self._responses.get(intent.tool_id)
        if isinstance(response, deque):
            if not response:
                raise LookupError(f"no canned response left for tool {intent.tool_id!r}")
            response = response.popleft()
        if response is None:
            raise LookupError(f"no canned response for tool {intent.tool_id!r}")
        if isinstance(response, GatewayError):
            raise response
        return response
