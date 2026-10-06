"""QA evaluation identity and agent run errors (T-57).

Existing QA items belong to the latest evaluation of their case at upgrade time. The unique
constraint then enforces one reason per evaluation. Agent run errors are nullable because completed
runs and runs recorded before this revision have none.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QA_EVALUATION_REASON_UNIQUE = "uq_qa_items_case_id_evaluation_no_reason"


def upgrade() -> None:
    op.add_column("qa_items", sa.Column("evaluation_no", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE qa_items SET evaluation_no = cases.evaluation_no "
        "FROM cases WHERE cases.case_id = qa_items.case_id"
    )
    # The old schema could hold the same reason from several evaluations. They all map to the
    # case's latest evaluation, so keep the oldest row before enforcing the new identity.
    op.execute(
        "DELETE FROM qa_items newer USING qa_items older "
        "WHERE newer.case_id = older.case_id AND newer.reason = older.reason "
        "AND newer.id > older.id"
    )
    op.alter_column("qa_items", "evaluation_no", nullable=False)
    op.create_unique_constraint(
        QA_EVALUATION_REASON_UNIQUE, "qa_items", ["case_id", "evaluation_no", "reason"]
    )
    op.add_column("agent_runs", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "error")
    op.drop_constraint(QA_EVALUATION_REASON_UNIQUE, "qa_items", type_="unique")
    op.drop_column("qa_items", "evaluation_no")
