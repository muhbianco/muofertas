"""30 envios por dia em cada grupo (decisão do dono em 12/10/2026; eram 20)

Na janela de 07h às 22h dá um envio a cada 30 min. O intervalo gravado em
`min_interval_minutes` é o mesmo cálculo do painel (janela ÷ envios, mínimo de 10 min); o envio
usa o cálculo, a coluna é só o que a tela de Grupos mostra.

Só dados. O downgrade volta para 20.

Revision ID: 0021_trinta_por_dia
Revises: 0020_tipo_de_produto
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_trinta_por_dia"
down_revision = "0020_tipo_de_produto"
branch_labels = None
depends_on = None

MIN_SPACING_MINUTES = 10  # o mesmo de app/domain/distribution.py (migração não importa o app)


def _set(per_day: int) -> None:
    bind = op.get_bind()
    channels = bind.execute(
        sa.text("SELECT id, window_start_hour, window_end_hour FROM channel")
    ).all()
    for channel_id, start, end in channels:
        window = max(int(end) - int(start), 1) * 60
        n = min(per_day, window // MIN_SPACING_MINUTES)  # janela curta: o que cabe nela
        bind.execute(
            sa.text(
                "UPDATE channel SET max_per_day = :per_day, min_interval_minutes = :interval"
                " WHERE id = :id"
            ),
            {
                "per_day": n,
                "interval": max(window // n, MIN_SPACING_MINUTES),
                "id": channel_id,
            },
        )


def upgrade() -> None:
    _set(30)


def downgrade() -> None:
    _set(20)
