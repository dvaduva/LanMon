"""Verificări pe porturi TCP

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("device_types") as batch:
        batch.add_column(sa.Column("check_ports", sa.String(length=100), nullable=True))
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("check_ports", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("service_status", sa.String(length=10), nullable=True))
        batch.add_column(sa.Column("closed_ports", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("closed_ports")
        batch.drop_column("service_status")
        batch.drop_column("check_ports")
    with op.batch_alter_table("device_types") as batch:
        batch.drop_column("check_ports")
