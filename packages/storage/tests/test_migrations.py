"""`alembic upgrade head` and `alembic downgrade base` on an empty database (T-004 criterion
1), revision 0002 on a database that holds runs (T-016 criterion 2) and revision 0003 on
one that holds data (T-017 criterion 1).

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
    assert "0003 (head)" in current.stdout
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
    assert "ALTER TABLE agent_runs ADD COLUMN model_release JSONB" in printed.stdout
    assert "CREATE TABLE platform_flags" in printed.stdout


def test_revision_0002_adds_skill_and_model_release_to_recorded_runs(
    server: Server, empty_database: str
) -> None:
    """A run recorded before 0002 keeps its row and gets NULL in both new columns; the
    downgrade drops the columns and keeps the row."""
    url = server.app_url(empty_database)
    first = alembic("upgrade", "0001", url=url)
    assert first.returncode == 0, first.stderr
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO agent_runs (run_id, agent_id, agent_version, prompt_version,"
                    " model_alias, model_target, toolset_profile, task, tokens, tool_calls,"
                    " started_at) VALUES ('run-1', 'triage', '1', 'v1', 'soc-fast', 'lab-model',"
                    " 'qradar-triage-read', '{}', 0, 0, now())"
                )
            )

        upgraded = alembic("upgrade", "head", url=url)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.connect() as connection:
            row = connection.execute(text("SELECT skill, model_release FROM agent_runs")).one()
        assert tuple(row) == (None, None)

        downgraded = alembic("downgrade", "0001", url=url)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            columns = {column["name"] for column in inspect(connection).get_columns("agent_runs")}
            runs = connection.scalar(text("SELECT count(*) FROM agent_runs"))
        assert {"skill", "model_release"}.isdisjoint(columns)
        assert runs == 1
    finally:
        engine.dispose()


def test_revision_0003_leaves_writes_off(server: Server, empty_database: str) -> None:
    """0003 creates `platform_flags` without a row, so `writes_enabled` is off: an upgraded
    platform starts in shadow mode. The downgrade drops the table and keeps the audit log."""
    url = server.app_url(empty_database)
    first = alembic("upgrade", "0002", url=url)
    assert first.returncode == 0, first.stderr
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_log (actor_kind, actor_id, action, object_type, object_id,"
                    " details) VALUES ('system', 'test', 'test.run', 'test', '1', '{}')"
                )
            )

        upgraded = alembic("upgrade", "head", url=url)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM platform_flags")) == 0

        downgraded = alembic("downgrade", "0002", url=url)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            assert "platform_flags" not in inspect(connection).get_table_names()
            assert connection.scalar(text("SELECT count(*) FROM audit_log")) == 1
    finally:
        engine.dispose()


def test_without_a_database_url_nothing_connects() -> None:
    """There is no default connection: the command fails before touching any database."""
    result = alembic("upgrade", "head", url=None)

    assert result.returncode != 0
    assert f"{DATABASE_URL_ENV} is not set" in result.stderr
