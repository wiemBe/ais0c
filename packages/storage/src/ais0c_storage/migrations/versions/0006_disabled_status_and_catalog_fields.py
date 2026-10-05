"""The data model of contracts v0.4 (T-041, decision T-37): the `disabled` status,
notifications.error, catalog_rules.qradar_enabled and the catalog's missing_since.

- `notes_written.status` and `notifications.status` take `disabled`: a note or an e-mail the
  kill switch held back (shadow mode, T-23). It is not a failure, so the error alarms do not
  count it. Both are text columns whose values the application checks (`ais0c_storage.enums`);
  the schema holds no list of values to change.
- Notes that T-019 recorded as `failed` with an error starting with `writes_disabled:` become
  `disabled`, without the error: a `disabled` record carries no error text. T-020 recorded
  nothing for an e-mail the kill switch held back, so no e-mail moves.
- `notifications.error`: why a `failed` or `rejected` e-mail was not sent. Earlier rows stay
  empty.
- `catalog_rules.qradar_enabled`: whether the rule is enabled in QRadar. Existing rules get
  `true` until the next sync reads QRadar's value.
- `catalog_rules.missing_since` and `catalog_log_sources.missing_since`: the sync that first
  found the entry missing from QRadar's lists. Existing entries start empty; the next sync marks
  the ones QRadar no longer lists.

Downgrade: a `disabled` note becomes `failed` with an error starting with `writes_disabled: `,
as T-019 recorded it, and a `disabled` e-mail becomes `failed`, which the code before this
revision tries again as well; `notifications.error` is dropped with the other new columns, so
the e-mail keeps no error text.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-05
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# How T-019 recorded a note the kill switch held back: status `failed`, and an error with this
# prefix followed by the switch's message, or by the text below when there was none.
WRITES_DISABLED_PREFIX: Final = "writes_disabled: "
WRITES_DISABLED_ERROR: Final = WRITES_DISABLED_PREFIX + "the kill switch is off"

TIMESTAMPTZ = sa.DateTime(timezone=True)

_notes = sa.table("notes_written", sa.column("status", sa.Text()), sa.column("error", sa.Text()))
_notifications = sa.table("notifications", sa.column("status", sa.Text()))


def upgrade() -> None:
    op.add_column("notifications", sa.Column("error", sa.Text(), nullable=True))
    op.add_column(
        "catalog_rules",
        sa.Column("qradar_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column("catalog_rules", sa.Column("missing_since", TIMESTAMPTZ, nullable=True))
    op.add_column("catalog_log_sources", sa.Column("missing_since", TIMESTAMPTZ, nullable=True))
    # error LIKE 'writes_disabled:%', with the `_` escaped so that it is no wildcard.
    op.execute(
        _notes.update()
        .where(
            _notes.c.status == "failed",
            _notes.c.error.startswith("writes_disabled:", autoescape=True),
        )
        .values(status="disabled", error=None)
    )


def downgrade() -> None:
    op.execute(
        _notes.update()
        .where(_notes.c.status == "disabled")
        .values(status="failed", error=WRITES_DISABLED_ERROR)
    )
    op.execute(
        _notifications.update().where(_notifications.c.status == "disabled").values(status="failed")
    )
    op.drop_column("catalog_log_sources", "missing_since")
    op.drop_column("catalog_rules", "missing_since")
    op.drop_column("catalog_rules", "qradar_enabled")
    op.drop_column("notifications", "error")
