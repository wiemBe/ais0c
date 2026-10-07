"""The platform's health alarms and their e-mail route (T-032, decisions T-23, T-68).

`health_alarms` holds one row per alarm: its kind, what it is about (`subject`) and its state.
A partial unique index allows one `open` row per kind and subject; a resolved row stays as
history, and the same subject alarming again is a new row. `kind` and `status` are text columns
whose values the application checks (`ais0c_storage.enums`).

The seed route sends `health_alarm` e-mails, which have no level, to the `analyst-eng` group, as
the other seed routes name their groups without requiring members: an admin populates them.

Downgrade drops the table and the seed route; a `health_alarm` e-mail already recorded in
`notifications` stays.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-07
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT = sa.Text()
TIMESTAMPTZ = sa.DateTime(timezone=True)

# The next ID after the seed routes of revision 0007, which count from 1 to 9.
SEED_ROUTE_ID = uuid.UUID("00000000-0000-4000-8000-000000000010")


def upgrade() -> None:
    op.create_table(
        "health_alarms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", TEXT, nullable=False),
        sa.Column("subject", TEXT, nullable=False),
        sa.Column("status", TEXT, nullable=False),
        sa.Column("opened_at", TIMESTAMPTZ, nullable=False),
        sa.Column("last_seen_at", TIMESTAMPTZ, nullable=False),
        sa.Column("resolved_at", TIMESTAMPTZ, nullable=True),
        sa.Column("last_notified_at", TIMESTAMPTZ, nullable=True),
        sa.Column("details", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_health_alarms"),
    )
    op.create_index(
        "uq_health_alarms_open_kind_subject",
        "health_alarms",
        ["kind", "subject"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    routes = sa.table(
        "notification_routes",
        sa.column("id", sa.Uuid()),
        sa.column("kind", TEXT),
        sa.column("level", TEXT),
        sa.column("list_name", TEXT),
    )
    op.bulk_insert(
        routes,
        [{"id": SEED_ROUTE_ID, "kind": "health_alarm", "level": None, "list_name": "analyst-eng"}],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM notification_routes WHERE id = :id").bindparams(
            sa.bindparam("id", SEED_ROUTE_ID, type_=sa.Uuid())
        )
    )
    op.drop_index("uq_health_alarms_open_kind_subject", table_name="health_alarms")
    op.drop_table("health_alarms")
