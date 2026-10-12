"""MercadoLíder Gold/Platinum no nicho que exige loja oficial (decisão do dono em 12/10/2026)

- `niche.accept_trusted`: o nicho que exige loja oficial passa a aceitar a oferta confiável (loja
  oficial ou MercadoLíder Gold/Platinum verde), a mesma régua da marca de risco;
- `niche.official_only_domains`: tipos de produto (JSON de domain_id) que seguem só loja oficial
  (celular, perfume).

Só acrescenta (expand). Os valores vêm do `nichos.json` no seed do deploy, que também manda os
produtos dos nichos com regra nova para a avaliação. O worker antigo ignora as colunas.

Revision ID: 0022_mercadolider
Revises: 0021_trinta_por_dia
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_mercadolider"
down_revision = "0021_trinta_por_dia"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("niche") as batch:
        batch.add_column(
            sa.Column("accept_trusted", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("official_only_domains", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("niche") as batch:
        batch.drop_column("official_only_domains")
        batch.drop_column("accept_trusted")
