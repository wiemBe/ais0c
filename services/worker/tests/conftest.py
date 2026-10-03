"""Fixtures of the case worker tests: PostgreSQL, the Temporal test server and the platform.

PostgreSQL is the storage tests' test server (packages/storage/tests/storage_postgres.py): the
schema is migrated once into a template database and every test gets its own copy. Each test
also gets its own time-skipping Temporal test server.

This directory is not a package (see packages/storage/tests/conftest.py). The test modules
import `worker_support` and `storage_postgres` by name, so both directories go on sys.path.
"""

import secrets
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import URL
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from ais0c_activities import SessionFactory
from ais0c_storage import create_engine, create_session_factory, create_sync_engine
from ais0c_storage.migrate import upgrade

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parents[2] / "packages" / "storage" / "tests")]

from storage_postgres import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    Server,
    start_server,
)

TEMPLATE_DATABASE = "ais0c_worker_template"


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
async def sessions(database_url: URL) -> AsyncIterator[SessionFactory]:
    engine = create_engine(database_url)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        yield env
