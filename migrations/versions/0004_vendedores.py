"""vendedores: cache de reputação e preço de oferta elegível/confiável na observação

Só acrescenta (expand). Antes desta revisão o menor preço vinha de qualquer vendedor, inclusive
sem reputação e com "entrega a combinar"; o programa de afiliados do ML só paga vendedor amarelo
ou verde. As linhas antigas ficam com os campos novos nulos e só entram na série da régua
"loja oficial" (o menor preço oficial já existia).

Revision ID: 0004_vendedores
Revises: 0003_painel
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_vendedores"
down_revision = "0003_painel"
branch_labels = None
depends_on = None

BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=False)


def upgrade() -> None:
    op.create_table(
        "marketplace_seller",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("marketplace", sa.String(20), nullable=False),
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column("nickname", sa.String(120), nullable=True),
        sa.Column("level_id", sa.String(16), nullable=True),
        sa.Column("power_seller_status", sa.String(16), nullable=True),
        sa.Column("transactions_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("checked_at", TS, nullable=False),
        sa.UniqueConstraint("marketplace", "external_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    with op.batch_alter_table("price_observation") as batch:
        batch.add_column(sa.Column("eligible_min_price_cents", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("eligible_item_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("eligible_seller_id", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("eligible_offers_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("trusted_min_price_cents", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("trusted_item_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("trusted_seller_id", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("eligible_official", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("trusted_official", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("official_item_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("official_seller_id", sa.BigInteger(), nullable=True))
    # Tudo que já tem preço volta a ser lido logo, já com o filtro de vendedor.
    op.execute("UPDATE catalog_product SET next_check_at = last_checked_at WHERE active = 1")


def downgrade() -> None:
    with op.batch_alter_table("price_observation") as batch:
        for col in (
            "official_seller_id",
            "official_item_id",
            "trusted_official",
            "eligible_official",
            "trusted_seller_id",
            "trusted_item_id",
            "trusted_min_price_cents",
            "eligible_offers_count",
            "eligible_seller_id",
            "eligible_item_id",
            "eligible_min_price_cents",
        ):
            batch.drop_column(col)
    op.drop_table("marketplace_seller")
