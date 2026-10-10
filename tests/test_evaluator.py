from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.evaluation.service import Evaluator
from app.models import CatalogProduct, Niche, OfferEvaluation, PriceObservation


async def seed(
    sessions: async_sessionmaker[AsyncSession], *, history: list[tuple[float, int, int | None]]
) -> int:
    """history: (dias atrás, menor preço, menor preço oficial)."""
    now = utcnow()
    async with sessions() as session, session.begin():
        niche = Niche(
            slug="ferramentas",
            name="Ferramentas",
            active=True,
            created_at=now,
            commission_rate_bp=1200,
            require_official=False,
            high_risk_brands=json.dumps(["Makita"]),
        )
        session.add(niche)
        await session.flush()
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLBP1",
            niche_id=niche.id,
            name="Serra Mármore Makita 4100NH3",
            brand="Makita",
            discovered_via="highlights",
            active=True,
            tier="hot",
            next_check_at=now,
            last_checked_at=now - timedelta(minutes=5),
            last_changed_at=now - timedelta(hours=1),
            last_observation_at=now - timedelta(minutes=5),
            consecutive_failures=0,
            first_seen_at=now - timedelta(days=40),
            best_highlight_position=1,
            needs_evaluation=True,
        )
        session.add(product)
        await session.flush()
        for days_ago, price, official in history:
            session.add(
                PriceObservation(
                    product_id=product.id,
                    observed_at=now - timedelta(days=days_ago),
                    kind="change",
                    currency="BRL",
                    min_price_cents=price,
                    min_official_price_cents=official,
                    offers_count=10,
                    official_offers_count=1 if official else 0,
                    deal_offers_count=0,
                    free_shipping_offers_count=5,
                    min_offer_item_id="MLB1",
                    min_offer_seller_id=1,
                    min_offer_original_price_cents=None,
                    min_offer_free_shipping=True,
                )
            )
        return product.id


async def test_real_official_drop_is_recorded_and_flag_cleared(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    product_id = await seed(sessions, history=[(30, 45000, 45000), (0.0035, 30000, 30000)])
    assert await Evaluator(sessions).evaluate_due() == 1
    async with sessions() as session:
        ev = (await session.execute(select(OfferEvaluation))).scalar_one()
        product = await session.get(CatalogProduct, product_id)
    assert product is not None
    assert ev.trigger == "price_change"
    assert ev.tier in ("excepcional", "excelente")
    assert ev.used_official
    assert ev.real_discount_bp == 3333
    snapshot = json.loads(ev.snapshot)
    assert snapshot["ruleset"].startswith("v1") and "components" in snapshot
    assert product.needs_evaluation is False
    assert product.last_tier == ev.tier
    # já avaliado e sem mudança: nada a fazer
    assert await Evaluator(sessions).evaluate_due() == 0


async def test_high_risk_brand_without_official_is_blocked(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed(sessions, history=[(30, 45000, None), (0.0035, 20000, None)])
    await Evaluator(sessions).evaluate_due()
    async with sessions() as session:
        ev = (await session.execute(select(OfferEvaluation))).scalar_one()
    assert ev.tier == "bloqueada"
    assert "G7_sem_loja_oficial" in json.loads(ev.snapshot)["gates_failed"]
