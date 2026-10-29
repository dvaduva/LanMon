"""Notificări: marcaj pe dispozitiv și reguli implicite

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("notify", sa.Boolean(), nullable=False, server_default="0"))
    settings = sa.table("settings", sa.column("key", sa.String), sa.column("value", sa.Text))
    op.bulk_insert(
        settings,
        [
            {"key": "notify_offline_min", "value": "10"},
            {"key": "notify_new_device", "value": "1"},
            {"key": "notify_wol_failed", "value": "1"},
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM settings WHERE key IN "
        "('notify_offline_min', 'notify_new_device', 'notify_wol_failed')"
    )
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("notify")
