"""coletor: nichos, produtos de catálogo, histórico de preço, execuções, erros e lease

Revision ID: 0001_coletor
Revises:
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_coletor"
down_revision = None
branch_labels = None
depends_on = None

BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=False)


def upgrade() -> None:
    op.create_table(
        "niche",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", TS, nullable=False),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_table(
        "niche_category",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "niche_id", sa.Integer(), sa.ForeignKey("niche.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("marketplace", sa.String(20), nullable=False),
        sa.Column("external_category_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_discovery_at", TS, nullable=True),
        sa.UniqueConstraint("niche_id", "marketplace", "external_category_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_table(
        "catalog_product",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("marketplace", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(32), nullable=False),
        sa.Column(
            "niche_id", sa.Integer(), sa.ForeignKey("niche.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("category_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("brand", sa.String(80), nullable=True),
        sa.Column("domain_id", sa.String(80), nullable=True),
        sa.Column("permalink", sa.String(512), nullable=True),
        sa.Column("image_url", sa.String(512), nullable=True),
        sa.Column("discovered_via", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tier", sa.String(8), nullable=False),
        sa.Column("next_check_at", TS, nullable=False),
        sa.Column("last_checked_at", TS, nullable=True),
        sa.Column("last_changed_at", TS, nullable=True),
        sa.Column("last_observation_at", TS, nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_in_highlights_at", TS, nullable=True),
        sa.Column("best_highlight_position", sa.Integer(), nullable=True),
        sa.UniqueConstraint("marketplace", "external_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_catalog_product_due", "catalog_product", ["active", "next_check_at"])
    op.create_table(
        "price_observation",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column(
            "product_id",
            BIGID,
            sa.ForeignKey("catalog_product.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("min_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("min_official_price_cents", sa.BigInteger(), nullable=True),
        sa.Column("offers_count", sa.Integer(), nullable=False),
        sa.Column("official_offers_count", sa.Integer(), nullable=False),
        sa.Column("deal_offers_count", sa.Integer(), nullable=False),
        sa.Column("free_shipping_offers_count", sa.Integer(), nullable=False),
        sa.Column("min_offer_item_id", sa.String(32), nullable=False),
        sa.Column("min_offer_seller_id", sa.BigInteger(), nullable=True),
        sa.Column("min_offer_original_price_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "min_offer_free_shipping", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_price_observation_product_time", "price_observation", ["product_id", "observed_at"]
    )
    op.create_table(
        "collection_run",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(255), nullable=True),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_table(
        "integration_error",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column(
            "run_id", BIGID, sa.ForeignKey("collection_run.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("path", sa.String(255), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_integration_error_time", "integration_error", ["occurred_at"])
    op.create_table(
        "worker_lease",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("holder", sa.String(80), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )


def downgrade() -> None:
    op.drop_table("worker_lease")
    op.drop_index("ix_integration_error_time", table_name="integration_error")
    op.drop_table("integration_error")
    op.drop_table("collection_run")
    op.drop_index("ix_price_observation_product_time", table_name="price_observation")
    op.drop_table("price_observation")
    op.drop_index("ix_catalog_product_due", table_name="catalog_product")
    op.drop_table("catalog_product")
    op.drop_table("niche_category")
    op.drop_table("niche")
