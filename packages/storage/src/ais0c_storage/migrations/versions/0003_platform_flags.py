"""platform_flags: switches such as the kill switch (T-23; T-017).

No row is inserted. A flag without a row is off, so a new database starts with `writes_enabled`
off: the platform runs in shadow mode and writes nothing outside itself until an admin switches
writes on (ais0c_storage.repositories.set_platform_flag, which audits the change).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT = sa.Text()
BOOL = sa.Boolean()
TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "platform_flags",
        sa.Column("name", TEXT, nullable=False),
        sa.Column("enabled", BOOL, nullable=False),
        sa.Column("reason", TEXT, nullable=True),
        sa.Column("changed_by", TEXT, nullable=False),
        sa.Column("changed_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("name", name="pk_platform_flags"),
    )


def downgrade() -> None:
    op.drop_table("platform_flags")
