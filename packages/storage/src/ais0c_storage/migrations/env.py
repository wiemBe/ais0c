"""Alembic environment of the application database.

Runs on the connection in `config.attributes["connection"]` when one is given
(`ais0c_storage.migrate`), otherwise on AIS0C_DATABASE_URL. There is no default URL.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from ais0c_storage.db import create_sync_engine, database_url
from ais0c_storage.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """`alembic upgrade --sql`: print the SQL instead of running it."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_on(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if isinstance(connection, Connection):
        run_migrations_on(connection)
        return
    engine = create_sync_engine()
    try:
        with engine.connect() as connection:
            run_migrations_on(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
