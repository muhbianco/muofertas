"""alertas: alertas críticos da operação (coleta parada, worker parado, erros do ML)

Só acrescenta (expand).

Revision ID: 0007_alertas
Revises: 0006_pix
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_alertas"
down_revision = "0006_pix"
branch_labels = None
depends_on = None

BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=False)


def upgrade() -> None:
    op.create_table(
        "alert",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("message", sa.String(255), nullable=False),
        sa.Column("opened_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("resolved_at", TS, nullable=True),
        sa.Column("notified_at", TS, nullable=True),
        sa.Column("resolution_notified_at", TS, nullable=True),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_alert_state_kind", "alert", ["state", "kind"])


def downgrade() -> None:
    op.drop_table("alert")
