"""PostgreSQL + pgvector for the storage tests, started with testcontainers.

One container per test session. As in deploy/compose, tests connect as a role that owns its
database but is not a superuser. The schema is migrated once into a template database and
every test gets its own copy, so tests commit freely and never see each other's rows.

This directory is not a package: with `--import-mode=importlib` it would clash with the
`tests` package of another workspace member. The test modules import the helper modules here
(`storage_postgres`, `storage_payloads`) by name, so the directory goes on sys.path.
"""

import secrets
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ais0c_storage.db import create_engine, create_session_factory, create_sync_engine
from ais0c_storage.migrate import upgrade

sys.path.insert(0, str(Path(__file__).parent))

from storage_postgres import Server, start_server

TEMPLATE_DATABASE = "ais0c_template"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def server() -> Iterator[Server]:
    with start_server() as server:
        yield server


@pytest.fixture(scope="session")
def template_database(server: Server) -> str:
    """A database migrated to head by the application role."""
    server.create_database(TEMPLATE_DATABASE)
    engine = create_sync_engine(server.app_url(TEMPLATE_DATABASE))
    try:
        with engine.begin() as connection:
            upgrade(connection)
    finally:
        engine.dispose()
    return TEMPLATE_DATABASE


def _new_database_name() -> str:
    return f"test_{secrets.token_hex(6)}"


@pytest.fixture
def empty_database(server: Server) -> Iterator[str]:
    """A new database without any table."""
    name = _new_database_name()
    server.create_database(name)
    try:
        yield name
    finally:
        server.drop_database(name)


@pytest.fixture
def database(server: Server, template_database: str) -> Iterator[str]:
    """A new database at the head revision."""
    name = _new_database_name()
    server.create_database(name, template=template_database)
    try:
        yield name
    finally:
        server.drop_database(name)


@pytest.fixture
def database_url(server: Server, database: str) -> URL:
    return server.app_url(database)


@pytest.fixture
async def engine(database_url: URL) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with create_session_factory(engine)() as session:
        yield session
