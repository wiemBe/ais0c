"""Named notification groups and routing by kind and level (T-036, D-41, T-43).

Existing recipient rows keep their group and address. The group name is now checked against
the admin-defined name pattern rather than a fixed application enum. Seed routes reference
group names without requiring members: an admin can populate them after the upgrade.

Downgrade drops the routing table and name checks. Recipient rows, including custom groups
created after the upgrade, remain intact: revision 0006 also stores the name as plain text.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GROUP_NAME_CHECK = "list_name ~ '^[a-z][a-z0-9-]{0,62}$'"
SEED_ROUTES = (
    ("case_alert", "high", "operators"),
    ("case_alert", "critical", "operators"),
    ("case_alert", "critical", "exec"),
    ("case_alert", "critical", "analyst-eng"),
    ("group_alert", "high", "operators"),
    ("group_alert", "critical", "operators"),
    ("group_alert", "critical", "exec"),
    ("group_alert", "critical", "analyst-eng"),
    ("hunt_report", None, "hunters"),
)


def upgrade() -> None:
    op.create_check_constraint(
        "ck_notification_recipients_list_name", "notification_recipients", GROUP_NAME_CHECK
    )
    routes = op.create_table(
        "notification_routes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=True),
        sa.Column("list_name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notification_routes"),
        sa.UniqueConstraint(
            "kind",
            "level",
            "list_name",
            name="uq_notification_routes_kind_level_list_name",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(GROUP_NAME_CHECK, name="ck_notification_routes_list_name"),
    )
    op.bulk_insert(
        routes,
        [
            {
                "id": uuid.UUID(f"00000000-0000-4000-8000-{index:012d}"),
                "kind": kind,
                "level": level,
                "list_name": list_name,
            }
            for index, (kind, level, list_name) in enumerate(SEED_ROUTES, start=1)
        ],
    )


def downgrade() -> None:
    op.drop_table("notification_routes")
    op.drop_constraint(
        "ck_notification_recipients_list_name", "notification_recipients", type_="check"
    )
