"""catalog_rules.attack_techniques: the ATT&CK techniques of a rule (T-021, decision T-26).

The skill router reads an offense's ATT&CK tags from its rules' catalog entries. Operators map
the techniques; the QRadar sync never changes them. Existing rules get an empty array.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_rules",
        sa.Column(
            "attack_techniques",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("catalog_rules", "attack_techniques")
