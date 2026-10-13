"""grupo "sempre marcado" na aprovação (decisão do dono em 13/10/2026)

Mudança de estratégia: produto de qualquer nicho vai a qualquer grupo, e o grupo Família é o que
mais rende. O grupo com `always_checked` vem marcado no "Grupos ▾" da aprovação e no link avulso
de todo produto, não só dos do nicho dele. Começa ligado no grupo do nicho pais-maes-filhos.

Expand + dado. O worker antigo ignora a coluna.

Revision ID: 0028_grupo_sempre_marcado
Revises: 0027_duzentos_por_dia
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_grupo_sempre_marcado"
down_revision = "0027_duzentos_por_dia"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("channel") as batch:
        batch.add_column(
            sa.Column("always_checked", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.execute(
        sa.text(
            "UPDATE channel SET always_checked = :on WHERE niche_id IN "
            "(SELECT id FROM niche WHERE slug = 'pais-maes-filhos')"
        ).bindparams(on=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("channel") as batch:
        batch.drop_column("always_checked")
