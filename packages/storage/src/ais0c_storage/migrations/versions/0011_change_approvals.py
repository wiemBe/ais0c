"""change_approvals: double control of the changes of D-36 (T-033, decision T-77).

A change asked through the API waits here as `pending` until a second admin approves it. The
database holds the rules that must not depend on the API: the decider is never the requester, an
object has at most one pending request, and only a rejected request has a reason.

`decided_by` stays empty for a request that ended without a second admin's decision (withdrawn by
the requester, stale because the object changed), so the requester never appears as their own
decider. `comment` is the second admin's comment on a rejection; it is not in data-model.md.

Downgrade drops the table and with it every pending and decided request.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT = sa.Text()
TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "change_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("object_type", TEXT, nullable=False),
        sa.Column("object_id", TEXT, nullable=False),
        sa.Column("object_version", TEXT, nullable=False),
        sa.Column("change", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("requested_by", TEXT, nullable=False),
        sa.Column("requested_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("decided_by", TEXT, nullable=True),
        sa.Column("decided_at", TIMESTAMPTZ, nullable=True),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("reason", TEXT, nullable=True),
        sa.Column("comment", TEXT, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_change_approvals"),
        sa.CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by",
            name="ck_change_approvals_decider_is_not_requester",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)",
            name="ck_change_approvals_decided_at_iff_decided",
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (reason IS NOT NULL)",
            name="ck_change_approvals_reason_iff_rejected",
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR decided_by IS NOT NULL",
            name="ck_change_approvals_approval_has_decider",
        ),
    )
    op.create_index(
        "uq_change_approvals_pending_object",
        "change_approvals",
        ["object_type", "object_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_change_approvals_status_requested_at", "change_approvals", ["status", "requested_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_change_approvals_status_requested_at", table_name="change_approvals")
    op.drop_index("uq_change_approvals_pending_object", table_name="change_approvals")
    op.drop_table("change_approvals")
