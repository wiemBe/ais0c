"""Fixtures of the executor tests: a PostgreSQL + pgvector test server and a database per test.

The server is the one the storage tests use (packages/storage/tests/storage_postgres.py): the
same pinned image, and a non-superuser application role that owns its database. The schema is
migrated once into a template database; every test gets its own copy.

This directory is not a package (see packages/storage/tests/conftest.py). The test modules
import `note_payloads` and `storage_postgres` by name, so both directories go on sys.path.
"""

import secrets
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ais0c_storage import create_engine, create_session_factory, create_sync_engine
from ais0c_storage.migrate import upgrade

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "storage" / "tests")]

from storage_postgres import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    Server,
    start_server,
)

TEMPLATE_DATABASE = "ais0c_executor_template"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def server() -> Iterator[Server]:
    with start_server() as server:
        yield server


@pytest.fixture(scope="session")
def template_database(server: Server) -> str:
    server.create_database(TEMPLATE_DATABASE)
    engine = create_sync_engine(server.app_url(TEMPLATE_DATABASE))
    try:
        with engine.begin() as connection:
            upgrade(connection)
    finally:
        engine.dispose()
    return TEMPLATE_DATABASE


@pytest.fixture
def database_url(server: Server, template_database: str) -> Iterator[URL]:
    name = f"test_{secrets.token_hex(6)}"
    server.create_database(name, template=template_database)
    try:
        yield server.app_url(name)
    finally:
        server.drop_database(name)


@pytest.fixture
async def engine(database_url: URL) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)
