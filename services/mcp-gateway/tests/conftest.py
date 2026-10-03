"""Fixtures of the gateway tests: PostgreSQL, the fake MCP server and the gateway.

PostgreSQL is the storage tests' test server (packages/storage/tests/storage_postgres.py): the
schema is migrated once into a template database and every test gets its own copy. The fake
MCP server (gateway_support.py) runs once per session in a thread; its state is reset before
every test.

This directory is not a package (see packages/storage/tests/conftest.py). The test modules
import `gateway_support` and `storage_postgres` by name, so both directories go on sys.path.
"""

import secrets
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_mcp_gateway.registry import Registry, load_registry
from ais0c_storage import create_engine, create_session_factory, create_sync_engine
from ais0c_storage.migrate import upgrade

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parents[2] / "packages" / "storage" / "tests")]

from gateway_support import (  # noqa: E402
    CONFIG_DIR,
    FakeQRadar,
    Harness,
    build_fake_mcp_app,
    build_harness,
    load_config,
    registry_from,
    serve,
    write_config,
)
from storage_postgres import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    Server,
    start_server,
)

TEMPLATE_DATABASE = "ais0c_gateway_template"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def postgres() -> Iterator[Server]:
    with start_server() as server:
        yield server


@pytest.fixture(scope="session")
def template_database(postgres: Server) -> str:
    postgres.create_database(TEMPLATE_DATABASE)
    engine = create_sync_engine(postgres.app_url(TEMPLATE_DATABASE))
    try:
        with engine.begin() as connection:
            upgrade(connection)
    finally:
        engine.dispose()
    return TEMPLATE_DATABASE


@pytest.fixture
def database_url(postgres: Server, template_database: str) -> Iterator[URL]:
    name = f"test_{secrets.token_hex(6)}"
    postgres.create_database(name, template=template_database)
    try:
        yield postgres.app_url(name)
    finally:
        postgres.drop_database(name)


@pytest.fixture
async def sessions(database_url: URL) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(database_url)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def registry() -> Registry:
    """The registry of the real config/connectors and config/policies files."""
    return load_registry(CONFIG_DIR, ["qradar"])


@pytest.fixture(scope="session")
def fake_server(registry: Registry) -> Iterator[tuple[FakeQRadar, str]]:
    state = FakeQRadar()
    tool_names = sorted({tool for p in registry.profiles.values() for tool in p.tools})
    with serve(build_fake_mcp_app(state, tool_names)) as url:
        yield state, url


@pytest.fixture
def fake(fake_server: tuple[FakeQRadar, str]) -> FakeQRadar:
    state, _ = fake_server
    state.reset()
    return state


@pytest.fixture
def harness(
    registry: Registry,
    sessions: async_sessionmaker[AsyncSession],
    fake: FakeQRadar,
    fake_server: tuple[FakeQRadar, str],
) -> Harness:
    return build_harness(
        registry=registry, sessions=sessions, fake=fake, upstream_url=fake_server[1]
    )


ConfigChange = Callable[[dict[str, Any], dict[str, Any]], None]


@pytest.fixture
def make_harness(
    tmp_path: Path,
    sessions: async_sessionmaker[AsyncSession],
    fake: FakeQRadar,
    fake_server: tuple[FakeQRadar, str],
) -> Callable[[ConfigChange], Harness]:
    """A harness whose configuration is the real one changed by `change(manifest, policy)`."""

    def make(change: ConfigChange) -> Harness:
        manifest, policy = load_config()
        change(manifest, policy)
        registry = registry_from(write_config(tmp_path, manifest, policy))
        return build_harness(
            registry=registry, sessions=sessions, fake=fake, upstream_url=fake_server[1]
        )

    return make
