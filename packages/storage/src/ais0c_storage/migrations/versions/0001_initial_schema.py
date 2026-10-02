"""Initial schema: every table of docs/impl/data-model.md except knowledge_chunks.

knowledge_chunks waits for the embedding model and its vector size, so this revision needs no
extension and runs as the (non-superuser) owner of the database.

audit_log is append-only: a statement-level trigger rejects UPDATE, DELETE and TRUNCATE for
every role, the table owner included.

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT = sa.Text()
BIGINT = sa.BigInteger()
INT = sa.Integer()
BOOL = sa.Boolean()
UUID = sa.Uuid()
TIMESTAMPTZ = sa.DateTime(timezone=True)
JSONB = postgresql.JSONB()
NOW = sa.text("now()")

AUDIT_LOG_GUARD = """
CREATE FUNCTION audit_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

# FOR EACH STATEMENT, so an UPDATE or DELETE that matches no row is rejected as well.
AUDIT_LOG_TRIGGER = """
CREATE TRIGGER audit_log_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_log
FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()
"""


def upgrade() -> None:
    # --- Offense ve vaka
    op.create_table(
        "offenses_seen",
        sa.Column("offense_id", BIGINT, autoincrement=False, nullable=False),
        sa.Column("first_seen_at", TIMESTAMPTZ, nullable=False),
        sa.Column("last_updated_at", TIMESTAMPTZ, nullable=False),
        sa.Column("description", TEXT, nullable=False),
        sa.Column("rule_ids", postgresql.ARRAY(BIGINT), nullable=False),
        sa.Column("catalog_mode", TEXT, nullable=False),
        sa.Column("group_id", TEXT, nullable=True),
        sa.Column("case_id", TEXT, nullable=True),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("pre_priority", INT, nullable=False),
        sa.PrimaryKeyConstraint("offense_id", name="pk_offenses_seen"),
    )
    op.create_index("ix_offenses_seen_group_id", "offenses_seen", ["group_id"])
    op.create_index("ix_offenses_seen_status", "offenses_seen", ["status"])

    op.create_table(
        "offense_groups",
        sa.Column("group_id", TEXT, nullable=False),
        sa.Column("rule_set_hash", TEXT, nullable=False),
        sa.Column("window_start", TIMESTAMPTZ, nullable=False),
        sa.Column("window_end", TIMESTAMPTZ, nullable=False),
        sa.Column("offense_count", INT, nullable=False),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("case_id", TEXT, nullable=True),
        sa.PrimaryKeyConstraint("group_id", name="pk_offense_groups"),
    )
    op.create_index("ix_offense_groups_rule_set_hash", "offense_groups", ["rule_set_hash"])

    op.create_table(
        "cases",
        sa.Column("case_id", TEXT, nullable=False),
        sa.Column("source", TEXT, nullable=False),
        sa.Column("offense_id", BIGINT, nullable=True),
        sa.Column("hunt_id", TEXT, nullable=True),
        sa.Column("group_id", TEXT, nullable=True),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("verdict", TEXT, nullable=True),
        sa.Column("confidence", TEXT, nullable=True),
        sa.Column("ai_level", TEXT, nullable=True),
        sa.Column("floor_level", TEXT, nullable=True),
        sa.Column("notify_level", TEXT, nullable=True),
        sa.Column("report", JSONB, nullable=True),
        sa.Column("evaluation_no", INT, nullable=False),
        sa.Column("sla_due_at", TIMESTAMPTZ, nullable=False),
        sa.Column("decided_at", TIMESTAMPTZ, nullable=True),
        sa.Column("workflow_id", TEXT, nullable=False),
        sa.Column("run_id", TEXT, nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("case_id", name="pk_cases"),
    )

    op.create_table(
        "qa_items",
        sa.Column("id", UUID, nullable=False),
        sa.Column("case_id", TEXT, nullable=False),
        sa.Column("reason", TEXT, nullable=False),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("resolved_by", TEXT, nullable=True),
        sa.Column("resolved_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_qa_items"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.case_id"], name="fk_qa_items_case_id_cases"),
    )
    op.create_index("ix_qa_items_case_id", "qa_items", ["case_id"])

    op.create_table(
        "operator_feedback",
        sa.Column("id", UUID, nullable=False),
        sa.Column("case_id", TEXT, nullable=False),
        sa.Column("user_subject", TEXT, nullable=False),
        sa.Column("verdict", TEXT, nullable=False),
        sa.Column("reason", TEXT, nullable=False),
        sa.Column("comment", TEXT, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_operator_feedback"),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_operator_feedback_case_id_cases"
        ),
    )
    op.create_index("ix_operator_feedback_case_id", "operator_feedback", ["case_id"])

    # --- Ajan çalışmaları ve kanıt
    op.create_table(
        "agent_runs",
        sa.Column("run_id", TEXT, nullable=False),
        sa.Column("case_id", TEXT, nullable=True),
        sa.Column("hunt_id", TEXT, nullable=True),
        sa.Column("agent_id", TEXT, nullable=False),
        sa.Column("agent_version", TEXT, nullable=False),
        sa.Column("prompt_version", TEXT, nullable=False),
        sa.Column("model_alias", TEXT, nullable=False),
        sa.Column("model_target", TEXT, nullable=False),
        sa.Column("toolset_profile", TEXT, nullable=False),
        # NULL while the run is in progress.
        sa.Column("status", TEXT, nullable=True),
        sa.Column("task", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("tokens", INT, nullable=False),
        sa.Column("tool_calls", INT, nullable=False),
        sa.Column("started_at", TIMESTAMPTZ, nullable=False),
        # NULL while the run is in progress.
        sa.Column("ended_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("run_id", name="pk_agent_runs"),
    )
    op.create_index("ix_agent_runs_case_id", "agent_runs", ["case_id"])
    op.create_index("ix_agent_runs_hunt_id", "agent_runs", ["hunt_id"])

    op.create_table(
        "tool_calls",
        sa.Column("id", UUID, nullable=False),
        sa.Column("run_id", TEXT, nullable=False),
        sa.Column("intent", JSONB, nullable=False),
        sa.Column("policy_decision", TEXT, nullable=False),
        sa.Column("deny_reason", TEXT, nullable=True),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("evidence_id", TEXT, nullable=True),
        sa.Column("latency_ms", INT, nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tool_calls"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.run_id"], name="fk_tool_calls_run_id_agent_runs"
        ),
    )
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"])

    op.create_table(
        "evidence",
        sa.Column("evidence_id", TEXT, nullable=False),
        sa.Column("source", TEXT, nullable=False),
        sa.Column("query_text", TEXT, nullable=False),
        sa.Column("query_hash", TEXT, nullable=False),
        sa.Column("time_start", TIMESTAMPTZ, nullable=False),
        sa.Column("time_end", TIMESTAMPTZ, nullable=False),
        sa.Column("identifiers", JSONB, nullable=False),
        sa.Column("excerpt", TEXT, nullable=False),
        sa.Column("retrieved_at", TIMESTAMPTZ, nullable=False),
        sa.Column("expires_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_evidence"),
    )

    op.create_table(
        "urgent_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("case_id", TEXT, nullable=False),
        sa.Column("evaluation_no", INT, nullable=False),
        sa.Column("rank", INT, nullable=False),
        sa.Column("event", JSONB, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_urgent_events"),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_urgent_events_case_id_cases"
        ),
    )
    op.create_index("ix_urgent_events_case_id", "urgent_events", ["case_id"])

    op.create_table(
        "recommendations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("case_id", TEXT, nullable=False),
        sa.Column("evaluation_no", INT, nullable=False),
        sa.Column("recommendation", JSONB, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_recommendations"),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_recommendations_case_id_cases"
        ),
    )
    op.create_index("ix_recommendations_case_id", "recommendations", ["case_id"])

    # --- Yazma ve gönderim kayıtları
    op.create_table(
        "notes_written",
        sa.Column("id", UUID, nullable=False),
        sa.Column("case_id", TEXT, nullable=False),
        sa.Column("offense_id", BIGINT, nullable=False),
        sa.Column("evaluation_no", INT, nullable=False),
        sa.Column("run_marker", TEXT, nullable=False),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("error", TEXT, nullable=True),
        sa.Column("written_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notes_written"),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_notes_written_case_id_cases"
        ),
        sa.UniqueConstraint(
            "offense_id", "run_marker", name="uq_notes_written_offense_id_run_marker"
        ),
    )
    op.create_index("ix_notes_written_case_id", "notes_written", ["case_id"])

    op.create_table(
        "notifications",
        sa.Column("id", UUID, nullable=False),
        sa.Column("kind", TEXT, nullable=False),
        sa.Column("case_id", TEXT, nullable=True),
        sa.Column("hunt_id", TEXT, nullable=True),
        sa.Column("group_id", TEXT, nullable=True),
        sa.Column("recipients", postgresql.ARRAY(TEXT), nullable=False),
        sa.Column("subject", TEXT, nullable=False),
        sa.Column("idempotency_key", TEXT, nullable=False),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("sent_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.UniqueConstraint("idempotency_key", name="uq_notifications_idempotency_key"),
    )
    op.create_index("ix_notifications_case_id", "notifications", ["case_id"])

    # --- Analiz Kataloğu ve kurum bağlamı
    op.create_table(
        "catalog_rules",
        sa.Column("rule_id", BIGINT, autoincrement=False, nullable=False),
        sa.Column("rule_name", TEXT, nullable=False),
        sa.Column("defined", BOOL, nullable=False),
        sa.Column("mode", TEXT, server_default=sa.text("'analyze'"), nullable=False),
        sa.Column("min_level", TEXT, nullable=True),
        sa.Column("has_automated_action", BOOL, nullable=False),
        sa.Column("context_note", TEXT, nullable=True),
        sa.Column("ai_draft_note", TEXT, nullable=True),
        sa.Column("updated_by", TEXT, nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("rule_id", name="pk_catalog_rules"),
    )

    op.create_table(
        "catalog_log_sources",
        sa.Column("log_source_id", BIGINT, autoincrement=False, nullable=False),
        sa.Column("name", TEXT, nullable=False),
        sa.Column("type_name", TEXT, nullable=False),
        sa.Column("defined", BOOL, nullable=False),
        sa.Column("description", TEXT, nullable=True),
        sa.Column("owner", TEXT, nullable=True),
        sa.Column("criticality", TEXT, nullable=True),
        sa.Column("in_scope", BOOL, nullable=False),
        sa.Column("context_note", TEXT, nullable=True),
        sa.Column("updated_by", TEXT, nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("log_source_id", name="pk_catalog_log_sources"),
    )

    op.create_table(
        "critical_assets",
        sa.Column("id", UUID, nullable=False),
        sa.Column("kind", TEXT, nullable=False),
        sa.Column("value", TEXT, nullable=False),
        sa.Column("label", TEXT, nullable=False),
        sa.Column("level", TEXT, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_critical_assets"),
    )

    op.create_table(
        "notification_recipients",
        sa.Column("list_name", TEXT, nullable=False),
        sa.Column("email", TEXT, nullable=False),
        sa.PrimaryKeyConstraint("list_name", "email", name="pk_notification_recipients"),
    )

    op.create_table(
        "allowed_email_domains",
        sa.Column("domain", TEXT, nullable=False),
        sa.PrimaryKeyConstraint("domain", name="pk_allowed_email_domains"),
    )

    # --- Hunt
    op.create_table(
        "hunt_packs",
        sa.Column("pack_id", TEXT, nullable=False),
        sa.Column("version", TEXT, nullable=False),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("actor_id", TEXT, nullable=True),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("approved_by", TEXT, nullable=True),
        sa.Column("approved_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("pack_id", "version", name="pk_hunt_packs"),
    )

    op.create_table(
        "hunts",
        sa.Column("hunt_id", TEXT, nullable=False),
        sa.Column("request", JSONB, nullable=False),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("outcome", TEXT, nullable=True),
        sa.Column("report", JSONB, nullable=True),
        sa.Column("report_pdf_path", TEXT, nullable=True),
        sa.Column("schedule_id", UUID, nullable=True),
        sa.Column("created_by", TEXT, nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.Column("completed_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("hunt_id", name="pk_hunts"),
    )

    op.create_table(
        "hunt_slices",
        sa.Column("hunt_id", TEXT, nullable=False),
        sa.Column("hypothesis_id", TEXT, nullable=False),
        sa.Column("log_source_type", TEXT, nullable=False),
        sa.Column("slice_start", TIMESTAMPTZ, nullable=False),
        sa.Column("slice_end", TIMESTAMPTZ, nullable=False),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("query_hash", TEXT, nullable=True),
        sa.Column("evidence_id", TEXT, nullable=True),
        sa.PrimaryKeyConstraint(
            "hunt_id", "hypothesis_id", "log_source_type", "slice_start", name="pk_hunt_slices"
        ),
        sa.ForeignKeyConstraint(
            ["hunt_id"], ["hunts.hunt_id"], name="fk_hunt_slices_hunt_id_hunts"
        ),
    )

    op.create_table(
        "analytic_results",
        sa.Column("id", UUID, nullable=False),
        sa.Column("hunt_id", TEXT, nullable=False),
        sa.Column("slice_start", TIMESTAMPTZ, nullable=False),
        sa.Column("analytic", TEXT, nullable=False),
        sa.Column("entity", TEXT, nullable=True),
        sa.Column("field", TEXT, nullable=False),
        sa.Column("value", TEXT, nullable=False),
        sa.Column("count", BIGINT, nullable=False),
        sa.Column("score", sa.Double(), nullable=True),
        sa.Column("evidence_id", TEXT, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_analytic_results"),
        sa.ForeignKeyConstraint(
            ["hunt_id"], ["hunts.hunt_id"], name="fk_analytic_results_hunt_id_hunts"
        ),
    )
    op.create_index("ix_analytic_results_hunt_id", "analytic_results", ["hunt_id"])

    op.create_table(
        "hunt_findings",
        sa.Column("id", UUID, nullable=False),
        sa.Column("hunt_id", TEXT, nullable=False),
        sa.Column("hypothesis_id", TEXT, nullable=False),
        sa.Column("outcome", TEXT, nullable=False),
        sa.Column("claims", JSONB, nullable=False),
        sa.Column("case_id", TEXT, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_hunt_findings"),
        sa.ForeignKeyConstraint(
            ["hunt_id"], ["hunts.hunt_id"], name="fk_hunt_findings_hunt_id_hunts"
        ),
    )
    op.create_index("ix_hunt_findings_hunt_id", "hunt_findings", ["hunt_id"])

    op.create_table(
        "hunt_schedules",
        sa.Column("id", UUID, nullable=False),
        sa.Column("pack_id", TEXT, nullable=False),
        sa.Column("cron", TEXT, nullable=False),
        sa.Column("window_days", INT, nullable=False),
        sa.Column("scope", JSONB, nullable=False),
        sa.Column("enabled", BOOL, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_hunt_schedules"),
    )

    # --- Bilgi düzlemi (knowledge_chunks comes later)
    op.create_table(
        "actors",
        sa.Column("actor_id", TEXT, nullable=False),
        sa.Column("name", TEXT, nullable=False),
        sa.Column("sector_relevance", TEXT, nullable=False),
        sa.Column("priority", INT, nullable=False),
        sa.Column("sources", JSONB, nullable=False),
        sa.PrimaryKeyConstraint("actor_id", name="pk_actors"),
    )

    op.create_table(
        "actor_aliases",
        sa.Column("actor_id", TEXT, nullable=False),
        sa.Column("alias", TEXT, nullable=False),
        sa.Column("source", TEXT, nullable=False),
        sa.PrimaryKeyConstraint("actor_id", "alias", "source", name="pk_actor_aliases"),
    )

    op.create_table(
        "techniques",
        sa.Column("technique_id", TEXT, nullable=False),
        sa.Column("name", TEXT, nullable=False),
        sa.Column("tactics", postgresql.ARRAY(TEXT), nullable=False),
        sa.PrimaryKeyConstraint("technique_id", name="pk_techniques"),
    )

    op.create_table(
        "actor_techniques",
        sa.Column("actor_id", TEXT, nullable=False),
        sa.Column("technique_id", TEXT, nullable=False),
        sa.Column("source", TEXT, nullable=False),
        sa.Column("first_reported", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("actor_id", "technique_id", "source", name="pk_actor_techniques"),
    )

    op.create_table(
        "iocs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("type", TEXT, nullable=False),
        sa.Column("value", TEXT, nullable=False),
        sa.Column("first_seen", TIMESTAMPTZ, nullable=False),
        sa.Column("last_seen", TIMESTAMPTZ, nullable=False),
        sa.Column("confidence", TEXT, nullable=False),
        sa.Column("tlp", TEXT, nullable=False),
        sa.Column("source", TEXT, nullable=False),
        sa.Column("actor_id", TEXT, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_iocs"),
    )

    op.create_table(
        "cti_reports",
        sa.Column("report_id", TEXT, nullable=False),
        sa.Column("source", TEXT, nullable=False),
        sa.Column("title", TEXT, nullable=False),
        sa.Column("published_at", TIMESTAMPTZ, nullable=False),
        sa.Column("url", TEXT, nullable=True),
        sa.PrimaryKeyConstraint("report_id", name="pk_cti_reports"),
    )

    # --- Tuning
    op.create_table(
        "fp_clusters",
        sa.Column("cluster_id", TEXT, nullable=False),
        sa.Column("rule_id", BIGINT, nullable=False),
        sa.Column("pattern", JSONB, nullable=False),
        sa.Column("case_ids", postgresql.ARRAY(TEXT), nullable=False),
        sa.Column("size", INT, nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("cluster_id", name="pk_fp_clusters"),
    )

    op.create_table(
        "tuning_proposals",
        sa.Column("id", UUID, nullable=False),
        sa.Column("cluster_id", TEXT, nullable=False),
        sa.Column("proposal", JSONB, nullable=False),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("decided_by", TEXT, nullable=True),
        sa.Column("decided_at", TIMESTAMPTZ, nullable=True),
        sa.Column("comment", TEXT, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_tuning_proposals"),
        sa.ForeignKeyConstraint(
            ["cluster_id"],
            ["fp_clusters.cluster_id"],
            name="fk_tuning_proposals_cluster_id_fp_clusters",
        ),
    )
    op.create_index("ix_tuning_proposals_cluster_id", "tuning_proposals", ["cluster_id"])

    # --- Kullanıcılar ve audit
    op.create_table(
        "users",
        sa.Column("subject", TEXT, nullable=False),
        sa.Column("display_name", TEXT, nullable=False),
        sa.Column("roles", postgresql.ARRAY(TEXT), nullable=False),
        sa.PrimaryKeyConstraint("subject", name="pk_users"),
    )

    op.create_table(
        "audit_log",
        # bigserial
        sa.Column("id", BIGINT, autoincrement=True, nullable=False),
        sa.Column("at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.Column("actor_kind", TEXT, nullable=False),
        sa.Column("actor_id", TEXT, nullable=False),
        sa.Column("action", TEXT, nullable=False),
        sa.Column("object_type", TEXT, nullable=False),
        sa.Column("object_id", TEXT, nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_object_type_object_id", "audit_log", ["object_type", "object_id"])
    op.execute(AUDIT_LOG_GUARD)
    op.execute(AUDIT_LOG_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION audit_log_append_only()")
    # Children before the tables they reference; drop_table also drops the table's indexes.
    for table in (
        "audit_log",
        "users",
        "tuning_proposals",
        "fp_clusters",
        "cti_reports",
        "iocs",
        "actor_techniques",
        "techniques",
        "actor_aliases",
        "actors",
        "hunt_schedules",
        "hunt_findings",
        "analytic_results",
        "hunt_slices",
        "hunts",
        "hunt_packs",
        "allowed_email_domains",
        "notification_recipients",
        "critical_assets",
        "catalog_log_sources",
        "catalog_rules",
        "notifications",
        "notes_written",
        "recommendations",
        "urgent_events",
        "evidence",
        "tool_calls",
        "agent_runs",
        "operator_feedback",
        "qa_items",
        "cases",
        "offense_groups",
        "offenses_seen",
    ):
        op.drop_table(table)
