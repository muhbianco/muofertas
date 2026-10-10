"""publicação: grupos por nicho e publicações (Fase 2a, envio manual)

Só acrescenta (expand). O código anterior roda neste schema.

Revision ID: 0005_publicacao
Revises: 0004_vendedores
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_publicacao"
down_revision = "0004_vendedores"
branch_labels = None
depends_on = None

BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=False)
TABLE_OPTS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "channel",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("niche_id", sa.Integer(), sa.ForeignKey("niche.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("transport", sa.String(12), nullable=False, server_default="manual"),
        sa.Column("ml_tag", sa.String(30), nullable=True),
        sa.Column("invite_url", sa.String(255), nullable=True),
        sa.Column("max_per_day", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("min_interval_minutes", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("window_start_hour", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("window_end_hour", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False),
        **TABLE_OPTS,
    )
    op.create_table(
        "publication",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channel.id"), nullable=False),
        sa.Column("product_id", BIGID, sa.ForeignKey("catalog_product.id"), nullable=False),
        sa.Column(
            "evaluation_id",
            BIGID,
            sa.ForeignKey("offer_evaluation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decision_id",
            BIGID,
            sa.ForeignKey("operator_decision.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("tier", sa.String(12), nullable=False),
        sa.Column("item_id", sa.String(32), nullable=True),
        sa.Column("target_url", sa.String(512), nullable=False),
        sa.Column("affiliate_url", sa.String(512), nullable=True),
        sa.Column("approved_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("checked_price_cents", sa.BigInteger(), nullable=True),
        sa.Column("checked_at", TS, nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("link_at", TS, nullable=True),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("actor", sa.String(40), nullable=True),
        **TABLE_OPTS,
    )
    op.create_index(
        "ix_publication_channel_dedup", "publication", ["channel_id", "dedup_key", "created_at"]
    )
    op.create_index("ix_publication_state", "publication", ["state", "created_at"])
    op.create_index("ix_publication_channel_sent", "publication", ["channel_id", "sent_at"])


def downgrade() -> None:
    # MariaDB não solta índice que sustenta FK antes da tabela (erro 1553): tabela direto.
    op.drop_table("publication")
    op.drop_table("channel")
