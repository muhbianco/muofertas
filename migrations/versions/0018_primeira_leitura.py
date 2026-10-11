"""primeira leitura não é mudança de preço

O coletor gravava a primeira leitura de um produto como `change` e marcava `last_changed_at`.
Isso dava urgência (U = 100 por 6 h, 70 até 24 h) a produto que acabou de ser descoberto, sem
preço nenhum ter mudado, e contava cada descoberta como "mudança de preço" no relatório.

- `price_observation.kind`: a primeira linha de cada produto passa a `first`.
- `catalog_product.last_changed_at`: some de quem nunca mudou de preço de verdade; os que
  ainda estavam dentro das 24 h da urgência voltam para a fila de avaliação.

Só dados, sem coluna nova. O worker antigo, entre a migração e a troca da imagem, ainda grava
`change` na primeira leitura: são poucas linhas e só pesam no relatório.

Revision ID: 0018_primeira_leitura
Revises: 0017_vendas
Create Date: 2026-10-11
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision = "0018_primeira_leitura"
down_revision = "0017_vendas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tabela derivada: o MySQL não aceita a própria tabela do UPDATE direto na subconsulta.
    op.execute(
        "UPDATE price_observation SET kind = 'first' WHERE kind = 'change' AND id IN ("
        " SELECT mid FROM (SELECT MIN(id) AS mid FROM price_observation GROUP BY product_id) f)"
    )
    # Tem data de mudança, mas nenhuma linha `change` depois da primeira leitura. Os que ainda
    # estavam nas 24 h da urgência voltam para a avaliação antes de perder a data.
    recent = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
    op.get_bind().execute(
        sa.text(
            "UPDATE catalog_product SET needs_evaluation = :yes"
            " WHERE last_changed_at >= :recent AND NOT EXISTS (SELECT 1 FROM price_observation po"
            " WHERE po.product_id = catalog_product.id AND po.kind = 'change')"
        ),
        {"yes": True, "recent": recent},
    )
    op.execute(
        "UPDATE catalog_product SET last_changed_at = NULL"
        " WHERE last_changed_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM price_observation po"
        " WHERE po.product_id = catalog_product.id AND po.kind = 'change')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE catalog_product SET last_changed_at = first_seen_at"
        " WHERE last_changed_at IS NULL AND last_observation_at IS NOT NULL"
    )
    op.execute("UPDATE price_observation SET kind = 'change' WHERE kind = 'first'")
