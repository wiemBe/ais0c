"""`alembic upgrade head` and `alembic downgrade base` on an empty database (criterion 1).

The tests run the real `alembic` command in packages/storage, so alembic.ini, env.py and the
AIS0C_DATABASE_URL lookup are covered too.
"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import URL, inspect, text
from storage_postgres import Server

from ais0c_storage.db import DATABASE_URL_ENV, create_sync_engine
from ais0c_storage.models import Base

STORAGE_DIR = Path(__file__).resolve().parents[1]


def alembic(*args: str, url: URL | None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != DATABASE_URL_ENV}
    if url is not None:
        env[DATABASE_URL_ENV] = url.render_as_string(hide_password=False)
    return subprocess.run(  # noqa: S603 - fixed arguments, test only
        [sys.executable, "-m", "alembic", *args],
        cwd=STORAGE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def public_objects(url: URL) -> dict[str, set[str]]:
    """Tables, sequences, functions, types and triggers in the public schema."""
    queries = {
        "relations": "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'S', 'v', 'm', 'p')",
        "functions": "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'public'",
        "types": "SELECT typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace"
        " WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd', 'c') AND NOT EXISTS"
        " (SELECT 1 FROM pg_class c WHERE c.oid = t.typrelid)",
        "triggers": "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal",
    }
    engine = create_sync_engine(url)
    try:
        with engine.connect() as connection:
            return {kind: set(connection.scalars(text(query))) for kind, query in queries.items()}
    finally:
        engine.dispose()


def test_upgrade_head_then_downgrade_base(server: Server, empty_database: str) -> None:
    url = server.app_url(empty_database)

    upgraded = alembic("upgrade", "head", url=url)
    assert upgraded.returncode == 0, upgraded.stderr
    current = alembic("current", url=url)
    assert "0001 (head)" in current.stdout
    objects = public_objects(url)
    assert set(Base.metadata.tables) <= objects["relations"]
    assert objects["functions"] == {"audit_log_append_only"}
    assert objects["triggers"] == {"audit_log_append_only"}

    downgraded = alembic("downgrade", "base", url=url)
    assert downgraded.returncode == 0, downgraded.stderr
    # Only Alembic's own, now empty, version table is left.
    assert public_objects(url) == {
        "relations": {"alembic_version"},
        "functions": set(),
        "types": set(),
        "triggers": set(),
    }
    engine = create_sync_engine(url)
    try:
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == ["alembic_version"]
            assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 0
    finally:
        engine.dispose()

    # The downgrade leaves nothing behind that would stop a new upgrade.
    again = alembic("upgrade", "head", url=url)
    assert again.returncode == 0, again.stderr


def test_downgrade_base_drops_tables_that_hold_data(server: Server, database: str) -> None:
    url = server.app_url(database)
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_log (actor_kind, actor_id, action, object_type, object_id,"
                    " details) VALUES ('system', 'test', 'test.run', 'test', '1', '{}')"
                )
            )
    finally:
        engine.dispose()

    downgraded = alembic("downgrade", "base", url=url)

    assert downgraded.returncode == 0, downgraded.stderr
    assert public_objects(url)["relations"] == {"alembic_version"}


def test_offline_mode_prints_the_sql(server: Server, empty_database: str) -> None:
    printed = alembic("upgrade", "head", "--sql", url=server.app_url(empty_database))

    assert printed.returncode == 0, printed.stderr
    assert "CREATE TABLE offenses_seen" in printed.stdout
    assert "CREATE TRIGGER audit_log_append_only" in printed.stdout


def test_without_a_database_url_nothing_connects() -> None:
    """There is no default connection: the command fails before touching any database."""
    result = alembic("upgrade", "head", url=None)

    assert result.returncode != 0
    assert f"{DATABASE_URL_ENV} is not set" in result.stderr
