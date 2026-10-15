"""marca ✓/✕ da fila visível entre operadores (pedido do dono em 15/10/2026)

Com dois operadores na fila (muhbianco e danilo.correa), um não via o que o outro estava
aprovando: as marcas ficavam só no navegador. `approval_claim` guarda quem marcou cada produto;
a tela dos outros mostra "em aprovação por X" e não deixa marcar o mesmo produto.

Só tabela nova (expand). O código antigo não a lê. `product_id` é BIGINT como o `id` de
`catalog_product`: com INTEGER o MariaDB recusa a chave estrangeira (errno 150).

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

BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TABLE_OPTS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "approval_claim",
        sa.Column(
            "product_id",
            BIGID,
            sa.ForeignKey("catalog_product.id", ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("actor", sa.String(40), nullable=False),
        sa.Column("mark", sa.String(12), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=False), nullable=False),
        **TABLE_OPTS,
    )


def downgrade() -> None:
    op.drop_table("approval_claim")
