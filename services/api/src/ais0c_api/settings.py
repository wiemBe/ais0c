"""Settings of the analyst API (T-028, T-63).

Environment variables:

| Variable | Meaning | Default |
|---|---|---|
| `AIS0C_DATABASE_URL` | Application database (packages/storage) | none |
| `TEMPORAL_ADDRESS` | Temporal frontend, for `POST /catalog/sync` | `127.0.0.1:7233` |
| `TEMPORAL_NAMESPACE` | Namespace | `default` |
| `AIS0C_API_AUTH` | Authentication mode; today only `dev` (T-035 adds `oidc`) | none |
| `AIS0C_API_DEV_USERS_FILE` | The dev users file `dev` mode reads | none in `dev` |
| `AIS0C_API_HOST` | Listen address | `127.0.0.1` |
| `AIS0C_API_PORT` | Listen port | `8000` |

`AIS0C_API_AUTH` has no default and only one accepted value: without it, or with a value the
code does not know, the service refuses to start. Choosing the mode explicitly is what keeps
the dev mode out of production (T-63 (1)).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

DATABASE_URL_ENV: Final = "AIS0C_DATABASE_URL"
TEMPORAL_ADDRESS_ENV: Final = "TEMPORAL_ADDRESS"
TEMPORAL_NAMESPACE_ENV: Final = "TEMPORAL_NAMESPACE"
AUTH_ENV: Final = "AIS0C_API_AUTH"
DEV_USERS_FILE_ENV: Final = "AIS0C_API_DEV_USERS_FILE"
HOST_ENV: Final = "AIS0C_API_HOST"
PORT_ENV: Final = "AIS0C_API_PORT"


class AuthMode(StrEnum):
    """`AIS0C_API_AUTH`. `oidc` is not implemented yet; T-035 adds it."""

    DEV = "dev"


class SettingsError(ValueError):
    """A setting, or a file the chosen mode needs, is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    auth_mode: AuthMode
    dev_users_file: Path | None
    temporal_address: str
    temporal_namespace: str
    host: str
    port: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """The settings from the environment (`environ` defaults to `os.environ`).

        Raises `SettingsError` when `AIS0C_API_AUTH` is unset or names a mode this build does
        not have, when it is `dev` without a users file, or when the port is not a number.
        """
        env = os.environ if environ is None else environ
        raw_mode = env.get(AUTH_ENV, "").strip()
        if not raw_mode:
            raise SettingsError(f"{AUTH_ENV} is not set; it must name the authentication mode")
        try:
            mode = AuthMode(raw_mode)
        except ValueError:
            known = ", ".join(sorted(item.value for item in AuthMode))
            raise SettingsError(
                f"{AUTH_ENV}={raw_mode!r} is not a known authentication mode; expected one of: "
                f"{known}"
            ) from None
        users_file = env.get(DEV_USERS_FILE_ENV, "").strip()
        if mode is AuthMode.DEV and not users_file:
            raise SettingsError(f"{AUTH_ENV}=dev needs {DEV_USERS_FILE_ENV}")
        try:
            port = int(env.get(PORT_ENV, "8000"))
        except ValueError:
            raise SettingsError(f"{PORT_ENV} must be a port number") from None
        if not 1 <= port <= 65535:
            raise SettingsError(f"{PORT_ENV} must be between 1 and 65535")
        return cls(
            auth_mode=mode,
            dev_users_file=Path(users_file) if users_file else None,
            temporal_address=env.get(TEMPORAL_ADDRESS_ENV, "").strip() or "127.0.0.1:7233",
            temporal_namespace=env.get(TEMPORAL_NAMESPACE_ENV, "").strip() or "default",
            # Not 0.0.0.0: the API is reached through the UI's own origin, not from the network.
            host=env.get(HOST_ENV, "").strip() or "127.0.0.1",
            port=port,
        )

    def temporal_target(self) -> tuple[str, str]:
        """`(address, namespace)` for a Temporal client."""
        return self.temporal_address, self.temporal_namespace
