"""Fixtures of the knowledge tests: PostgreSQL + pgvector for the catalog sync's tests.

The server is the storage tests' (packages/storage/tests/storage_postgres.py): the same pinned
image, and a non-superuser application role that owns its database. It starts on the first
test that asks for a database; the schema is migrated once into a template database and every
such test gets its own copy.

This directory is not a package (see packages/storage/tests/conftest.py). The skill tests import
their helpers relatively; `storage_postgres` is imported by name, so the storage tests'
directory goes on sys.path.
"""

import secrets
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_storage import create_engine, create_session_factory, create_sync_engine
from ais0c_storage.migrate import upgrade

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parents[1] / "storage" / "tests")]

from storage_postgres import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    Server,
    start_server,
)

TEMPLATE_DATABASE = "ais0c_knowledge_template"


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
async def sessions(database_url: URL) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(database_url)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
