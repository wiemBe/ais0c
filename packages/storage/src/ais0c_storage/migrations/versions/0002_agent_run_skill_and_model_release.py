"""agent_runs.skill and agent_runs.model_release (contracts v0.2; T-016).

Both are nullable jsonb. Runs recorded before this revision hold NULL, and so do runs that use
no skill or no model, such as the pseudo agent runs of the offense intake. What the columns
hold is checked against SkillRef and ModelRelease by their column types in
ais0c_storage.models, on every write and read.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB()


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("skill", JSONB, nullable=True))
    op.add_column("agent_runs", sa.Column("model_release", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "model_release")
    op.drop_column("agent_runs", "skill")
