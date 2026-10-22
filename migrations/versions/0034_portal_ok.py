"""último acerto do gerador de links do ML (pedido do dono em 22/10/2026)

O alerta "Sessão do ML no servidor expirou" ficava aberto 3 h depois da última recusa, mesmo com a
sessão já renovada e os links saindo curtos. `portal_status` guarda o último acerto; o alerta só
conta as recusas depois dele.

Só tabela nova (expand). O código antigo não a lê.

Revision ID: 0034_portal_ok
Revises: 0033_boas_vindas
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_portal_ok"
down_revision = "0033_boas_vindas"
branch_labels = None
depends_on = None

TABLE_OPTS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "portal_status",
        sa.Column("source", sa.String(20), primary_key=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=False), nullable=False),
        **TABLE_OPTS,
    )


def downgrade() -> None:
    op.drop_table("portal_status")
