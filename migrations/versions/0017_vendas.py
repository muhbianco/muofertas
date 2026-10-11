"""vendas do afiliado: o worker confere a lista de vendas do painel a cada 3 h

Uma linha por checagem: total de vendas dos últimos 30 dias e só os NOMES dos campos de uma venda
(para montar a atribuição por etiqueta; nenhum valor de venda ou de comprador). Quando o total
sobe, sai e-mail. Só acrescenta (expand).

Revision ID: 0017_vendas
Revises: 0016_convite_manha
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_vendas"
down_revision = "0016_convite_manha"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_check",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("total_results", sa.Integer(), nullable=True),
        sa.Column("item_keys", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.create_index("ix_sales_check_checked_at", "sales_check", ["checked_at"])


def downgrade() -> None:
    op.drop_index("ix_sales_check_checked_at", table_name="sales_check")
    op.drop_table("sales_check")
