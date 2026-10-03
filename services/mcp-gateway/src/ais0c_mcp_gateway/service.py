"""Building the gateway from its settings (settings.py) at startup.

Startup fails rather than run with a weaker check: an invalid registry, no enabled profile, a
weak or shared token, a missing MCP endpoint or token, or no database URL all stop it. Only
profiles with a token file are served.
"""

import logging
from dataclasses import dataclass

from fastapi import FastAPI

from ais0c_mcp_gateway.app import create_app
from ais0c_mcp_gateway.auth import ProfileAuthenticator
from ais0c_mcp_gateway.logs import Redactor, configure_logging
from ais0c_mcp_gateway.pipeline import Gateway
from ais0c_mcp_gateway.registry import Registry, load_registry
from ais0c_mcp_gateway.settings import Settings, SettingsError
from ais0c_mcp_gateway.upstream import McpUpstream, Upstream
from ais0c_storage import create_engine, create_session_factory, database_url

logger = logging.getLogger("ais0c.gateway")


@dataclass(frozen=True)
class Service:
    app: FastAPI
    gateway: Gateway
    redactor: Redactor


def build_service(settings: Settings, *, configure_logs: bool = True) -> Service:
    registry = load_registry(settings.config_dir, settings.connectors)
    tokens = {
        name: token
        for name in registry.profiles
        if (token := settings.profile_token(name)) is not None
    }
    authenticator = ProfileAuthenticator(tokens)
    enabled = Registry(
        profiles={name: registry.profiles[name] for name in sorted(authenticator.profiles)},
        connectors=registry.connectors,
    )

    upstreams: dict[str, Upstream] = {}
    upstream_secrets: list[str] = []
    for profile in enabled.profiles.values():
        instance = profile.instance
        if instance in upstreams:
            continue
        token = settings.upstream_token(instance)
        if token.get_secret_value() in authenticator.secrets():
            raise SettingsError(f"the MCP token of {instance} is also a profile token")
        upstream_secrets.append(token.get_secret_value())
        upstreams[instance] = McpUpstream(
            settings.upstream_url(instance),
            token,
            timeout_seconds=profile.connector.limits.call_timeout_seconds,
        )

    url = database_url()
    redactor = Redactor([*authenticator.secrets(), *upstream_secrets, url.password or ""])
    if configure_logs:
        configure_logging(redactor)
    sessions = create_session_factory(create_engine(url))
    gateway = Gateway(registry=enabled, upstreams=upstreams, sessions=sessions, redactor=redactor)
    logger.info("serving profiles: %s", ", ".join(enabled.profiles))
    return Service(app=create_app(gateway, authenticator), gateway=gateway, redactor=redactor)
