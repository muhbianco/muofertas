"""pix: preço do Pix informado pelo operador na publicação

A API do ML não traz o desconto do Pix (sondagem de 10/10/2026); o operador digita o que a página
mostra na hora de postar. Só acrescenta (expand).

Revision ID: 0006_pix
Revises: 0005_publicacao
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_pix"
down_revision = "0005_publicacao"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("pix_price_cents", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("pix_price_cents")
