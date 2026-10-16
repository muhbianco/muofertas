"""envio em lote: Y anúncios a cada X minutos (pedido do dono em 16/10/2026)

O dono quer experimentar, no grupo que mais rende, 20 anúncios a cada 3 horas em vez de espalhados
no dia. `channel.send_mode` ("ritmo", o de sempre, ou "lote"), `batch_size` e
`batch_every_minutes`. Os lotes saem nos horários alinhados ao começo da janela; link avulso,
relâmpago e últimas unidades continuam saindo a qualquer hora.

Expand: todo grupo começa em "ritmo" (nada muda até o dono trocar). O código antigo ignora.

Revision ID: 0031_envio_em_lote
Revises: 0030_marca_entre_operadores
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_envio_em_lote"
down_revision = "0030_marca_entre_operadores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("channel") as batch:
        batch.add_column(
            sa.Column("send_mode", sa.String(8), nullable=False, server_default="ritmo")
        )
        batch.add_column(sa.Column("batch_size", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("batch_every_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("channel") as batch:
        batch.drop_column("batch_every_minutes")
        batch.drop_column("batch_size")
        batch.drop_column("send_mode")
