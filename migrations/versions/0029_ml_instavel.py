"""robustez contra o ML instável e camada quente por queda (14/10/2026)

Das 13:00 às 13:51 UTC de 14/10 o ML respondeu 400 e 404 falsos em /products/{id}/items: o coletor
desativou 230 produtos no 1º 404 e o envio tirou 6 posts da fila. E a camada quente durava 48 h
desde qualquer mudança de preço, que acontece quase todo dia: 579 quentes, metade da demanda de
chamadas, colada no teto.

- `publication.retry_after`: o 1º 404 na conferência adia o post; o 2º tira da fila.
- `catalog_product.hot_since`: quente dura 12 h desde a última queda relevante. Os quentes de
  agora começam pela última mudança (`last_changed_at`), então os parados há mais de 12 h esfriam
  na próxima leitura.

Expand + dado. O worker antigo ignora as colunas.

Revision ID: 0029_ml_instavel
Revises: 0028_grupo_sempre_marcado
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_ml_instavel"
down_revision = "0028_grupo_sempre_marcado"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("retry_after", sa.DateTime(timezone=False), nullable=True))
    with op.batch_alter_table("catalog_product") as batch:
        batch.add_column(sa.Column("hot_since", sa.DateTime(timezone=False), nullable=True))
    op.execute(
        sa.text(
            "UPDATE catalog_product SET hot_since = COALESCE(last_changed_at, first_seen_at) "
            "WHERE tier = 'hot'"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("catalog_product") as batch:
        batch.drop_column("hot_since")
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("retry_after")
