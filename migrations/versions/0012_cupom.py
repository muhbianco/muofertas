"""cupom: preço com o cupom da loja informado na aprovação

A API do ML não traz cupom de vendedor (a página mostra "R$ X com Cupom"). Como o Pix, o operador
digita o que a página mostra; vale por 3 horas. Só acrescenta (expand).

Revision ID: 0012_cupom
Revises: 0011_preco_riscado
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_cupom"
down_revision = "0011_preco_riscado"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("coupon_price_cents", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("coupon_at", sa.DateTime(timezone=False), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("coupon_at")
        batch.drop_column("coupon_price_cents")
