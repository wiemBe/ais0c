"""Building the analyst API from the environment at start-up.

Like the worker (`ais0c_worker.main`) the service refuses to start rather than run with a weaker
setting: without `AIS0C_API_AUTH`, with a mode this build does not have, with no database URL, or
in `dev` mode with a users file that is missing or does not hold what a user needs. `dev` mode is
logged as a warning at start-up, so a dev stack is never mistaken for a deployment (T-63 (1)).
"""

import logging

from fastapi import FastAPI

from ais0c_api.app import build_app
from ais0c_api.auth import AuthError, DevAuthenticator
from ais0c_api.settings import AuthMode, Settings, SettingsError
from ais0c_api.temporal import TemporalScheduleTrigger
from ais0c_storage import (
    ConfigurationError,
    create_engine,
    create_session_factory,
    database_url,
)

logger = logging.getLogger("ais0c.api")


class ServiceError(RuntimeError):
    """The service cannot be built from the environment it was given."""


class Service:
    """The app and what it was built from."""

    def __init__(self, app: FastAPI, settings: Settings) -> None:
        self.app = app
        self.settings = settings


def build_service(settings: Settings | None = None) -> Service:
    """The service from `settings` (the environment by default).

    Raises `ServiceError` when a setting, a file or a secret it needs is missing or invalid.
    """
    try:
        resolved = Settings.from_env() if settings is None else settings
    except SettingsError as error:
        raise ServiceError(str(error)) from error
    if resolved.auth_mode is AuthMode.DEV:
        if resolved.dev_users_file is None:  # from_env already refuses this; kept for clarity
            raise ServiceError("dev mode needs a users file")
        try:
            authenticator = DevAuthenticator.from_file(resolved.dev_users_file)
        except AuthError as error:
            raise ServiceError(str(error)) from error
        logger.warning(
            "AIS0C_API_AUTH=dev: bearer tokens from %s are accepted. This mode is for "
            "development only; T-035 replaces it with OIDC.",
            resolved.dev_users_file,
        )
    else:  # pragma: no cover - AuthMode has one member today
        raise ServiceError(f"{resolved.auth_mode.value} is not implemented yet")
    try:
        url = database_url()
    except ConfigurationError as error:
        raise ServiceError(str(error)) from error
    sessions = create_session_factory(create_engine(url))
    address, namespace = resolved.temporal_target()
    app = build_app(
        sessions=sessions,
        authenticator=authenticator,
        schedule_trigger=TemporalScheduleTrigger(address, namespace),
    )
    return Service(app=app, settings=resolved)
