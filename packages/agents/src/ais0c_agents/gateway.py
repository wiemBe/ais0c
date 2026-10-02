"""The agents' only way to a security product: the MCP Policy Gateway (architecture §13).

An agent sends a ToolIntent and gets a ToolResult back. The gateway checks the intent against
the agent's profile, runs the call and records the evidence; a denied call is a ToolResult with
status `denied`, not an exception. The HTTP client is written in T-011.
"""

from abc import ABC, abstractmethod

from ais0c_contracts import ToolIntent, ToolResult


class GatewayError(Exception):
    """The gateway could not answer. The agent run ends with status `failed`."""


class GatewayUnavailableError(GatewayError):
    """The gateway cannot be reached; no tool call is made (fail closed, §13.3)."""


class GatewayClient(ABC):
    @abstractmethod
    async def call(self, intent: ToolIntent) -> ToolResult:
        """Send one tool call. Raises GatewayError when no ToolResult can be returned."""
