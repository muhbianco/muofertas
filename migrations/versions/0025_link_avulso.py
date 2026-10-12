"""link avulso com oferta relâmpago (pedido do dono em 12/10/2026)

O operador cola o link de um anúncio que o robô não acompanha, digita o que a página mostra e o
post entra em 1º na fila dos grupos escolhidos (nível `avulso`). Na oferta relâmpago ele digita o
relógio da página ("Encerra em 07:10:38"); guardamos a hora em que ela acaba: o post diz até
quando vale e não sai depois disso.

Só acrescenta (expand). O worker antigo ignora a coluna.

Revision ID: 0025_link_avulso
Revises: 0024_ultimas_unidades
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_link_avulso"
down_revision = "0024_ultimas_unidades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("flash_ends_at", sa.DateTime(timezone=False), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("flash_ends_at")
