"""post de vendedor comum compara com a loja oficial (decisão do dono em 12/10/2026)

`publication.official_price_cents`: menor preço de loja oficial (novo) do produto, lido na mesma
revalidação que confere o preço da oferta antes do envio. Sem "de" no próprio anúncio, o post diz
"X% mais barato que a loja oficial" com esse preço riscado.

Só acrescenta (expand). O worker antigo não grava a coluna; o post dele sai como antes.

Revision ID: 0023_preco_loja_oficial
Revises: 0022_mercadolider
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_preco_loja_oficial"
down_revision = "0022_mercadolider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("official_price_cents", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("official_price_cents")
