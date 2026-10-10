"""envio automático: hora do Pix e controle de envio pela WuzAPI na publicação

Só acrescenta (expand). Estados novos de `publication.state` (texto livre): `sending`,
`unknown`, `failed`.

Revision ID: 0009_envio_automatico
Revises: 0008_whatsapp
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_envio_automatico"
down_revision = "0008_whatsapp"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=False)


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("pix_at", TS, nullable=True))
        batch.add_column(sa.Column("sending_at", TS, nullable=True))
        batch.add_column(sa.Column("external_message_id", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("external_message_id")
        batch.drop_column("sending_at")
        batch.drop_column("pix_at")
