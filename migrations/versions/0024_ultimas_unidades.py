"""últimas unidades informadas na aprovação (decisão do dono em 12/10/2026)

A API do app não traz estoque: `/products/{id}/items` não tem `available_quantity`, o vencedor do
produto vem vazio e `/items` dá 403 (testado em 12/10). O operador digita o "Últimas N unidades"
que vê na página, como o Pix; vale por 2 horas e o post diz "⏳ Últimas N unidades!".

Só acrescenta (expand). O worker antigo ignora as colunas.

Revision ID: 0024_ultimas_unidades
Revises: 0023_preco_loja_oficial
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_ultimas_unidades"
down_revision = "0023_preco_loja_oficial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("last_units", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("last_units_at", sa.DateTime(timezone=False), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("last_units_at")
        batch.drop_column("last_units")
