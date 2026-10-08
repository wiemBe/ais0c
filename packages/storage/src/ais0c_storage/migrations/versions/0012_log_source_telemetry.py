"""catalog_log_sources: QRadar's enabled state and telemetry classes (T-068, decision T-95).

- `qradar_enabled`: whether the log source is enabled in QRadar. Existing log sources get `true`
  until the next sync reads QRadar's value.
- `default_telemetry_classes`: the classes of the log source's type, written by KnowledgeSync
  from `config/telemetry/log-source-classes.yaml`. Existing log sources start with none until the
  next sync.
- `telemetry_classes`: the classes an admin assigned; `NULL` means the defaults apply. Both
  class columns are text arrays whose values the application checks (`TelemetryClass`); the
  schema holds no list of values to change.

Downgrade drops the three columns and with them the classes an admin assigned.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_log_sources",
        sa.Column("qradar_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "catalog_log_sources",
        sa.Column(
            "default_telemetry_classes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "catalog_log_sources",
        sa.Column("telemetry_classes", postgresql.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalog_log_sources", "telemetry_classes")
    op.drop_column("catalog_log_sources", "default_telemetry_classes")
    op.drop_column("catalog_log_sources", "qradar_enabled")
