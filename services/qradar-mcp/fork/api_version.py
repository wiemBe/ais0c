# SPDX-License-Identifier: Apache-2.0
"""QRadar REST API version discovery (architecture §11.1).

At startup the server asks QRadar which API versions it serves, picks the highest version
that is also in the configured list of known versions, and pins every later request to it
with the ``Version`` header. If QRadar serves none of the known versions the server does
not start: tool schemas and contract tests are only valid for versions we have tested.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from qradar_mcp.fork.settings import Settings

VERSIONS_PATH = "help/versions"


class ApiVersionError(RuntimeError):
    """No usable QRadar API version could be selected."""


@dataclass(frozen=True)
class ApiVersion:
    version: str
    deprecated: bool


def version_key(version: str) -> tuple[int, ...]:
    """Sort key for versions such as ``"27.0"``."""
    return tuple(int(part) for part in version.split("."))


def select_api_version(available: object, known: Sequence[str]) -> ApiVersion:
    """Return the highest version in ``known`` that QRadar lists and has not removed.

    ``available`` is the parsed body of ``GET /api/help/versions``: a list of objects with
    at least ``version`` and usually ``deprecated`` and ``removed`` flags.
    """
    if not isinstance(available, list):
        raise ApiVersionError("unexpected response from /api/help/versions: not a list")
    offered: dict[str, bool] = {}
    for entry in available:
        if not isinstance(entry, dict):
            continue
        version = entry.get("version")
        if not isinstance(version, str) or entry.get("removed") is True:
            continue
        offered[version.strip()] = entry.get("deprecated") is True
    usable = [version for version in known if version in offered]
    if not usable:
        raise ApiVersionError(
            f"QRadar serves none of the known API versions {list(known)}; "
            f"it offers {sorted(offered)}"
        )
    best = max(usable, key=version_key)
    return ApiVersion(version=best, deprecated=offered[best])


def fetch_api_versions(settings: Settings, transport: httpx.BaseTransport | None = None) -> object:
    """Call ``GET /api/help/versions`` and return the parsed JSON body."""
    try:
        with httpx.Client(
            base_url=f"https://{settings.console_host}/api/",
            verify=settings.ssl_verify(),
            timeout=settings.timeout_seconds,
            transport=transport,
        ) as client:
            response = client.get(
                VERSIONS_PATH,
                headers={
                    "SEC": settings.qradar_token.get_secret_value(),
                    "Accept": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise ApiVersionError(f"cannot reach QRadar: {type(exc).__name__}: {exc}") from None
    if response.status_code in (401, 403):
        raise ApiVersionError(
            f"QRadar rejected the API token (HTTP {response.status_code} from /api/help/versions)"
        )
    if response.status_code != 200:
        raise ApiVersionError(f"/api/help/versions returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise ApiVersionError("/api/help/versions did not return JSON") from None


def discover_api_version(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> ApiVersion:
    """Select the API version every request of this process will use."""
    return select_api_version(fetch_api_versions(settings, transport), settings.known_api_versions)
