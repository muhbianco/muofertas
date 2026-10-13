"""régua "reputado" nos nichos de loja oficial (decisão do dono em 13/10/2026)

Beleza, Tecnologia, Esportes e Pais passam a aceitar vendedor verde com 500+ vendas e envio pelo
ML, além da loja oficial e do MercadoLíder Gold/Platinum (perfume, celular e suplemento seguem só
loja oficial; marca de risco segue confiável). Caso que motivou: máscara L'Oréal a R$ 249,99 na
loja oficial e a R$ 81,61 de vendedor verde com 1.733 vendas.

O histórico guarda o menor preço de cada régua; esta ganha colunas próprias. Linha antiga fica com
`reputable_known` falso e fora da série dessa régua (o preço dela seria o de outra régua, mais
alto, e daria desconto falso). O coletor completa o dado como batimento, sem fingir mudança.

Só acrescenta (expand). O worker antigo ignora as colunas.

Revision ID: 0026_vendedor_reputado
Revises: 0025_link_avulso
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_vendedor_reputado"
down_revision = "0025_link_avulso"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("price_observation") as batch:
        batch.add_column(sa.Column("reputable_min_price_cents", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("reputable_item_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("reputable_seller_id", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("reputable_official", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("reputable_original_cents", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("reputable_known", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("price_observation") as batch:
        batch.drop_column("reputable_known")
        batch.drop_column("reputable_original_cents")
        batch.drop_column("reputable_official")
        batch.drop_column("reputable_seller_id")
        batch.drop_column("reputable_item_id")
        batch.drop_column("reputable_min_price_cents")
