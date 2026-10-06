"""Fixtures of the analyst API tests: PostgreSQL, the app and the stand-in Temporal.

PostgreSQL is the storage tests' test server (packages/storage/tests/storage_postgres.py): the
schema is migrated once into a template database and every test gets its own copy. The app is the
real one, built around that database, with the synthetic dev users of `api_support.py` and a fake
Temporal, so no model, QRadar or Temporal server is ever called.

This directory is not a package (see packages/storage/tests/conftest.py). The test modules import
`api_support` and `storage_postgres` by name, so both directories go on sys.path.
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
sys.path[:0] = [str(HERE), str(HERE.parents[2] / "packages" / "storage" / "tests")]

from api_support import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    Harness,
    build_harness,
)
from storage_postgres import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    Server,
    start_server,
)

TEMPLATE_DATABASE = "ais0c_api_template"


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


@pytest.fixture
async def api(sessions: async_sessionmaker[AsyncSession], tmp_path: Path) -> AsyncIterator[Harness]:
    """The API under test, with the synthetic dev users and a fake Temporal."""
    from api_support import DevUsersFile  # sys.path is set above

    harness = build_harness(sessions, DevUsersFile.write(tmp_path))
    try:
        yield harness
    finally:
        await harness.client.aclose()
