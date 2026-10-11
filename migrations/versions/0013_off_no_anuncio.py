"""% OFF no anúncio: o "de" da oferta de cada régua e o selo na fila

- `price_observation`: o preço riscado da oferta elegível, da confiável e da loja oficial. Antes
  só havia o da oferta mais barata de qualquer vendedor, que nem sempre é a que o card mostra.
  `originals_known` marca as linhas antigas (falso): o coletor completa o "de" na próxima leitura
  sem fingir mudança de preço.
- `catalog_product.listing_discount_pct`: o % OFF da última avaliação, com as travas do post
  (5% a 70%, "de" até 1,5 x a referência). Ordena e filtra a fila.

Só acrescenta (expand). O worker antigo, entre a migração e a troca da imagem, grava linhas com
`originals_known` falso pelo default do servidor: certo, ele não conhece o "de" por régua.

Revision ID: 0013_off_no_anuncio
Revises: 0012_cupom
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_off_no_anuncio"
down_revision = "0012_cupom"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("price_observation") as batch:
        batch.add_column(sa.Column("eligible_original_cents", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("trusted_original_cents", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("official_original_cents", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("originals_known", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("catalog_product") as batch:
        batch.add_column(sa.Column("listing_discount_pct", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("catalog_product") as batch:
        batch.drop_column("listing_discount_pct")
    with op.batch_alter_table("price_observation") as batch:
        batch.drop_column("originals_known")
        batch.drop_column("official_original_cents")
        batch.drop_column("trusted_original_cents")
        batch.drop_column("eligible_original_cents")
