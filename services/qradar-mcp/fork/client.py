# SPDX-License-Identifier: Apache-2.0
"""QRadar REST client for the platform deployment.

Upstream takes credentials per request, from the MCP caller's headers or from a local
``config.json``, and sends a ``Version`` header only when a tool asks for one. In the
platform the caller is the MCP Policy Gateway, which never holds QRadar credentials
(architecture §13.4). So every request carries this server's own token and the API version
selected at startup, whatever the tool or the caller passes.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr

from qradar_mcp.client.qradar_rest_client import QRadarRestClient


class PlatformQRadarClient(QRadarRestClient):
    """Fixed console, service token from the environment, pinned API version."""

    def __init__(
        self,
        *,
        console_host: str,
        token: SecretStr,
        api_version: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        super().__init__(client=http_client)
        # Drop anything upstream may have loaded from a local config.json.
        self.config = None
        self._local_mode = False
        self._authorized_service_token = None
        self._sec_token = None
        self._csrf_token = None
        self._url = console_host
        self._token = token
        self.api_version = api_version

    def _add_headers(
        self, headers: dict[str, str] | None, version: str | None = None
    ) -> dict[str, Any]:
        """Authenticate with the server token and pin the API version.

        ``version`` is accepted for signature compatibility and ignored: a tool cannot
        move a request to another API version than the one selected at startup.
        """
        del version
        merged: dict[str, Any] = dict(headers or {})
        merged["SEC"] = self._token.get_secret_value()
        merged["Version"] = self.api_version
        merged["Accept"] = "application/json"
        return merged
