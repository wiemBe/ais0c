"""`alembic upgrade head` and `alembic downgrade base` on an empty database (T-004 criterion
1), revision 0002 on a database that holds runs (T-016 criterion 2), and revisions 0003
(T-017 criterion 1), 0004 (T-020), 0005 (T-021), 0006 (T-041 criterion 3), 0007 (T-036
criterion 1), 0008 (T-57) and 0009 (T-027) on one that holds data.

The tests run the real `alembic` command in packages/storage, so alembic.ini, env.py and the
AIS0C_DATABASE_URL lookup are covered too.
"""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import URL, inspect, text
from sqlalchemy.exc import IntegrityError
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
    assert "0009 (head)" in current.stdout
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


def test_revision_0005_gives_existing_rules_no_techniques(
    server: Server, empty_database: str
) -> None:
    """0005 adds `catalog_rules.attack_techniques`: a rule synced before it gets an empty
    array, and the downgrade drops the column and keeps the rule."""
    url = server.app_url(empty_database)
    first = alembic("upgrade", "0004", url=url)
    assert first.returncode == 0, first.stderr
    insert_rule = (
        "INSERT INTO catalog_rules (rule_id, rule_name, defined, has_automated_action,"
        " updated_by, updated_at) VALUES (100201, 'Rule', false, false, 'knowledge-sync', now())"
    )
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(insert_rule))

        upgraded = alembic("upgrade", "head", url=url)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.connect() as connection:
            techniques = connection.scalar(text("SELECT attack_techniques FROM catalog_rules"))
        assert techniques == []

        downgraded = alembic("downgrade", "0004", url=url)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            columns = {
                column["name"] for column in inspect(connection).get_columns("catalog_rules")
            }
            assert "attack_techniques" not in columns
            assert connection.scalar(text("SELECT count(*) FROM catalog_rules")) == 1
    finally:
        engine.dispose()


def test_offline_mode_prints_the_sql(server: Server, empty_database: str) -> None:
    printed = alembic("upgrade", "head", "--sql", url=server.app_url(empty_database))

    assert printed.returncode == 0, printed.stderr
    assert "CREATE TABLE offenses_seen" in printed.stdout
    assert "CREATE TRIGGER audit_log_append_only" in printed.stdout
    assert "ALTER TABLE agent_runs ADD COLUMN model_release JSONB" in printed.stdout
    assert "CREATE TABLE platform_flags" in printed.stdout
    assert "ALTER TABLE notifications ADD COLUMN error TEXT" in printed.stdout
    assert "UPDATE notes_written SET status='disabled'" in printed.stdout
    assert "CREATE TABLE notification_routes" in printed.stdout
    assert "NULLS NOT DISTINCT" in printed.stdout
    assert "ADD COLUMN evaluation_no INTEGER" in printed.stdout
    assert "ADD COLUMN error TEXT" in printed.stdout
    assert "CREATE TABLE offense_group_values" in printed.stdout


def test_revision_0008_backfills_qa_evaluation_and_keeps_data_on_downgrade(
    server: Server, empty_database: str
) -> None:
    url = server.app_url(empty_database)
    first = alembic("upgrade", "0007", url=url)
    assert first.returncode == 0, first.stderr
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO cases (case_id, source, offense_id, status, evaluation_no,"
                    " sla_due_at, workflow_id, run_id) VALUES ('case-8', 'offense', 8, 'decided',"
                    " 3, now(), 'case-8', 'case-run-8')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO qa_items (id, case_id, reason, status) VALUES"
                    " (gen_random_uuid(), 'case-8', 'low_confidence', 'open'),"
                    " (gen_random_uuid(), 'case-8', 'low_confidence', 'open')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO agent_runs (run_id, case_id, agent_id, agent_version,"
                    " prompt_version, model_alias, model_target, toolset_profile, task, tokens,"
                    " tool_calls, started_at) VALUES ('case-8-triage-3', 'case-8', 'triage',"
                    " '1', 'v1', 'soc-fast', 'lab-model', 'qradar-triage-read', '{}', 0, 0, now())"
                )
            )

        upgraded = alembic("upgrade", "head", url=url)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT evaluation_no FROM qa_items")) == 3
            assert connection.scalar(text("SELECT count(*) FROM qa_items")) == 1
            assert connection.scalar(text("SELECT error FROM agent_runs")) is None
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO qa_items (id, case_id, evaluation_no, reason, status) VALUES"
                        " (gen_random_uuid(), 'case-8', 3, 'low_confidence', 'open')"
                    )
                )

        downgraded = alembic("downgrade", "0007", url=url)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM qa_items")) == 1
            assert connection.scalar(text("SELECT count(*) FROM agent_runs")) == 1
            qa_columns = {column["name"] for column in inspect(connection).get_columns("qa_items")}
            run_columns = {
                column["name"] for column in inspect(connection).get_columns("agent_runs")
            }
        assert "evaluation_no" not in qa_columns
        assert "error" not in run_columns
    finally:
        engine.dispose()


def test_revision_0009_adds_group_values_and_keeps_offenses(
    server: Server, empty_database: str
) -> None:
    """0009 (T-027): recorded offenses get no full analysis reason, so they count against the
    group's hourly limit as before; the new table takes a group's values and goes on
    downgrade, while the offenses stay."""
    url = server.app_url(empty_database)
    first = alembic("upgrade", "0008", url=url)
    assert first.returncode == 0, first.stderr
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO offense_groups (group_id, rule_set_hash, window_start,"
                    " window_end, offense_count, status) VALUES ('G-1', 'abc', now(),"
                    " now() + interval '1 day', 1, 'storm')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO offenses_seen (offense_id, first_seen_at, last_updated_at,"
                    " description, rule_ids, catalog_mode, group_id, status, pre_priority)"
                    " VALUES (9, now(), now(), 'Synthetic', '{100201}', 'analyze', 'G-1',"
                    " 'done', 0)"
                )
            )

        upgraded = alembic("upgrade", "head", url=url)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT full_analysis_reason FROM offenses_seen")) is None
            connection.execute(
                text(
                    "INSERT INTO offense_group_values (group_id, kind, value, offense_id,"
                    " seen_at) VALUES ('G-1', 'source_ip', '203.0.113.7', 9, now())"
                )
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO offense_group_values (group_id, kind, value, offense_id,"
                        " seen_at) VALUES ('G-1', 'source_ip', '203.0.113.7', 9, now())"
                    )
                )

        downgraded = alembic("downgrade", "0008", url=url)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM offenses_seen")) == 1
            tables = set(inspect(connection).get_table_names())
            columns = {
                column["name"] for column in inspect(connection).get_columns("offenses_seen")
            }
        assert "offense_group_values" not in tables
        assert "full_analysis_reason" not in columns
    finally:
        engine.dispose()


# The recipient rows as revision 0006 holds them: the three groups T-020 knew about, plus one an
# admin added after the storage enum was replaced by a name (T-43).
RECIPIENTS_AT_0007 = sorted(
    [
        ("operators", "soc-1@example.com"),
        ("operators", "soc-2@example.com"),
        ("hunters", "hunter@example.com"),
        ("soc-on-call-2", "duty@example.com"),
    ]
)


def test_revision_0007_names_the_groups_and_routes_each_alert(
    server: Server, empty_database: str
) -> None:
    """0007 (T-43, D-41): the existing recipient rows keep their group and address, the name is
    checked from now on, and `notification_routes` arrives with the seeded routes. The downgrade
    drops the routing table and the name check and keeps every recipient row, including a custom
    group: 0006 stored the name as plain text too."""
    url = server.app_url(empty_database)
    first = alembic("upgrade", "0006", url=url)
    assert first.returncode == 0, first.stderr
    engine = create_sync_engine(url)
    routes = text(
        "SELECT kind, level, list_name FROM notification_routes"
        " ORDER BY kind, level NULLS FIRST, list_name"
    )
    recipients = text(
        "SELECT list_name, email FROM notification_recipients ORDER BY list_name, email"
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO notification_recipients (list_name, email) VALUES (:list_name,"
                    " :email)"
                ),
                [{"list_name": name, "email": email} for name, email in RECIPIENTS_AT_0007],
            )

        upgraded = alembic("upgrade", "head", url=url)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.connect() as connection:
            assert [tuple(row) for row in connection.execute(routes)] == [
                ("case_alert", "critical", "analyst-eng"),
                ("case_alert", "critical", "exec"),
                ("case_alert", "critical", "operators"),
                ("case_alert", "high", "operators"),
                ("group_alert", "critical", "analyst-eng"),
                ("group_alert", "critical", "exec"),
                ("group_alert", "critical", "operators"),
                ("group_alert", "high", "operators"),
                ("hunt_report", None, "hunters"),
            ]
            assert [tuple(row) for row in connection.execute(recipients)] == RECIPIENTS_AT_0007
        with engine.begin() as connection:
            # A new name outside the pattern is refused by the database, not only by the code.
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO notification_recipients (list_name, email) VALUES"
                        " ('SOC Ops', 'soc-3@example.com')"
                    )
                )
        with engine.begin() as connection:
            # The hunt report's empty level is one value: a second NULL row is a duplicate.
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO notification_routes (id, kind, level, list_name) VALUES"
                        " (gen_random_uuid(), 'hunt_report', NULL, 'hunters')"
                    )
                )

        downgraded = alembic("downgrade", "0006", url=url)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            assert "notification_routes" not in inspect(connection).get_table_names()
            assert [tuple(row) for row in connection.execute(recipients)] == RECIPIENTS_AT_0007
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_constraint WHERE conname ="
                        " 'ck_notification_recipients_list_name'"
                    )
                )
                == 0
            )

        # Up again: the routes come back and the custom group is still there.
        again = alembic("upgrade", "head", url=url)
        assert again.returncode == 0, again.stderr
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM notification_routes")) == 9
            assert [tuple(row) for row in connection.execute(recipients)] == RECIPIENTS_AT_0007
    finally:
        engine.dispose()


# Rows as revision 0005 holds them: a case, its notes as T-019 recorded them, two e-mails and
# a catalog entry of each kind.
CASE_AT_0005 = (
    "INSERT INTO cases (case_id, source, offense_id, status, evaluation_no, sla_due_at,"
    " workflow_id, run_id) VALUES ('case-12345', 'offense', 12345, 'running', 1, now(),"
    " 'case-12345', 'case-12345-triage-1')"
)
NOTE_AT_0005 = (
    "INSERT INTO notes_written (id, case_id, offense_id, evaluation_no, run_marker, status,"
    " error, written_at) VALUES (gen_random_uuid(), 'case-12345', 12345, 1, :marker, :status,"
    " :error, now())"
)
NOTES_AT_0005 = [
    # Held back by the kill switch (T-019's form), with and without a reason.
    {
        "marker": "aaaaaa",
        "status": "failed",
        "error": "writes_disabled: external writes are off: writes_enabled was never switched on"
        " (shadow mode)",
    },
    {"marker": "bbbbbb", "status": "failed", "error": "writes_disabled: the kill switch is off"},
    # Real failures and a written note keep what they have.
    {
        "marker": "cccccc",
        "status": "failed",
        "error": "get_offense_notes: error: upstream_unavailable",
    },
    {
        "marker": "dddddd",
        "status": "failed",
        "error": "writesXdisabled: the _ of the prefix is no wildcard",
    },
    {"marker": "eeeeee", "status": "written", "error": None},
]
EMAIL_AT_0005 = (
    "INSERT INTO notifications (id, kind, level, case_id, recipients, subject, idempotency_key,"
    " status, sent_at) VALUES (gen_random_uuid(), 'case_alert', 'high', 'case-12345',"
    " ARRAY['soc-operators@example.com'], 'subject', :key, :status, :sent_at)"
)
EMAILS_AT_0005 = [
    {"key": "case_alert:case-12345:1", "status": "failed", "sent_at": None},
    {
        "key": "case_alert:case-12345:2",
        "status": "sent",
        "sent_at": datetime(2026, 10, 5, tzinfo=UTC),
    },
]
CATALOG_AT_0005 = [
    "INSERT INTO catalog_rules (rule_id, rule_name, defined, has_automated_action, updated_by,"
    " updated_at) VALUES (100201, 'Rule', false, false, 'knowledge-sync', now())",
    "INSERT INTO catalog_log_sources (log_source_id, name, type_name, defined, in_scope,"
    " updated_by, updated_at) VALUES (2001, 'DC-01', 'Microsoft Windows Security Event Log',"
    " false, true, 'knowledge-sync', now())",
]


def test_revision_0006_records_held_back_writes_as_disabled_and_back(
    server: Server, empty_database: str
) -> None:
    """0006 (T-37): a note T-019 recorded as `failed` with a `writes_disabled:` error becomes
    `disabled` without the error; every other row keeps its status and error. The new catalog
    columns start empty, `qradar_enabled` true. The downgrade turns `disabled` notes back into
    T-019's form and `disabled` e-mails into `failed`, drops the columns and keeps every row."""
    url = server.app_url(empty_database)
    first = alembic("upgrade", "0005", url=url)
    assert first.returncode == 0, first.stderr
    notes = text("SELECT run_marker, status, error FROM notes_written ORDER BY run_marker")
    emails = text("SELECT idempotency_key, status FROM notifications ORDER BY idempotency_key")
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(CASE_AT_0005))
            connection.execute(text(NOTE_AT_0005), NOTES_AT_0005)
            connection.execute(text(EMAIL_AT_0005), EMAILS_AT_0005)
            for statement in CATALOG_AT_0005:
                connection.execute(text(statement))

        upgraded = alembic("upgrade", "head", url=url)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.begin() as connection:
            assert [tuple(row) for row in connection.execute(notes)] == [
                ("aaaaaa", "disabled", None),
                ("bbbbbb", "disabled", None),
                ("cccccc", "failed", "get_offense_notes: error: upstream_unavailable"),
                ("dddddd", "failed", "writesXdisabled: the _ of the prefix is no wildcard"),
                ("eeeeee", "written", None),
            ]
            assert [
                tuple(row)
                for row in connection.execute(
                    text("SELECT status, error FROM notifications ORDER BY idempotency_key")
                )
            ] == [("failed", None), ("sent", None)]
            assert tuple(
                connection.execute(
                    text("SELECT qradar_enabled, missing_since FROM catalog_rules")
                ).one()
            ) == (True, None)
            assert connection.scalar(text("SELECT missing_since FROM catalog_log_sources")) is None
            # What the new code writes: a held-back e-mail, a missing and disabled rule.
            connection.execute(
                text(
                    "INSERT INTO notifications (id, kind, level, case_id, recipients, subject,"
                    " idempotency_key, status) VALUES (gen_random_uuid(), 'case_alert',"
                    " 'critical', 'case-12345', ARRAY['soc-operators@example.com'], 'subject',"
                    " 'case_alert:case-12345:3', 'disabled')"
                )
            )
            connection.execute(
                text("UPDATE catalog_rules SET qradar_enabled = false, missing_since = now()")
            )
            connection.execute(text("UPDATE catalog_log_sources SET missing_since = now()"))

        downgraded = alembic("downgrade", "0005", url=url)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            assert [tuple(row) for row in connection.execute(notes)] == [
                ("aaaaaa", "failed", "writes_disabled: the kill switch is off"),
                ("bbbbbb", "failed", "writes_disabled: the kill switch is off"),
                ("cccccc", "failed", "get_offense_notes: error: upstream_unavailable"),
                ("dddddd", "failed", "writesXdisabled: the _ of the prefix is no wildcard"),
                ("eeeeee", "written", None),
            ]
            assert [tuple(row) for row in connection.execute(emails)] == [
                ("case_alert:case-12345:1", "failed"),
                ("case_alert:case-12345:2", "sent"),
                ("case_alert:case-12345:3", "failed"),
            ]
            inspector = inspect(connection)
            columns = {
                table: {column["name"] for column in inspector.get_columns(table)}
                for table in ("notifications", "catalog_rules", "catalog_log_sources")
            }
            assert "error" not in columns["notifications"]
            assert {"qradar_enabled", "missing_since"}.isdisjoint(columns["catalog_rules"])
            assert "missing_since" not in columns["catalog_log_sources"]
            assert connection.scalar(text("SELECT count(*) FROM catalog_rules")) == 1
            assert connection.scalar(text("SELECT count(*) FROM catalog_log_sources")) == 1

        # Up again: the held-back notes are `disabled` once more.
        again = alembic("upgrade", "head", url=url)
        assert again.returncode == 0, again.stderr
        with engine.connect() as connection:
            statuses = [tuple(row)[:2] for row in connection.execute(notes)]
        assert statuses == [
            ("aaaaaa", "disabled"),
            ("bbbbbb", "disabled"),
            ("cccccc", "failed"),
            ("dddddd", "failed"),
            ("eeeeee", "written"),
        ]
    finally:
        engine.dispose()


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


def test_revision_0004_adds_the_level_and_keeps_the_emails(
    server: Server, empty_database: str
) -> None:
    """0004 adds `notifications.level`; an e-mail recorded before it keeps an empty level, and
    the downgrade drops the column and keeps the e-mail."""
    url = server.app_url(empty_database)
    first = alembic("upgrade", "0003", url=url)
    assert first.returncode == 0, first.stderr
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO notifications (id, kind, case_id, recipients, subject,"
                    " idempotency_key, status) VALUES (gen_random_uuid(), 'case_alert',"
                    " 'case-12345', ARRAY['soc-operators@example.com'], 'subject',"
                    " 'case_alert:case-12345:1', 'failed')"
                )
            )

        upgraded = alembic("upgrade", "head", url=url)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM notifications")) == 1
            assert connection.scalar(text("SELECT level FROM notifications")) is None

        downgraded = alembic("downgrade", "0003", url=url)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            columns = {
                column["name"] for column in inspect(connection).get_columns("notifications")
            }
            assert "level" not in columns
            assert connection.scalar(text("SELECT count(*) FROM notifications")) == 1
    finally:
        engine.dispose()


def test_without_a_database_url_nothing_connects() -> None:
    """There is no default connection: the command fails before touching any database."""
    result = alembic("upgrade", "head", url=None)

    assert result.returncode != 0
    assert f"{DATABASE_URL_ENV} is not set" in result.stderr
