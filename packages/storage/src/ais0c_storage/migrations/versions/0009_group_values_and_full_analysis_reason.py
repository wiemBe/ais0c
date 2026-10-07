"""The values a group keeps of its offenses and why an offense got a full analysis (T-027).

`offense_group_values` holds one row per group, kind, value and offense: the group's seen
values (T-62), from which the group's summary counts its most frequent values and the intake
finds a log source or category new to the group. Groups that exist at upgrade time start
without values; their next offenses fill them.

`offenses_seen.full_analysis_reason` says why an offense of a group got a full analysis, so the
hourly limit, the novelty escapes and the hourly sample keep separate counters (T-62 (3), T-22).
Offenses recorded before this revision have none and count against the limit, as they did.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("offenses_seen", sa.Column("full_analysis_reason", sa.Text(), nullable=True))
    op.create_table(
        "offense_group_values",
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("offense_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "group_id", "kind", "value", "offense_id", name="pk_offense_group_values"
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["offense_groups.group_id"],
            name="fk_offense_group_values_group_id_offense_groups",
        ),
        sa.ForeignKeyConstraint(
            ["offense_id"],
            ["offenses_seen.offense_id"],
            name="fk_offense_group_values_offense_id_offenses_seen",
        ),
    )


def downgrade() -> None:
    op.drop_table("offense_group_values")
    op.drop_column("offenses_seen", "full_analysis_reason")
