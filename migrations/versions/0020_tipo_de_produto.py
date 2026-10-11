"""tipo de produto (domínio do ML): nome para o painel e nicho fixo por tipo

A categoria do ML mistura coisas (Ciclismo traz compressor de pneu de carro). O `domain_id` do
produto de catálogo diz o que ele é. `marketplace_domain` guarda, por tipo:

- o nome em português (`/catalog_domains/{id}`), buscado aos poucos pelo worker;
- o nicho fixo, quando há regra (`nichos.json` ou painel). Sem regra, vale a categoria.

Só acrescenta (expand). O worker antigo não conhece a tabela.

Revision ID: 0020_tipo_de_produto
Revises: 0019_nicho_travado
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_tipo_de_produto"
down_revision = "0019_nicho_travado"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketplace_domain",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("marketplace", sa.String(20), nullable=False),
        sa.Column("domain_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("name_checked_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column(
            "niche_id",
            sa.Integer(),
            sa.ForeignKey("niche.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rule_source", sa.String(10), nullable=True),
        sa.Column("rule_actor", sa.String(40), nullable=True),
        sa.Column("rule_at", sa.DateTime(timezone=False), nullable=True),
        sa.UniqueConstraint("marketplace", "domain_id", name="uq_marketplace_domain"),
    )
    # O coletor procura produto por tipo (regra nova, seed).
    op.create_index("ix_catalog_product_domain", "catalog_product", ["domain_id"])


def downgrade() -> None:
    op.drop_index("ix_catalog_product_domain", table_name="catalog_product")
    op.drop_table("marketplace_domain")
