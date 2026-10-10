"""whatsapp: sessão da WuzAPI, grupo do WhatsApp no canal e tamanho diário dos grupos

Só acrescenta (expand). Sem token no banco: os dois tokens ficam no Env da stack.

Revision ID: 0008_whatsapp
Revises: 0007_alertas
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_whatsapp"
down_revision = "0007_alertas"
branch_labels = None
depends_on = None

BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=False)
TABLE_OPTS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "wa_session",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("jid", sa.String(80), nullable=True),
        sa.Column("logged_in", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("checked_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        **TABLE_OPTS,
    )
    op.create_table(
        "channel_stat",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("channel.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("members", sa.Integer(), nullable=False),
        sa.Column("admins", sa.Integer(), nullable=False),
        sa.Column("captured_at", TS, nullable=False),
        sa.UniqueConstraint("channel_id", "day"),
        **TABLE_OPTS,
    )
    with op.batch_alter_table("channel") as batch:
        batch.add_column(sa.Column("wa_jid", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_channel_wa_jid", ["wa_jid"])


def downgrade() -> None:
    with op.batch_alter_table("channel") as batch:
        batch.drop_constraint("uq_channel_wa_jid", type_="unique")
        batch.drop_column("wa_jid")
    op.drop_table("channel_stat")
    op.drop_table("wa_session")
