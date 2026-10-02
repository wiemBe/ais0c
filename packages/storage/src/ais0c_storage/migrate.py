"""Run the Alembic migrations from code.

The `alembic` command uses packages/storage/alembic.ini and AIS0C_DATABASE_URL. These helpers
run the same revisions on a connection the caller opens, inside the caller's transaction:

    with create_sync_engine().begin() as connection:
        upgrade(connection)
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def alembic_config(connection: Connection | None = None) -> Config:
    """Alembic configuration for the migrations of this package, optionally bound to `connection`."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("path_separator", "os")
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def upgrade(connection: Connection, revision: str = "head") -> None:
    command.upgrade(alembic_config(connection), revision)


def downgrade(connection: Connection, revision: str = "base") -> None:
    command.downgrade(alembic_config(connection), revision)
