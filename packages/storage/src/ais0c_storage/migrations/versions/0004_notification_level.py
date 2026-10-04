"""notifications.level: the notify level of an alert e-mail (T-020).

A case evaluated again is e-mailed again only if its level is above every level already e-mailed
about it (architecture §9). Case and group alerts carry their level; a hunt report has none, so
the column is nullable. Rows written before this revision stay empty.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("level", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "level")
