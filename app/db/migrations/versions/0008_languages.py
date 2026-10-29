"""Per-user language; English names for the built-in device types

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Built-in types seeded with Romanian names before the app was translated.
RENAMED_TYPES = {
    "Imprimantă": "Printer",
    "Telefon": "Phone",
    "Tabletă": "Tablet",
    "Altele": "Other",
}


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("language", sa.String(length=5), nullable=False, server_default="en")
        )
    conn = op.get_bind()
    # Existing installs were Romanian-only: keep their users on Romanian.
    conn.execute(sa.text("UPDATE users SET language = 'ro'"))
    for old, new in RENAMED_TYPES.items():
        conn.execute(
            sa.text(
                "UPDATE device_types SET name = :new WHERE name = :old AND is_builtin = 1"
                " AND NOT EXISTS (SELECT 1 FROM device_types WHERE name = :new)"
            ),
            {"old": old, "new": new},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for old, new in RENAMED_TYPES.items():
        conn.execute(
            sa.text("UPDATE device_types SET name = :old WHERE name = :new AND is_builtin = 1"),
            {"old": old, "new": new},
        )
    with op.batch_alter_table("users") as batch:
        batch.drop_column("language")
