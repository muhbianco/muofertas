"""convites: o convite diário de compartilhamento de cada grupo

Uma linha por grupo por dia (índice único): é a trava do "no máximo um por dia", inclusive com
dois workers ou reinício no meio do envio. O operador pula um dia gravando a linha como `skipped`
antes do horário. Só acrescenta (expand).

Revision ID: 0014_convites
Revises: 0013_off_no_anuncio
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_convites"
down_revision = "0013_off_no_anuncio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_invite",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("channel.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("event_slug", sa.String(60), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("external_message_id", sa.String(64), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(80), nullable=True),
        sa.UniqueConstraint("channel_id", "local_date", name="uq_channel_invite_day"),
    )
    op.create_index("ix_channel_invite_state", "channel_invite", ["state", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_channel_invite_state", table_name="channel_invite")
    op.drop_table("channel_invite")
