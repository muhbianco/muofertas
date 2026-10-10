"""preço riscado: o "de" do anúncio na publicação (para o "% OFF" do post, com travas)

Só acrescenta (expand).

Revision ID: 0011_preco_riscado
Revises: 0010_convites
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_preco_riscado"
down_revision = "0010_convites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("original_price_cents", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("original_price_cents")
