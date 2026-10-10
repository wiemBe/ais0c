# SPDX-License-Identifier: Apache-2.0
"""Bearer token check for callers of this server.

Upstream authenticates each HTTP request with QRadar credentials the caller sends. In the
platform the server holds its own QRadar token, so without a check anyone who reaches the
port could use it. Only the MCP Policy Gateway knows ``MCP_AUTH_TOKEN``; the network
should let only the gateway reach the server as well (architecture §13.4).
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable

from pydantic import SecretStr
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    """Rejects HTTP requests without ``Authorization: Bearer <MCP_AUTH_TOKEN>``."""

    def __init__(self, app: ASGIApp, *, token: SecretStr, exempt_paths: Iterable[str] = ()) -> None:
        self._app = app
        self._expected = token.get_secret_value().encode()
        self._exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] not in self._exempt_paths:
            if not self._authorized(scope):
                response = JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                scheme, _, credentials = value.partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(
                    credentials.strip(), self._expected
                )
        return False
