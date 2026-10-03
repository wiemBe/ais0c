"""Database settings, engines and sessions.

The connection, driver included, comes only from the `AIS0C_DATABASE_URL` environment variable:
a SQLAlchemy URL such as `postgresql+psycopg://<user>:<password>@<host>:5432/ais0c`. There is no
default; without the variable nothing connects.

Sessions never commit on their own. Repository functions take an `AsyncSession` and the caller
owns the transaction:

    sessions = create_session_factory(create_engine())
    async with sessions.begin() as session:
        await add_offense_seen(session, ...)
"""

import os
from collections.abc import Mapping

from sqlalchemy import URL, Engine, NullPool, make_url
from sqlalchemy import create_engine as create_sync_engine_
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ais0c_storage.errors import ConfigurationError

DATABASE_URL_ENV = "AIS0C_DATABASE_URL"

# Sessions run in UTC, so timestamps never take the server's local time zone.
_CONNECT_ARGS = {"options": "-c TimeZone=UTC"}


def database_url(environ: Mapping[str, str] | None = None) -> URL:
    """The database URL from `AIS0C_DATABASE_URL` (`environ` defaults to `os.environ`)."""
    env = os.environ if environ is None else environ
    raw = env.get(DATABASE_URL_ENV, "").strip()
    if not raw:
        raise ConfigurationError(f"{DATABASE_URL_ENV} is not set")
    try:
        url = make_url(raw)
    except ArgumentError:
        # The parser's message repeats the URL, password included.
        raise ConfigurationError(f"{DATABASE_URL_ENV} is not a valid database URL") from None
    if url.get_backend_name() != "postgresql":
        raise ConfigurationError(f"{DATABASE_URL_ENV} must point to PostgreSQL")
    return url


def create_engine(url: URL | str | None = None, **options: object) -> AsyncEngine:
    """Async engine for the application database; `url` defaults to `database_url()`.

    `options` go to `create_async_engine` (pool size and the like). Statement parameters are
    left out of error messages so log data does not leak into logs.
    """
    resolved = database_url() if url is None else make_url(url)
    options.setdefault("pool_pre_ping", True)
    options.setdefault("hide_parameters", True)
    return create_async_engine(resolved, connect_args=_CONNECT_ARGS, **options)


def create_sync_engine(url: URL | str | None = None) -> Engine:
    """Synchronous engine without a pool, for migrations."""
    resolved = database_url() if url is None else make_url(url)
    return create_sync_engine_(
        resolved, connect_args=_CONNECT_ARGS, poolclass=NullPool, hide_parameters=True
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory; objects stay readable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
