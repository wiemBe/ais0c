"""Gateway settings: environment variables and secret files (architecture §25).

Environment variables:

| Variable | Meaning | Default |
|---|---|---|
| `AIS0C_DATABASE_URL` | Application database (packages/storage) | none |
| `AIS0C_GATEWAY_CONFIG_DIR` | Directory with `connectors/` and `policies/` | `config` |
| `AIS0C_GATEWAY_CONNECTORS` | Comma-separated connector IDs | `qradar` |
| `AIS0C_GATEWAY_SECRETS_DIR` | Directory of the secret files below | `/run/secrets` |
| `AIS0C_GATEWAY_UPSTREAM_URL_<INSTANCE>` | MCP endpoint of an instance, e.g. `..._QRADAR_MCP_READ` | none |
| `AIS0C_GATEWAY_HOST`, `AIS0C_GATEWAY_PORT` | Listen address | `0.0.0.0`, `8080` |

Secret files (Docker secrets), one token per file:

- `gateway-token-<profile>`: the token agents of that profile present. A profile without a
  file is disabled. Tokens are at least 32 characters and unique.
- `mcp-token-<instance>`: the bearer token the gateway presents to that MCP instance.

Token values never appear in errors or logs.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import SecretStr

MIN_TOKEN_LENGTH = 32
_TOKEN_CHARACTERS = re.compile(r"[\x21-\x7e]+")

CONFIG_DIR_ENV = "AIS0C_GATEWAY_CONFIG_DIR"
CONNECTORS_ENV = "AIS0C_GATEWAY_CONNECTORS"
SECRETS_DIR_ENV = "AIS0C_GATEWAY_SECRETS_DIR"
UPSTREAM_URL_ENV_PREFIX = "AIS0C_GATEWAY_UPSTREAM_URL_"
HOST_ENV = "AIS0C_GATEWAY_HOST"
PORT_ENV = "AIS0C_GATEWAY_PORT"


class SettingsError(ValueError):
    """A setting or secret file is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    config_dir: Path
    connectors: tuple[str, ...]
    secrets_dir: Path
    host: str
    port: int
    upstream_urls: Mapping[str, str] = field(default_factory=dict[str, str])
    """MCP endpoint per instance name, e.g. qradar-mcp-read."""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        connectors = tuple(
            item.strip() for item in env.get(CONNECTORS_ENV, "qradar").split(",") if item.strip()
        )
        if not connectors:
            raise SettingsError(f"{CONNECTORS_ENV} names no connector")
        try:
            port = int(env.get(PORT_ENV, "8080"))
        except ValueError:
            raise SettingsError(f"{PORT_ENV} must be a port number") from None
        upstream_urls = {
            name.removeprefix(UPSTREAM_URL_ENV_PREFIX).lower().replace("_", "-"): value.strip()
            for name, value in env.items()
            if name.startswith(UPSTREAM_URL_ENV_PREFIX) and value.strip()
        }
        return cls(
            config_dir=Path(env.get(CONFIG_DIR_ENV, "config")),
            connectors=connectors,
            secrets_dir=Path(env.get(SECRETS_DIR_ENV, "/run/secrets")),
            host=env.get(HOST_ENV, "0.0.0.0"),  # noqa: S104  # the container's own interface
            port=port,
            upstream_urls=upstream_urls,
        )

    def upstream_url(self, instance: str) -> str:
        url = self.upstream_urls.get(instance)
        if url is None:
            variable = UPSTREAM_URL_ENV_PREFIX + instance.upper().replace("-", "_")
            raise SettingsError(f"{variable} is not set")
        if not url.startswith(("http://", "https://")):
            raise SettingsError(f"the MCP endpoint of {instance} must be an http(s) URL")
        return url

    def profile_token(self, profile: str) -> SecretStr | None:
        """The profile's token, or None when the profile has no token file (disabled)."""
        return read_token(self.secrets_dir / f"gateway-token-{profile}", required=False)

    def upstream_token(self, instance: str) -> SecretStr:
        token = read_token(self.secrets_dir / f"mcp-token-{instance}", required=True)
        if token is None:  # read_token raises for a missing required file
            raise SettingsError(f"secret file mcp-token-{instance} is missing")
        return token


def read_token(path: Path, *, required: bool) -> SecretStr | None:
    """A token from a secret file: printable ASCII without spaces, at least 32 characters."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if required:
            raise SettingsError(f"secret file {path.name} is missing") from None
        return None
    except (OSError, UnicodeDecodeError):
        raise SettingsError(f"secret file {path.name} cannot be read") from None
    if len(value) < MIN_TOKEN_LENGTH or not _TOKEN_CHARACTERS.fullmatch(value):
        raise SettingsError(
            f"secret file {path.name} must hold one token of at least {MIN_TOKEN_LENGTH} "
            "printable characters"
        )
    return SecretStr(value)
