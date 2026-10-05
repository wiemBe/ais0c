"""The migrated schema against docs/impl/data-model.md (acceptance criteria 2, 7 and 8).

`DOC` is transcribed from the tables of data-model.md: `name type`, with `?` for nullable.
Choices the document leaves open are marked "Not in data-model.md" here and in
`ais0c_storage.models`.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import URL, inspect
from sqlalchemy.dialects import postgresql
from storage_postgres import Server

from ais0c_storage.db import create_sync_engine
from ais0c_storage.models import Base

# Type in data-model.md -> the type as SQLAlchemy reflects it from PostgreSQL.
PG_TYPES = {
    "text": "TEXT",
    "bigint": "BIGINT",
    "bigserial": "BIGINT",
    "int": "INTEGER",
    "bool": "BOOLEAN",
    "uuid": "UUID",
    "timestamptz": "TIMESTAMP WITH TIME ZONE",
    "jsonb": "JSONB",
    "double precision": "DOUBLE PRECISION",
    "bigint[]": "BIGINT[]",
    "text[]": "TEXT[]",
}


@dataclass(frozen=True)
class Table:
    # name -> (type in data-model.md, nullable)
    columns: dict[str, tuple[str, bool]]
    primary_key: tuple[str, ...]
    unique: set[tuple[str, ...]] = field(default_factory=set[tuple[str, ...]])
    # (column, referenced "table.column")
    foreign_keys: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])


def table(
    *columns: str,
    pk: tuple[str, ...],
    unique: tuple[tuple[str, ...], ...] = (),
    fk: tuple[tuple[str, str], ...] = (),
) -> Table:
    parsed: dict[str, tuple[str, bool]] = {}
    for spec in columns:
        name, doc_type = spec.split(" ", 1)
        parsed[name] = (doc_type.removesuffix("?"), doc_type.endswith("?"))
    return Table(parsed, pk, set(unique), set(fk))


CASES_FK = (("case_id", "cases.case_id"),)
HUNTS_FK = (("hunt_id", "hunts.hunt_id"),)

DOC: dict[str, Table] = {
    # --- Offense ve vaka
    "offenses_seen": table(
        "offense_id bigint",
        "first_seen_at timestamptz",
        "last_updated_at timestamptz",
        "description text",
        "rule_ids bigint[]",
        "catalog_mode text",
        "group_id text?",
        "case_id text?",
        "status text",
        "pre_priority int",
        pk=("offense_id",),
    ),
    "offense_groups": table(
        "group_id text",
        "rule_set_hash text",
        "window_start timestamptz",
        "window_end timestamptz",
        "offense_count int",
        "status text",
        "case_id text?",
        pk=("group_id",),
    ),
    "cases": table(
        "case_id text",
        "source text",
        "offense_id bigint?",
        "hunt_id text?",
        "group_id text?",
        "status text",
        "verdict text?",
        "confidence text?",
        "ai_level text?",
        "floor_level text?",
        "notify_level text?",
        "report jsonb?",
        "evaluation_no int",
        "sla_due_at timestamptz",
        # Not in data-model.md: nullable (a running case has no decision yet).
        "decided_at timestamptz?",
        "workflow_id text",
        "run_id text",
        "created_at timestamptz",
        pk=("case_id",),
    ),
    "qa_items": table(
        "id uuid",
        "case_id text",
        "reason text",
        "status text",
        "resolved_by text?",
        "resolved_at timestamptz?",
        pk=("id",),
        fk=CASES_FK,
    ),
    "operator_feedback": table(
        "id uuid",
        "case_id text",
        "user_subject text",
        "verdict text",
        "reason text",
        "comment text?",
        "created_at timestamptz",
        pk=("id",),
        fk=CASES_FK,
    ),
    # --- Ajan çalışmaları ve kanıt
    "agent_runs": table(
        "run_id text",
        "case_id text?",
        "hunt_id text?",
        "agent_id text",
        "agent_version text",
        "prompt_version text",
        "model_alias text",
        "model_target text",
        "toolset_profile text",
        # Not in data-model.md: nullable while the run is in progress.
        "status text?",
        "task jsonb",
        "result jsonb?",
        "tokens int",
        "tool_calls int",
        "skill jsonb?",
        "model_release jsonb?",
        "started_at timestamptz",
        # Not in data-model.md: nullable while the run is in progress.
        "ended_at timestamptz?",
        pk=("run_id",),
    ),
    "tool_calls": table(
        "id uuid",
        "run_id text",
        "intent jsonb",
        "policy_decision text",
        "deny_reason text?",
        "status text",
        "evidence_id text?",
        "latency_ms int",
        "created_at timestamptz",
        pk=("id",),
        fk=(("run_id", "agent_runs.run_id"),),
    ),
    "evidence": table(
        "evidence_id text",
        "source text",
        "query_text text",
        "query_hash text",
        "time_start timestamptz",
        "time_end timestamptz",
        "identifiers jsonb",
        "excerpt text",
        "retrieved_at timestamptz",
        "expires_at timestamptz",
        pk=("evidence_id",),
    ),
    "urgent_events": table(
        "id uuid",
        "case_id text",
        "evaluation_no int",
        "rank int",
        "event jsonb",
        pk=("id",),
        fk=CASES_FK,
    ),
    "recommendations": table(
        "id uuid",
        "case_id text",
        "evaluation_no int",
        "recommendation jsonb",
        pk=("id",),
        fk=CASES_FK,
    ),
    # --- Yazma ve gönderim kayıtları
    "notes_written": table(
        "id uuid",
        "case_id text",
        "offense_id bigint",
        "evaluation_no int",
        "run_marker text",
        "status text",
        "error text?",
        "written_at timestamptz",
        pk=("id",),
        unique=(("offense_id", "run_marker"),),
        fk=CASES_FK,
    ),
    "notifications": table(
        "id uuid",
        "kind text",
        "level text?",
        "case_id text?",
        "hunt_id text?",
        "group_id text?",
        "recipients text[]",
        "subject text",
        "idempotency_key text",
        "status text",
        "error text?",
        "sent_at timestamptz?",
        pk=("id",),
        unique=(("idempotency_key",),),
    ),
    # --- Analiz Kataloğu ve kurum bağlamı
    "catalog_rules": table(
        "rule_id bigint",
        "rule_name text",
        "defined bool",
        "mode text",
        "min_level text?",
        "has_automated_action bool",
        "context_note text?",
        "ai_draft_note text?",
        "attack_techniques text[]",
        "qradar_enabled bool",
        "missing_since timestamptz?",
        "updated_by text",
        "updated_at timestamptz",
        pk=("rule_id",),
    ),
    "catalog_log_sources": table(
        "log_source_id bigint",
        "name text",
        "type_name text",
        "defined bool",
        "description text?",
        "owner text?",
        "criticality text?",
        "in_scope bool",
        "context_note text?",
        "missing_since timestamptz?",
        "updated_by text",
        "updated_at timestamptz",
        pk=("log_source_id",),
    ),
    "critical_assets": table(
        "id uuid",
        "kind text",
        "value text",
        "label text",
        "level text",
        pk=("id",),
    ),
    # Not in data-model.md: the primary key.
    "notification_recipients": table("list_name text", "email text", pk=("list_name", "email")),
    "allowed_email_domains": table("domain text", pk=("domain",)),
    # --- Hunt
    "hunt_packs": table(
        "pack_id text",
        "version text",
        "status text",
        "actor_id text?",
        "content jsonb",
        "approved_by text?",
        "approved_at timestamptz?",
        pk=("pack_id", "version"),
    ),
    "hunts": table(
        "hunt_id text",
        "request jsonb",
        "status text",
        "outcome text?",
        "report jsonb?",
        "report_pdf_path text?",
        "schedule_id uuid?",
        # Not in data-model.md: the three types, and completed_at being nullable.
        "created_by text",
        "created_at timestamptz",
        "completed_at timestamptz?",
        pk=("hunt_id",),
    ),
    "hunt_slices": table(
        "hunt_id text",
        "hypothesis_id text",
        "log_source_type text",
        "slice_start timestamptz",
        "slice_end timestamptz",
        "status text",
        "query_hash text?",
        "evidence_id text?",
        pk=("hunt_id", "hypothesis_id", "log_source_type", "slice_start"),
        fk=HUNTS_FK,
    ),
    "analytic_results": table(
        "id uuid",
        "hunt_id text",
        "slice_start timestamptz",
        "analytic text",
        "entity text?",
        "field text",
        "value text",
        "count bigint",
        "score double precision?",
        "evidence_id text?",
        pk=("id",),
        fk=HUNTS_FK,
    ),
    "hunt_findings": table(
        "id uuid",
        "hunt_id text",
        "hypothesis_id text",
        "outcome text",
        "claims jsonb",
        "case_id text?",
        pk=("id",),
        fk=HUNTS_FK,
    ),
    "hunt_schedules": table(
        "id uuid",
        "pack_id text",
        "cron text",
        "window_days int",
        "scope jsonb",
        "enabled bool",
        pk=("id",),
    ),
    # --- Bilgi düzlemi. data-model.md lists only the main columns; not in data-model.md: the
    # types it does not give and the primary keys of actor_aliases and actor_techniques.
    # knowledge_chunks is out of scope (T-004).
    "actors": table(
        "actor_id text",
        "name text",
        "sector_relevance text",
        "priority int",
        "sources jsonb",
        pk=("actor_id",),
    ),
    "actor_aliases": table(
        "actor_id text", "alias text", "source text", pk=("actor_id", "alias", "source")
    ),
    "techniques": table("technique_id text", "name text", "tactics text[]", pk=("technique_id",)),
    "actor_techniques": table(
        "actor_id text",
        "technique_id text",
        "source text",
        "first_reported timestamptz",
        pk=("actor_id", "technique_id", "source"),
    ),
    "iocs": table(
        "id uuid",
        "type text",
        "value text",
        "first_seen timestamptz",
        "last_seen timestamptz",
        "confidence text",
        "tlp text",
        "source text",
        "actor_id text?",
        pk=("id",),
    ),
    "cti_reports": table(
        "report_id text",
        "source text",
        "title text",
        "published_at timestamptz",
        "url text?",
        pk=("report_id",),
    ),
    # --- Tuning
    "fp_clusters": table(
        "cluster_id text",
        "rule_id bigint",
        "pattern jsonb",
        "case_ids text[]",
        "size int",
        "created_at timestamptz",
        pk=("cluster_id",),
    ),
    "tuning_proposals": table(
        "id uuid",
        "cluster_id text",
        "proposal jsonb",
        "status text",
        # Not in data-model.md: nullable (an open proposal has no decision yet).
        "decided_by text?",
        "decided_at timestamptz?",
        "comment text?",
        pk=("id",),
        fk=(("cluster_id", "fp_clusters.cluster_id"),),
    ),
    # --- Platform bayrakları ve onaylar. change_approvals comes with a later task (D-36).
    "platform_flags": table(
        "name text",
        "enabled bool",
        "reason text?",
        "changed_by text",
        "changed_at timestamptz",
        pk=("name",),
    ),
    # --- Kullanıcılar ve audit
    "users": table("subject text", "display_name text", "roles text[]", pk=("subject",)),
    "audit_log": table(
        "id bigserial",
        "at timestamptz",
        "actor_kind text",
        "actor_id text",
        "action text",
        "object_type text",
        "object_id text",
        "details jsonb",
        pk=("id",),
    ),
}


@dataclass(frozen=True)
class Column:
    type: str
    nullable: bool
    default: str | None


@dataclass(frozen=True)
class Schema:
    """What the inspector reads from a database at the head revision."""

    tables: set[str]
    columns: dict[str, dict[str, Column]]
    primary_keys: dict[str, tuple[str, ...]]
    unique: dict[str, set[tuple[str, ...]]]
    # Unique indexes that do not back a unique constraint.
    unique_indexes: dict[str, set[str]]
    foreign_keys: dict[str, set[tuple[str, str]]]


def reflect(url: URL) -> Schema:
    dialect = postgresql.dialect()
    engine = create_sync_engine(url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            names = set(inspector.get_table_names()) - {"alembic_version"}
            columns: dict[str, dict[str, Column]] = {}
            primary_keys: dict[str, tuple[str, ...]] = {}
            unique: dict[str, set[tuple[str, ...]]] = {}
            unique_indexes: dict[str, set[str]] = {}
            foreign_keys: dict[str, set[tuple[str, str]]] = {}
            for name in names:
                columns[name] = {
                    column["name"]: Column(
                        type=column["type"].compile(dialect=dialect),
                        nullable=column["nullable"],
                        default=column["default"],
                    )
                    for column in inspector.get_columns(name)
                }
                primary_keys[name] = tuple(inspector.get_pk_constraint(name)["constrained_columns"])
                unique[name] = {
                    tuple(constraint["column_names"])
                    for constraint in inspector.get_unique_constraints(name)
                }
                unique_indexes[name] = {
                    str(index["name"])
                    for index in inspector.get_indexes(name)
                    if index["unique"] and "duplicates_constraint" not in index
                }
                foreign_keys[name] = set()
                for fk in inspector.get_foreign_keys(name):
                    for local, remote in zip(
                        fk["constrained_columns"], fk["referred_columns"], strict=True
                    ):
                        foreign_keys[name].add((local, f"{fk['referred_table']}.{remote}"))
            return Schema(names, columns, primary_keys, unique, unique_indexes, foreign_keys)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def schema(server: Server, template_database: str) -> Iterator[Schema]:
    name = "schema_check"
    server.create_database(name, template=template_database)
    try:
        yield reflect(server.app_url(name))
    finally:
        server.drop_database(name)


def test_tables_are_the_documented_ones(schema: Schema) -> None:
    assert schema.tables == set(DOC)
    assert "knowledge_chunks" not in schema.tables


@pytest.mark.parametrize("name", sorted(DOC))
def test_columns_types_and_nullability_match_the_document(schema: Schema, name: str) -> None:
    expected = {
        column: (PG_TYPES[doc_type], nullable)
        for column, (doc_type, nullable) in DOC[name].columns.items()
    }
    actual = {
        column: (reflected.type, reflected.nullable)
        for column, reflected in schema.columns[name].items()
    }
    assert actual == expected


@pytest.mark.parametrize("name", sorted(DOC))
def test_primary_key_matches_the_document(schema: Schema, name: str) -> None:
    assert schema.primary_keys[name] == DOC[name].primary_key


@pytest.mark.parametrize("name", sorted(DOC))
def test_unique_constraints_match_the_document(schema: Schema, name: str) -> None:
    assert schema.unique[name] == DOC[name].unique
    assert schema.unique_indexes[name] == set()


@pytest.mark.parametrize("name", sorted(DOC))
def test_foreign_keys_match_the_document(schema: Schema, name: str) -> None:
    assert schema.foreign_keys[name] == DOC[name].foreign_keys


def test_only_audit_log_id_is_a_bigserial(schema: Schema) -> None:
    sequences = {
        (table_name, column_name)
        for table_name, columns in schema.columns.items()
        for column_name, column in columns.items()
        if column.default is not None and column.default.startswith("nextval(")
    }
    assert sequences == {("audit_log", "id")}


def test_every_time_column_is_timestamptz(schema: Schema) -> None:
    """Criterion 7: no timestamp without time zone, date or time column anywhere."""
    time_types = {"TIMESTAMP WITHOUT TIME ZONE", "DATE", "TIME WITHOUT TIME ZONE"}
    time_types |= {"TIME WITH TIME ZONE", "INTERVAL"}
    time_like = ("_at", "_start", "_end", "first_seen", "last_seen", "first_reported")
    for table_name, columns in schema.columns.items():
        for column_name, column in columns.items():
            assert column.type not in time_types, f"{table_name}.{column_name}"
            if column_name == "at" or column_name.endswith(time_like):
                assert column.type == "TIMESTAMP WITH TIME ZONE", f"{table_name}.{column_name}"


def test_models_match_the_migrations(database_url: URL) -> None:
    """`ais0c_storage.models` and the migrations describe the same schema."""
    engine = create_sync_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def test_tests_run_on_postgres_with_pgvector(server: Server, database: str) -> None:
    """Criterion 8. The extension is created as the superuser, as deploy/compose does."""
    with server.admin_connection(database) as connection:
        connection.execute("CREATE EXTENSION vector")
        row = connection.execute("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector").fetchone()
        version = connection.execute("SHOW server_version_num").fetchone()
    assert row is not None
    assert row[0] == pytest.approx(1.0)
    assert version is not None
    assert int(version[0]) >= 160000
