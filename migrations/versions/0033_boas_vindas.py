"""boas-vindas a quem entra no grupo (pedido do dono em 22/10/2026)

O worker olha a lista do grupo a cada 5 min e junta quem entrou numa espera; a saudação sai uma
vez por leva. `channel_greeting` guarda a espera e o envio, só com a contagem: quem entrou não
vai para o banco.

Só tabela nova (expand). O código antigo não a lê.

Revision ID: 0033_boas_vindas
Revises: 0032_repeticao_24h
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_boas_vindas"
down_revision = "0032_repeticao_24h"
branch_labels = None
depends_on = None

BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TABLE_OPTS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "channel_greeting",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("channel.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("new_members", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_join_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("last_join_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("external_message_id", sa.String(64), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        **TABLE_OPTS,
    )
    op.create_index(
        "ix_channel_greeting_channel_state", "channel_greeting", ["channel_id", "state"]
    )
    op.create_index("ix_channel_greeting_state", "channel_greeting", ["state", "created_at"])


def downgrade() -> None:
    op.drop_table("channel_greeting")
