"""convites: convite de operador por e-mail (link de uso único, 48 h)

Só acrescenta (expand).

Revision ID: 0010_convites
Revises: 0009_envio_automatico
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_convites"
down_revision = "0009_envio_automatico"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=False)


def upgrade() -> None:
    op.create_table(
        "admin_invite",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("username", sa.String(40), nullable=True),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )


def downgrade() -> None:
    op.drop_table("admin_invite")
