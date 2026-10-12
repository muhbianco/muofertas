from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.maintenance import retention as retention_module
from app.maintenance.retention import Retention
from app.models import (
    CatalogProduct,
    CollectionRun,
    IntegrationError,
    Niche,
    OfferEvaluation,
    OperatorDecision,
    PriceObservation,
)

# 07h de Brasília: já passou das 4h
NOW = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


def _eval(product_id: int, days_ago: int) -> OfferEvaluation:
    return OfferEvaluation(
        product_id=product_id,
        evaluated_at=NOW - timedelta(days=days_ago),
        trigger="daily",
        ruleset="v2",
        tier="regular",
        score=40.0,
        price_cents=1000,
        used_official=False,
        snapshot="{}",
    )


async def test_daily_cleanup_keeps_what_matters(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention_module, "BATCH", 2)  # força vários lotes
    async with sessions() as s, s.begin():
        niche = Niche(slug="f", name="F", active=True, created_at=NOW)
        s.add(niche)
        await s.flush()
        products = []
        for n in range(2):
            p = CatalogProduct(
                marketplace="mercadolivre",
                external_id=f"MLB{n}",
                niche_id=niche.id,
                discovered_via="highlights",
                active=True,
                tier="warm",
                next_check_at=NOW,
                consecutive_failures=0,
                first_seen_at=NOW - timedelta(days=200),
            )
            s.add(p)
            products.append(p)
        await s.flush()
        a, b = products
        # A: três velhas (uma citada por decisão) e uma recente. B: só uma velha, que é a última.
        old = [_eval(a.id, 120), _eval(a.id, 110), _eval(a.id, 100)]
        s.add_all([*old, _eval(a.id, 1), _eval(b.id, 150)])
        await s.flush()
        s.add(
            OperatorDecision(
                product_id=a.id,
                evaluation_id=old[1].id,
                decision="approved",
                actor="dono",
                decided_at=NOW - timedelta(days=110),
            )
        )
        for days in (40, 35, 31, 5):
            s.add(
                CollectionRun(
                    source="ml_refresh",
                    started_at=NOW - timedelta(days=days),
                    status="ok",
                )
            )
            s.add(IntegrationError(source="ml_refresh", occurred_at=NOW - timedelta(days=days)))
        s.add(
            PriceObservation(
                product_id=a.id,
                observed_at=NOW - timedelta(days=200),
                kind="first",
                currency="BRL",
                min_price_cents=1,
                offers_count=1,
                official_offers_count=0,
                deal_offers_count=0,
                free_shipping_offers_count=0,
                min_offer_item_id="X",
            )
        )

    job = Retention(sessions, Settings(database_url_override="sqlite+aiosqlite://"))
    assert await job.run(NOW - timedelta(hours=4)) is None  # 03h em Brasília: antes das 4h
    cleaned = await job.run(NOW)
    assert cleaned is not None
    assert (cleaned.runs, cleaned.errors, cleaned.evaluations) == (3, 3, 2)
    assert await job.run(NOW + timedelta(hours=2)) is None  # uma vez por dia

    async with sessions() as s:
        evals = sorted(
            (e.product_id, (NOW - e.evaluated_at).days)
            for e in (await s.execute(select(OfferEvaluation))).scalars()
        )
        runs = len((await s.execute(select(CollectionRun))).scalars().all())
        history = len((await s.execute(select(PriceObservation))).scalars().all())
    # Ficam: a citada pela decisão (110), a recente de A (1) e a única de B (150).
    assert evals == sorted([(a.id, 110), (a.id, 1), (b.id, 150)])
    assert runs == 1 and history == 1
