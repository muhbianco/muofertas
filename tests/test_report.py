from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import (
    CatalogProduct,
    Channel,
    ChannelStat,
    Niche,
    OfferEvaluation,
    OperatorDecision,
    PriceObservation,
    Publication,
    SalesCheck,
)
from app.panel.report import niche_report, sales_status
from app.whatsapp.service import local_day
from tests.test_panel import login, seed_admin


async def seed(sessions: async_sessionmaker[AsyncSession]) -> int:
    now = utcnow()
    async with sessions() as s, s.begin():
        niche = Niche(
            slug="ferramentas",
            name="Ferramentas",
            active=True,
            created_at=now,
            commission_rate_bp=1200,
        )
        s.add(niche)
        await s.flush()
        channel = Channel(niche_id=niche.id, name="Ferramentas", transport="wuzapi", created_at=now)
        s.add(channel)
        await s.flush()
        products = []
        for n, (tier, off, price, real_bp) in enumerate(
            [
                ("boa", 30, 10000, None),
                ("regular", None, 20000, 1500),
                ("regular", 10, 30000, 2500),
                ("bloqueada", 40, 5000, None),
            ]
        ):
            p = CatalogProduct(
                marketplace="mercadolivre",
                external_id=f"MLB{n}",
                niche_id=niche.id,
                name=f"P{n}",
                discovered_via="highlights",
                active=True,
                tier="warm",
                next_check_at=now,
                consecutive_failures=0,
                first_seen_at=now,
                last_tier=tier,
                listing_discount_pct=off,
            )
            s.add(p)
            await s.flush()
            products.append(p)
            s.add(
                OfferEvaluation(
                    product_id=p.id,
                    evaluated_at=now - timedelta(hours=1),
                    trigger="daily",
                    ruleset="v2",
                    tier=tier,
                    score=50.0,
                    price_cents=price,
                    used_official=False,
                    real_discount_bp=real_bp,
                    snapshot="{}",
                )
            )
        for days_ago in (1, 2, 3):  # 14 mudanças em 7 dias = 2 por dia
            for _ in range(4 if days_ago < 3 else 6):
                s.add(
                    PriceObservation(
                        product_id=products[0].id,
                        observed_at=now - timedelta(days=days_ago),
                        kind="change",
                        currency="BRL",
                        min_price_cents=1,
                        offers_count=1,
                        official_offers_count=0,
                        deal_offers_count=0,
                        free_shipping_offers_count=0,
                        min_offer_item_id="MLBI",
                    )
                )
        s.add_all(
            [
                OperatorDecision(
                    product_id=products[0].id,
                    decision="approved",
                    actor="dono",
                    decided_at=now - timedelta(days=1),
                ),
                OperatorDecision(
                    product_id=products[1].id,
                    decision="rejected",
                    actor="dono",
                    decided_at=now - timedelta(days=1),
                ),
                OperatorDecision(
                    product_id=products[2].id,
                    decision="rejected",
                    actor="dono",
                    decided_at=now - timedelta(days=30),
                ),  # fora da janela
            ]
        )
        s.add(
            Publication(
                channel_id=channel.id,
                product_id=products[0].id,
                idempotency_key="k",
                dedup_key="d",
                state="sent",
                tier="boa",
                target_url="https://x",
                approved_price_cents=10000,
                created_at=now - timedelta(days=1),
                sent_at=now - timedelta(days=1),
            )
        )
        today = local_day(now)
        s.add_all(
            [
                ChannelStat(
                    channel_id=channel.id, day=today, members=30, admins=1, captured_at=now
                ),
                ChannelStat(
                    channel_id=channel.id,
                    day=today - timedelta(days=7),
                    members=12,
                    admins=1,
                    captured_at=now,
                ),
            ]
        )
        return niche.id


async def test_report_numbers_per_niche(sessions: async_sessionmaker[AsyncSession]) -> None:
    await seed(sessions)
    async with sessions() as db:
        (r,) = await niche_report(db, utcnow())
    assert (r.products, r.good, r.with_off, r.blocked, r.blocked_pct) == (4, 1, 2, 1, 25)
    assert r.price_changes_per_day == 2.0
    assert r.median_real_discount_pct == 20.0  # mediana de 15% e 25%
    assert r.median_ticket_cents == 20000  # boa + regular, sem o bloqueado
    assert r.commission_per_order_cents == 2400  # 12%
    assert (r.approved, r.rejected, r.posts_sent) == (1, 1, 1)
    assert (r.members, r.members_delta) == (30, 18)


async def test_nichos_page_shows_report_and_sales(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    await seed(sessions)
    async with sessions() as s, s.begin():
        s.add(SalesCheck(checked_at=utcnow(), status="ok", total_results=2))
    async with sessions() as db:
        status = await sales_status(db)
    assert (status.total, status.problem) == (2, None)
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/nichos")).text
    assert "Relatório (últimos 7 dias)" in page
    assert "Comissão por pedido" in page and "R$ 24,00" in page
    assert "Vendas pelo afiliado (30 dias):" in page and "<b>2</b>" in page
