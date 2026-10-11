"""convite da manhã: dois convites por grupo por dia (07h, no primeiro horário, e 19h)

`slot` (`manha` | `noite`) entra na trava única: grupo + dia + horário. As linhas que já existem
são da noite (o default do servidor). O índice novo nasce antes de o antigo cair: o antigo também
serve à chave estrangeira de `channel_id` no MariaDB.

Compatível com o código antigo durante o deploy: ele grava sem `slot` (vira `noite`) e continua
com no máximo um convite da noite por dia.

Revision ID: 0016_convite_manha
Revises: 0015_ordem_da_fila
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_convite_manha"
down_revision = "0015_ordem_da_fila"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("channel_invite") as batch:
        batch.add_column(sa.Column("slot", sa.String(10), nullable=False, server_default="noite"))
        batch.create_unique_constraint(
            "uq_channel_invite_slot", ["channel_id", "local_date", "slot"]
        )
        batch.drop_constraint("uq_channel_invite_day", type_="unique")


def downgrade() -> None:
    op.execute("DELETE FROM channel_invite WHERE slot = 'manha'")
    with op.batch_alter_table("channel_invite") as batch:
        batch.create_unique_constraint("uq_channel_invite_day", ["channel_id", "local_date"])
        batch.drop_constraint("uq_channel_invite_slot", type_="unique")
        batch.drop_column("slot")
