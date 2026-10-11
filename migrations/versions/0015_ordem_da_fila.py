"""ordem da fila de envio: o operador arrasta um post para cima e ele sai antes

`queue_position` vale só entre as publicações prontas do mesmo grupo. Vazio (o normal) = ordem
de aprovação; quem tem posição sai antes, na ordem dela. Só acrescenta (expand).

Revision ID: 0015_ordem_da_fila
Revises: 0014_convites
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_ordem_da_fila"
down_revision = "0014_convites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.add_column(sa.Column("queue_position", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication") as batch:
        batch.drop_column("queue_position")
