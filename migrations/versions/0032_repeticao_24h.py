"""repetição em 24 h, agendada (pedido do dono em 16/10/2026... 20/10/2026)

O mesmo produto no mesmo grupo ficava de fora por 7 dias ("já foi para este grupo"). Agora entra
na fila e sai quando completar 24 h do último envio: `publication.not_before`.

Só coluna nova (expand). O código antigo a ignora.

Revision ID: 0032_repeticao_24h
Revises: 0031_envio_em_lote
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_repeticao_24h"
down_revision = "0031_envio_em_lote"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("not_before", sa.DateTime(timezone=False), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("not_before")
