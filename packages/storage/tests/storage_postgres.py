"""The PostgreSQL + pgvector test server (testcontainers)."""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
from psycopg import sql
from sqlalchemy import URL
from testcontainers.community.postgres import PostgresContainer

# The image of deploy/compose/docker-compose.dev.yaml, pinned by digest.
POSTGRES_IMAGE = (
    "docker.io/pgvector/pgvector:0.8.7-pg18-trixie"
    "@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0"
)
# As in deploy/compose: owns its database, is not a superuser.
APP_ROLE = "ais0c_app"


@dataclass(frozen=True)
class Server:
    """A running test container. Passwords are random and live only as long as it does."""

    host: str
    port: int
    admin_user: str
    admin_password: str
    app_password: str

    def admin_url(self, database: str) -> URL:
        """Superuser URL."""
        return self._url(self.admin_user, self.admin_password, database)

    def app_url(self, database: str) -> URL:
        """URL of the application role, the owner of `database`."""
        return self._url(APP_ROLE, self.app_password, database)

    def _url(self, user: str, password: str, database: str) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host=self.host,
            port=self.port,
            database=database,
        )

    def admin_connection(self, database: str = "postgres") -> psycopg.Connection:
        return psycopg.connect(
            host=self.host,
            port=self.port,
            user=self.admin_user,
            password=self.admin_password,
            dbname=database,
            autocommit=True,
        )

    def create_database(self, name: str, template: str | None = None) -> None:
        """A database owned by the application role, optionally copied from `template`."""
        statement = sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(name), sql.Identifier(APP_ROLE)
        )
        if template is not None:
            statement += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
        with self.admin_connection() as connection:
            connection.execute(statement)

    def drop_database(self, name: str) -> None:
        with self.admin_connection() as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@contextmanager
def start_server() -> Iterator[Server]:
    """Start a container and create the application role in it."""
    container = PostgresContainer(
        POSTGRES_IMAGE, password=secrets.token_hex(16), dbname="postgres", driver="psycopg"
    )
    with container:
        server = Server(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            admin_user=container.username,
            admin_password=container.password,
            app_password=secrets.token_hex(16),
        )
        with server.admin_connection() as connection:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(APP_ROLE), sql.Literal(server.app_password)
                )
            )
        yield server
