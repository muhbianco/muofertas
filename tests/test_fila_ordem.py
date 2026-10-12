from __future__ import annotations

import json

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import CatalogProduct, OfferEvaluation
from tests.test_panel import login, seed_admin, seed_product


async def test_discount_order_weighs_best_seller_position(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    first_id = await seed_product(sessions)
    now = utcnow()
    async with sessions() as s, s.begin():
        first = await s.get(CatalogProduct, first_id)
        assert first is not None
        niche_id = first.niche_id
        for n, (name, pct, position) in enumerate(
            [
                ("Kit 10 potes", 44, 1),  # 44 x 2,0 = 88
                ("Pote qualquer", 70, 20),  # 70 x 1,05 = 73,5
                ("Pote vigiado", 53, None),  # sem posição: 53
            ]
        ):
            p = CatalogProduct(
                marketplace="mercadolivre",
                external_id=f"MLBO{n}",
                niche_id=niche_id,
                name=name,
                discovered_via="highlights" if position else "watchlist",
                active=True,
                tier="warm",
                next_check_at=now,
                consecutive_failures=0,
                first_seen_at=now,
                last_tier="regular",
                last_score=40.0,
                listing_discount_pct=pct,
                best_highlight_position=position,
            )
            s.add(p)
            await s.flush()
            s.add(
                OfferEvaluation(
                    product_id=p.id,
                    evaluated_at=now,
                    trigger="daily",
                    ruleset="v2",
                    tier="regular",
                    score=40.0,
                    price_cents=9990,
                    used_official=True,
                    real_discount_bp=None,
                    reference_cents=None,
                    snapshot=json.dumps({"offer_kind": "oficial", "offer_original_cents": 19990}),
                )
            )
    assert (await login(client, secret)).status_code == 303

    page = (await client.get("/?nivel=off")).text
    order = [page.index(name) for name in ("Kit 10 potes", "Pote qualquer", "Pote vigiado")]
    assert order == sorted(order)
    assert '<span class="selo top">1º mais vendido</span>' in page
