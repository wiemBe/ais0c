# SPDX-License-Identifier: Apache-2.0
"""Runtime settings, read from environment variables only.

The container image needs no config file. Each secret comes either from a variable or from
a file (a Docker secret), never from both, and is held as a ``SecretStr`` so it does not
show up in reprs or tracebacks.
"""

from __future__ import annotations

import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr

DEFAULT_KNOWN_API_VERSIONS: tuple[str, ...] = ("27.0", "29.0")
DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_MCP_AUTH_TOKEN_LENGTH = 32
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_VERSION_PATTERN = re.compile(r"^\d+\.\d+$")
_TRUE = ("true", "1", "yes")
_FALSE = ("false", "0", "no")


class SettingsError(ValueError):
    """The environment does not describe a usable configuration."""


@dataclass(frozen=True)
class Settings:
    """Everything the server needs to reach QRadar and to accept callers."""

    console_host: str
    """QRadar console as ``host`` or ``host:port``; the API is always reached over HTTPS."""
    qradar_token: SecretStr
    """Authorized service token sent as the ``SEC`` header."""
    mcp_auth_token: SecretStr
    """Bearer token the MCP Policy Gateway presents to this server."""
    verify_tls: bool
    ca_bundle: str | None
    known_api_versions: tuple[str, ...]
    timeout_seconds: float
    log_level: str

    def secrets(self) -> tuple[str, ...]:
        """Secret values that must never appear in logs or tool output."""
        return (self.qradar_token.get_secret_value(), self.mcp_auth_token.get_secret_value())

    def ssl_verify(self) -> ssl.SSLContext | bool:
        """The ``verify`` argument for httpx clients."""
        if not self.verify_tls:
            return False
        if self.ca_bundle:
            return ssl.create_default_context(cafile=self.ca_bundle)
        return True


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build settings from ``environ`` (defaults to ``os.environ``)."""
    env = os.environ if environ is None else environ
    qradar_token = _read_secret(env, "QRADAR_AUTH_TOKEN")
    mcp_auth_token = _read_secret(env, "MCP_AUTH_TOKEN")
    if len(mcp_auth_token.get_secret_value()) < MIN_MCP_AUTH_TOKEN_LENGTH:
        raise SettingsError(
            f"MCP_AUTH_TOKEN must be at least {MIN_MCP_AUTH_TOKEN_LENGTH} characters long"
        )
    if mcp_auth_token.get_secret_value() == qradar_token.get_secret_value():
        raise SettingsError("MCP_AUTH_TOKEN must differ from the QRadar token")
    verify_tls, ca_bundle = _tls(env)
    return Settings(
        console_host=_console_host(env.get("QRADAR_CONSOLE_FQDN", "")),
        qradar_token=qradar_token,
        mcp_auth_token=mcp_auth_token,
        verify_tls=verify_tls,
        ca_bundle=ca_bundle,
        known_api_versions=_known_api_versions(env.get("QRADAR_API_VERSIONS")),
        timeout_seconds=_timeout(env.get("MCP_HTTPX_TIMEOUT")),
        log_level=_log_level(env.get("LOG_LEVEL")),
    )


def _read_secret(env: Mapping[str, str], name: str) -> SecretStr:
    value = env.get(name, "")
    path = env.get(f"{name}_FILE", "")
    if value and path:
        raise SettingsError(f"set either {name} or {name}_FILE, not both")
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise SettingsError(f"cannot read {name}_FILE ({path}): {exc.strerror}") from None
    value = value.strip()
    if not value:
        raise SettingsError(f"{name} or {name}_FILE is required")
    return SecretStr(value)


def _console_host(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise SettingsError("QRADAR_CONSOLE_FQDN is required")
    if "://" in raw:
        parts = urlsplit(raw)
        if parts.scheme != "https":
            raise SettingsError("QRADAR_CONSOLE_FQDN must use https; the QRadar API is HTTPS only")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise SettingsError("QRADAR_CONSOLE_FQDN must be a host name with an optional port")
        host = parts.netloc
    else:
        host = raw.rstrip("/")
    if not host or any(char in host for char in "/@?#\\") or any(c.isspace() for c in host):
        raise SettingsError("QRADAR_CONSOLE_FQDN must be a host name with an optional port")
    return host


def _tls(env: Mapping[str, str]) -> tuple[bool, str | None]:
    flag = env.get("QRADAR_VERIFY_SSL", "true").strip().lower()
    if flag in _FALSE:
        return False, None
    if flag not in _TRUE:
        raise SettingsError("QRADAR_VERIFY_SSL must be true or false")
    ca_bundle = env.get("REQUESTS_CA_BUNDLE", "").strip() or None
    if ca_bundle and not Path(ca_bundle).is_file():
        raise SettingsError(f"REQUESTS_CA_BUNDLE does not point to a file: {ca_bundle}")
    return True, ca_bundle


def _known_api_versions(raw: str | None) -> tuple[str, ...]:
    if raw is None or not raw.strip():
        return DEFAULT_KNOWN_API_VERSIONS
    versions: list[str] = []
    for item in raw.split(","):
        version = item.strip()
        if not version:
            continue
        if not _VERSION_PATTERN.match(version):
            raise SettingsError(f"QRADAR_API_VERSIONS has an invalid version: {version!r}")
        if version not in versions:
            versions.append(version)
    if not versions:
        raise SettingsError("QRADAR_API_VERSIONS lists no versions")
    return tuple(versions)


def _timeout(raw: str | None) -> float:
    if raw is None or not raw.strip():
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        raise SettingsError("MCP_HTTPX_TIMEOUT must be a number of seconds") from None
    if timeout <= 0:
        raise SettingsError("MCP_HTTPX_TIMEOUT must be positive")
    return timeout


def _log_level(raw: str | None) -> str:
    level = (raw or "INFO").strip().upper()
    if level not in LOG_LEVELS:
        raise SettingsError(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")
    return level
