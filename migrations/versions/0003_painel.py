"""painel: operador, sessão, decisões do operador e bloqueio de produto

Só acrescenta (expand). O código anterior roda neste schema.

Revision ID: 0003_painel
Revises: 0002_nota
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_painel"
down_revision = "0002_nota"
branch_labels = None
depends_on = None

BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=False)
TABLE_OPTS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "admin_user",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(40), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("totp_secret", sa.String(64), nullable=False),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_login_at", TS, nullable=True),
        **TABLE_OPTS,
    )
    op.create_table(
        "admin_session",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("admin_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        **TABLE_OPTS,
    )
    op.create_table(
        "operator_decision",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column(
            "product_id",
            BIGID,
            sa.ForeignKey("catalog_product.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evaluation_id",
            BIGID,
            sa.ForeignKey("offer_evaluation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("actor", sa.String(40), nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        **TABLE_OPTS,
    )
    op.create_index(
        "ix_operator_decision_product_time", "operator_decision", ["product_id", "decided_at"]
    )
    with op.batch_alter_table("catalog_product") as batch:
        batch.add_column(
            sa.Column("operator_blocked", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("catalog_product") as batch:
        batch.drop_column("operator_blocked")
    op.drop_table("operator_decision")  # leva os índices junto
    op.drop_table("admin_session")
    op.drop_table("admin_user")
