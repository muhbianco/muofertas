"""nicho escolhido pelo operador: o seed das categorias não move mais o produto

`catalog_product.niche_locked`: o operador trocou o nicho no painel (ou escolheu ao vigiar).
O `seed-niches`, que leva os produtos junto quando uma categoria muda de nicho, pula esses.

Só acrescenta (expand). O worker antigo ignora a coluna: o default do servidor é falso.

Revision ID: 0019_nicho_travado
Revises: 0018_primeira_leitura
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_nicho_travado"
down_revision = "0018_primeira_leitura"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("catalog_product") as batch:
        batch.add_column(
            sa.Column("niche_locked", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("catalog_product") as batch:
        batch.drop_column("niche_locked")
