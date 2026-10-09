"""Deployment database operations use the real migrations and repositories."""

import asyncio
import secrets
from collections.abc import Iterator

import pytest
from sqlalchemy import URL
from storage_postgres import Server  # pyright: ignore[reportMissingImports]

from ais0c_activities.deploy import database_revision, migrate_to_head, writes_enabled
from ais0c_storage import (
    ActorKind,
    PlatformFlag,
    create_engine,
    create_session_factory,
    create_sync_engine,
)
from ais0c_storage.migrate import upgrade
from ais0c_storage.repositories import set_platform_flag


@pytest.fixture
def empty_database(server: Server) -> Iterator[URL]:
    name = f"test_deploy_{secrets.token_hex(6)}"
    server.create_database(name)
    try:
        yield server.app_url(name)
    finally:
        server.drop_database(name)


def test_migrate_brings_an_empty_database_to_head(empty_database: URL) -> None:
    url = empty_database.render_as_string(hide_password=False)

    assert database_revision(url)[0] is None
    revision = migrate_to_head(url)

    assert database_revision(url) == (revision, revision)


def test_migrate_is_idempotent(empty_database: URL) -> None:
    url = empty_database.render_as_string(hide_password=False)

    first = migrate_to_head(url)
    second = migrate_to_head(url)

    assert second == first
    assert database_revision(url) == (first, first)


def test_database_revision_reports_behind(empty_database: URL) -> None:
    url = empty_database.render_as_string(hide_password=False)
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            upgrade(connection, "0001")
    finally:
        engine.dispose()

    current, head = database_revision(url)

    assert current == "0001"
    assert head != current


def test_writes_enabled_is_false_without_a_flag_row(database_url: URL) -> None:
    url = database_url.render_as_string(hide_password=False)

    assert writes_enabled(url) is False


def test_writes_enabled_is_true_when_the_flag_is_on(database_url: URL) -> None:
    url = database_url.render_as_string(hide_password=False)

    async def enable() -> None:
        engine = create_engine(url)
        try:
            sessions = create_session_factory(engine)
            async with sessions.begin() as session:
                await set_platform_flag(
                    session,
                    PlatformFlag.WRITES_ENABLED,
                    enabled=True,
                    reason="Canary is approved for the synthetic test.",
                    actor_kind=ActorKind.USER,
                    actor_id="test-admin",
                )
        finally:
            await engine.dispose()

    asyncio.run(enable())

    assert writes_enabled(url) is True
