"""Connection settings come only from the environment; no default is written in code."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ais0c_storage.db import (
    DATABASE_URL_ENV,
    create_engine,
    create_session_factory,
    create_sync_engine,
    database_url,
)
from ais0c_storage.errors import ConfigurationError

PASSWORD = "not-a-real-password"  # noqa: S105 - synthetic value, never a real secret
# Nothing listens there.
URL = f"postgresql+psycopg://ais0c:{PASSWORD}@db.example.com:5432/ais0c"


def test_url_and_driver_come_from_the_environment() -> None:
    url = database_url({DATABASE_URL_ENV: URL})

    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == (
        "db.example.com",
        5432,
        "ais0c",
        "ais0c",
    )


@pytest.mark.parametrize("environ", [{}, {DATABASE_URL_ENV: ""}, {DATABASE_URL_ENV: "  "}])
def test_there_is_no_default_url(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError, match=f"{DATABASE_URL_ENV} is not set"):
        database_url(environ)


def test_os_environ_is_the_default_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    with pytest.raises(ConfigurationError):
        database_url()
    with pytest.raises(ConfigurationError):
        create_engine()

    monkeypatch.setenv(DATABASE_URL_ENV, URL)
    assert database_url().host == "db.example.com"


def test_an_invalid_url_does_not_leak_into_the_error() -> None:
    with pytest.raises(ConfigurationError) as raised:
        database_url({DATABASE_URL_ENV: f"no url at all {PASSWORD}"})

    assert PASSWORD not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_only_postgresql_is_accepted() -> None:
    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        database_url({DATABASE_URL_ENV: "sqlite:///ais0c.db"})


@pytest.mark.anyio
async def test_engines_hide_parameters_and_passwords() -> None:
    engine: AsyncEngine = create_engine(URL)
    try:
        assert engine.sync_engine.hide_parameters
        assert PASSWORD not in repr(engine)
        assert engine.url.drivername == "postgresql+psycopg"
        assert create_session_factory(engine).kw["expire_on_commit"] is False
    finally:
        await engine.dispose()
    sync_engine = create_sync_engine(URL)
    try:
        assert sync_engine.hide_parameters
    finally:
        sync_engine.dispose()
