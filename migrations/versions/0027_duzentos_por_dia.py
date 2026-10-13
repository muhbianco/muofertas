"""200 envios por dia em cada grupo (decisão do dono em 13/10/2026; eram 30, 40 no Família)

Na janela de 07h às 22h dá um envio a cada ~4,5 min, quando há fila. O intervalo gravado em
`min_interval_minutes` é o mesmo cálculo do painel (janela ÷ envios, piso de 4 min); o envio usa
o cálculo, a coluna é só o que a tela de Grupos mostra.

Só dados. O downgrade volta para 30.

Revision ID: 0027_duzentos_por_dia
Revises: 0026_vendedor_reputado
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_duzentos_por_dia"
down_revision = "0026_vendedor_reputado"
branch_labels = None
depends_on = None


def _set(per_day: int, floor: int) -> None:
    bind = op.get_bind()
    channels = bind.execute(
        sa.text("SELECT id, window_start_hour, window_end_hour FROM channel")
    ).all()
    for channel_id, start, end in channels:
        window = max(int(end) - int(start), 1) * 60
        n = min(per_day, window // floor)  # janela curta: o que cabe nela
        bind.execute(
            sa.text(
                "UPDATE channel SET max_per_day = :per_day, min_interval_minutes = :interval"
                " WHERE id = :id"
            ),
            {"per_day": n, "interval": max(window // n, floor), "id": channel_id},
        )


def upgrade() -> None:
    _set(200, 4)  # o piso de app/domain/distribution.py (migração não importa o app)


def downgrade() -> None:
    _set(30, 10)
