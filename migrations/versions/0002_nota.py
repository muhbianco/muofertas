"""nota: regras por nicho, marcação de reavaliação e avaliações imutáveis

Só acrescenta (expand): colunas novas têm default e a tabela é nova. O código anterior roda neste
schema, então o rollback de imagem não precisa de downgrade.

Revision ID: 0002_nota
Revises: 0001_coletor
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_nota"
down_revision = "0001_coletor"
branch_labels = None
depends_on = None

BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=False)


def upgrade() -> None:
    with op.batch_alter_table("niche") as batch:
        batch.add_column(
            sa.Column("commission_rate_bp", sa.Integer(), nullable=False, server_default="1200")
        )
        batch.add_column(
            sa.Column("require_official", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("high_risk_brands", sa.Text(), nullable=True))
    with op.batch_alter_table("catalog_product") as batch:
        batch.add_column(
            sa.Column("needs_evaluation", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("last_evaluated_at", TS, nullable=True))
        batch.add_column(sa.Column("last_tier", sa.String(12), nullable=True))
        batch.add_column(sa.Column("last_score", sa.Numeric(5, 1), nullable=True))
    op.create_index(
        "ix_catalog_product_needs_eval", "catalog_product", ["needs_evaluation", "active"]
    )
    op.create_table(
        "offer_evaluation",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column(
            "product_id",
            BIGID,
            sa.ForeignKey("catalog_product.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evaluated_at", TS, nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("ruleset", sa.String(32), nullable=False),
        sa.Column("tier", sa.String(12), nullable=False),
        sa.Column("score", sa.Numeric(5, 1), nullable=False),
        sa.Column("price_cents", sa.BigInteger(), nullable=True),
        sa.Column("used_official", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("real_discount_bp", sa.Integer(), nullable=True),
        sa.Column("reference_cents", sa.BigInteger(), nullable=True),
        sa.Column("snapshot", sa.Text(), nullable=False),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_offer_evaluation_product_time", "offer_evaluation", ["product_id", "evaluated_at"]
    )
    op.create_index("ix_offer_evaluation_tier_time", "offer_evaluation", ["tier", "evaluated_at"])
    # Tudo que já tem preço entra na primeira avaliação.
    op.execute(
        "UPDATE catalog_product SET needs_evaluation = 1 WHERE last_observation_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("offer_evaluation")  # leva os índices junto (MariaDB recusa índice de FK antes)
    op.drop_index("ix_catalog_product_needs_eval", table_name="catalog_product")
    with op.batch_alter_table("catalog_product") as batch:
        batch.drop_column("last_score")
        batch.drop_column("last_tier")
        batch.drop_column("last_evaluated_at")
        batch.drop_column("needs_evaluation")
    with op.batch_alter_table("niche") as batch:
        batch.drop_column("high_risk_brands")
        batch.drop_column("require_official")
        batch.drop_column("commission_rate_bp")
