"""marca ✓/✕ da fila visível entre operadores (pedido do dono em 15/10/2026)

Com dois operadores na fila (muhbianco e danilo.correa), um não via o que o outro estava
aprovando: as marcas ficavam só no navegador. `approval_claim` guarda quem marcou cada produto;
a tela dos outros mostra "em aprovação por X" e não deixa marcar o mesmo produto.

Só tabela nova (expand). O código antigo não a lê.

Revision ID: 0030_marca_entre_operadores
Revises: 0029_ml_instavel
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_marca_entre_operadores"
down_revision = "0029_ml_instavel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_claim",
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("catalog_product.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("actor", sa.String(40), nullable=False),
        sa.Column("mark", sa.String(12), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=False), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("approval_claim")
