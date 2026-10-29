"""Oprire de la distanță prin SSH

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column("shutdown_enabled", sa.Boolean(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("ssh_user", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("os", sa.String(length=10), nullable=True))
        batch.add_column(sa.Column("ssh_host_key", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("ssh_host_key")
        batch.drop_column("os")
        batch.drop_column("ssh_user")
        batch.drop_column("shutdown_enabled")
