"""Criterion 13: what the API must not do (D-02, D-19, AGENTS.md hard rules).

The API sends no request to QRadar, Falcon, the gateway or a model; it does not import the
executor, the activities or the workflows; and it has no endpoint that closes an offense, changes a
rule in QRadar or runs an action. Some of this import-linter already enforces; these tests hold
what it cannot see.
"""

import ast
from pathlib import Path

import pytest
from api_support import Harness, add_catalog, decided_case
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import async_sessionmaker

from ais0c_api.app import build_app
from ais0c_api.auth import DevAuthenticator, DevUser, Role
from ais0c_api.temporal import TemporalUnavailable

pytestmark = pytest.mark.anyio

SOURCE = Path(__file__).resolve().parents[1] / "src" / "ais0c_api"

# What the API may import of the platform, per docs/impl/repo-structure.md.
ALLOWED_PACKAGES = {"ais0c_contracts", "ais0c_storage"}
# What the API may never reach: the gateway and the MCP SDK, any provider's client, the executor,
# the activities and the workflows.
FORBIDDEN_MODULES = {
    "ais0c_agents",
    "ais0c_activities",
    "ais0c_workflows",
    "ais0c_executor",
    "ais0c_policy",
    "ais0c_querylang",
    "ais0c_knowledge",
    "ais0c_mcp_gateway",
    "ais0c_harness",
    "mcp",
    "fastmcp",
    "qradar_mcp",
    "falcon_mcp",
    "falconpy",
    "pydantic_ai",
    "openai",
}
# The HTTP clients a request to a security product or a model would need.
FORBIDDEN_HTTP_CLIENTS = {"httpx", "httpx2", "requests", "aiohttp", "urllib3"}


def imported_roots(path: Path) -> set[str]:
    """The top-level module names every file under `path` imports."""
    roots: set[str] = set()
    for module in sorted(path.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_the_api_imports_only_contracts_and_storage() -> None:
    """The import table of docs/impl/repo-structure.md, checked from the source as well."""
    roots = imported_roots(SOURCE)
    ais0c = {name for name in roots if name.startswith("ais0c_") and name != "ais0c_api"}

    assert ais0c == ALLOWED_PACKAGES
    assert not (FORBIDDEN_MODULES & roots)
    # No client that could reach a security product or a model.
    assert not (FORBIDDEN_HTTP_CLIENTS & roots)


def test_no_module_of_the_api_names_a_security_product_or_a_model() -> None:
    """Hard rule 3: no provider name in code, and no QRadar or Falcon client."""
    for module in sorted(SOURCE.rglob("*.py")):
        text = module.read_text(encoding="utf-8")
        for word in ("qradar_mcp", "falconpy", "add_offense_note", "get_offense_notes"):
            assert word not in text, f"{module.name} names {word}"


def test_the_app_has_no_endpoint_that_acts_on_a_security_product() -> None:
    """D-02, D-19: nothing closes an offense, changes a rule or runs an action."""
    app = build_app(
        sessions=async_sessionmaker(),  # no engine: no route is called here
        authenticator=_authenticator(),
        schedule_trigger=_NoTemporal(),
    )
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}

    for path in paths:
        for word in ("offense", "action", "tuning", "hunt", "actor", "isolate", "contain", "block"):
            assert word not in path, f"{path} looks like an action endpoint"


async def test_every_read_endpoint_answers_from_the_platform_tables(api: Harness) -> None:
    """Each read endpoint answers 200 with no network client imported (see the test above)."""
    await decided_case(api.sessions)
    await add_catalog(api.sessions)

    for path in (
        "/cases",
        "/qa",
        "/groups",
        "/catalog/rules",
        "/catalog/log-sources",
        "/critical-assets",
        "/admin/platform-flags",
        "/metrics/sla",
    ):
        response = await api.get(path)
        assert response.status_code == 200, path


def _authenticator() -> DevAuthenticator:
    return DevAuthenticator(
        [
            DevUser(
                token_sha256="0" * 64,
                subject="s",
                display_name="S",
                roles=frozenset({Role.OPERATOR}),
            )
        ]
    )


class _NoTemporal:
    """A trigger that always fails: building the app must not need a Temporal."""

    async def trigger(self, schedule_id: str) -> None:
        raise TemporalUnavailable("no Temporal here")
