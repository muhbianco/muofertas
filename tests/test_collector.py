from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collector.service import Collector
from app.core.clock import utcnow
from app.core.config import Settings
from app.integrations import mercadolivre as ml
from app.integrations.mercadolivre import MercadoLivreClient
from app.models import (
    CatalogProduct,
    CollectionRun,
    IntegrationError,
    MarketplaceSeller,
    Niche,
    NicheCategory,
    PriceObservation,
)
from tests.fake_ml import FakeML


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ml, "_backoff", lambda attempt: 0.0)


def offer(item: str, price: float, **extra: object) -> dict[str, object]:
    return {"item_id": item, "price": price, "currency_id": "BRL", "condition": "new", **extra}


async def seed(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        niche = Niche(slug="ferramentas", name="Ferramentas", active=True, created_at=utcnow())
        session.add(niche)
        await session.flush()
        session.add(
            NicheCategory(
                niche_id=niche.id,
                marketplace="mercadolivre",
                external_category_id="MLB271379",
                name="Parafusadeiras",
                active=True,
            )
        )


def fake_ml() -> FakeML:
    return FakeML(
        highlights={
            "MLB271379": [
                {"id": "MLBP1", "type": "PRODUCT", "position": 2},
                {
                    "id": "MLBU9",
                    "type": "USER_PRODUCT",
                    "position": 1,
                },  # sem preço pela API: ignorado
            ]
        },
        products={
            "MLBP1": {
                "id": "MLBP1",
                "name": "Parafusadeira X",
                "domain_id": "MLB-DRILLS",
                "permalink": "https://www.mercadolivre.com.br/p/MLBP1",
                "attributes": [{"id": "BRAND", "value_name": "Marca"}],
                "pictures": [{"url": "https://http2.mlstatic.com/x.jpg"}],
            }
        },
        offers={"MLBP1": [offer("MLB100", 399.9), offer("MLB101", 379.9, official_store_id=5)]},
    )


async def test_discovery_creates_catalog_products(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed(sessions)
    fake = fake_ml()
    client = MercadoLivreClient(settings, transport=fake.transport())
    try:
        assert await Collector(sessions, client, settings).discover_due() == 1
        # dentro do intervalo de descoberta, não varre de novo
        assert await Collector(sessions, client, settings).discover_due() == 0
    finally:
        await client.aclose()
    async with sessions() as session:
        product = (await session.execute(select(CatalogProduct))).scalar_one()
        runs = (await session.execute(select(CollectionRun.status))).scalars().all()
    assert product.external_id == "MLBP1"
    assert product.brand == "Marca"
    assert product.tier == "warm"
    assert runs == ["ok"]


async def test_refresh_writes_change_then_skips_until_heartbeat(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed(sessions)
    fake = fake_ml()
    client = MercadoLivreClient(settings, transport=fake.transport())
    collector = Collector(sessions, client, settings)
    try:
        await collector.discover_due()
        stats = await collector.refresh_due()
        # Primeira leitura: linha de partida e avaliação, mas não é mudança de preço (sem a
        # urgência de "acabou de mudar" no U da nota).
        assert (stats.seen, stats.changed) == (1, 0)
        async with sessions() as session:
            product = (await session.execute(select(CatalogProduct))).scalar_one()
        assert product.last_changed_at is None and product.needs_evaluation

        # mesmo preço, ainda dentro do batimento: produto vencido de novo, mas nenhuma linha nova
        async with sessions() as session, session.begin():
            product = (await session.execute(select(CatalogProduct))).scalar_one()
            product.next_check_at = utcnow() - timedelta(seconds=1)
        stats = await collector.refresh_due()
        assert (stats.seen, stats.changed) == (1, 0)

        # queda de 10%: mudança e camada quente
        fake.offers["MLBP1"] = [offer("MLB100", 339.9)]
        async with sessions() as session, session.begin():
            product = (await session.execute(select(CatalogProduct))).scalar_one()
            product.next_check_at = utcnow() - timedelta(seconds=1)
        stats = await collector.refresh_due()
        assert stats.changed == 1
    finally:
        await client.aclose()

    async with sessions() as session:
        rows = (
            (await session.execute(select(PriceObservation).order_by(PriceObservation.id)))
            .scalars()
            .all()
        )
        product = (await session.execute(select(CatalogProduct))).scalar_one()
    assert [(r.kind, r.min_price_cents, r.min_official_price_cents) for r in rows] == [
        ("first", 37990, 37990),
        ("change", 33990, None),
    ]
    assert product.tier == "hot" and product.last_changed_at is not None


async def test_refresh_fills_the_strikethrough_price_without_faking_a_price_change(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed(sessions)
    fake = fake_ml()
    client = MercadoLivreClient(settings, transport=fake.transport())
    collector = Collector(sessions, client, settings)

    async def due_again() -> None:
        async with sessions() as session, session.begin():
            product = (await session.execute(select(CatalogProduct))).scalar_one()
            product.next_check_at = utcnow() - timedelta(seconds=1)
            product.needs_evaluation = False

    try:
        await collector.discover_due()
        await collector.refresh_due()
        # Linha gravada antes da 0013: sem o "de" por régua.
        async with sessions() as session, session.begin():
            first = (await session.execute(select(PriceObservation))).scalar_one()
            first.originals_known = False
            changed_at = (
                (await session.execute(select(CatalogProduct))).scalar_one().last_changed_at
            )
        await due_again()
        fake.offers["MLBP1"] = [
            offer("MLB100", 399.9),
            offer("MLB101", 379.9, official_store_id=5, original_price=499.9),
        ]
        assert (await collector.refresh_due()).changed == 0
        async with sessions() as session:
            product = (await session.execute(select(CatalogProduct))).scalar_one()
        assert product.needs_evaluation and product.last_changed_at == changed_at

        await due_again()
        await collector.refresh_due()  # mesmo "de", já conhecido: nada novo
    finally:
        await client.aclose()

    async with sessions() as session:
        rows = (
            (await session.execute(select(PriceObservation).order_by(PriceObservation.id)))
            .scalars()
            .all()
        )
        product = (await session.execute(select(CatalogProduct))).scalar_one()
    assert [(r.kind, r.official_original_cents, r.originals_known) for r in rows] == [
        ("first", None, False),
        ("heartbeat", 49990, True),
    ]
    assert not product.needs_evaluation


async def test_refresh_not_found_deactivates_only_on_the_third_in_a_row(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """14/10/2026: o ML instável respondeu 404 para produto com oferta; desativar no 1º tirou
    230 produtos da base. Agora só no 3º seguido."""
    await seed(sessions)
    fake = fake_ml()
    client = MercadoLivreClient(settings, transport=fake.transport())
    collector = Collector(sessions, client, settings)

    async def due_now() -> None:
        async with sessions() as session, session.begin():
            (await session.execute(select(CatalogProduct))).scalar_one().next_check_at = utcnow()

    try:
        await collector.discover_due()
        del fake.offers["MLBP1"]
        for expected_active in (True, True, False):
            await due_now()
            stats = await collector.refresh_due()
            assert stats.errors == 1
            async with sessions() as session:
                product = (await session.execute(select(CatalogProduct))).scalar_one()
            assert product.active is expected_active
    finally:
        await client.aclose()
    async with sessions() as session:
        runs = (await session.execute(select(func.count()).select_from(CollectionRun))).scalar_one()
        error = (
            (await session.execute(select(IntegrationError).order_by(IntegrationError.id.desc())))
            .scalars()
            .first()
        )
    assert runs == 4  # descoberta + três atualizações
    assert error is not None and error.message and error.message.startswith("404: ")


async def test_rate_limit_stops_batch_and_keeps_partial_run(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed(sessions)
    fake = fake_ml()
    client = MercadoLivreClient(settings, transport=fake.transport())
    collector = Collector(sessions, client, settings)
    try:
        await collector.discover_due()
        import httpx

        fake.override = lambda r: httpx.Response(429) if r.url.path.endswith("/items") else None
        with pytest.raises(ml.RateLimitedError):
            await collector.refresh_due()
    finally:
        await client.aclose()
    async with sessions() as session:
        statuses = (
            (await session.execute(select(CollectionRun.status).order_by(CollectionRun.id)))
            .scalars()
            .all()
        )
        observations = (
            await session.execute(select(func.count()).select_from(PriceObservation))
        ).scalar_one()
    assert statuses == ["ok", "partial"]
    assert observations == 0


def rep(level: str | None, power: str | None, sales: int) -> dict[str, object]:
    return {"level_id": level, "power_seller_status": power, "transactions": {"total": sales}}


ME2 = {"mode": "me2", "free_shipping": True}


async def test_refresh_prices_only_eligible_sellers_and_caches_reputation(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed(sessions)
    fake = fake_ml()
    fake.offers["MLBP1"] = [
        offer("MLB1", 299.9, seller_id=1, shipping=ME2),  # sem reputação
        offer("MLB2", 319.9, seller_id=2, shipping={"mode": "custom"}),  # entrega a combinar
        offer("MLB3", 349.9, seller_id=3, shipping=ME2),  # verde, prata
        offer("MLB4", 379.9, seller_id=4, shipping=ME2, official_store_id=5),
    ]
    fake.sellers = {
        1: rep(None, None, 0),
        2: rep("5_green", "platinum", 9000),
        3: rep("5_green", "silver", 3520),
        4: rep("5_green", "platinum", 10**6),
    }
    client = MercadoLivreClient(settings, transport=fake.transport())
    collector = Collector(sessions, client, settings)
    try:
        await collector.discover_due()
        await collector.refresh_due()
        assert fake.calls.count("/users/bulk") == 1
        # dentro do TTL do cache, a reputação não é pedida de novo
        async with sessions() as session, session.begin():
            product = (await session.execute(select(CatalogProduct))).scalar_one()
            product.next_check_at = utcnow() - timedelta(seconds=1)
        await collector.refresh_due()
        assert fake.calls.count("/users/bulk") == 1
    finally:
        await client.aclose()

    async with sessions() as session:
        row = (await session.execute(select(PriceObservation))).scalar_one()
        sellers = (
            await session.execute(select(func.count()).select_from(MarketplaceSeller))
        ).scalar_one()
    assert row.min_price_cents == 29990  # bruto, só sinal
    assert (row.eligible_min_price_cents, row.eligible_item_id, row.eligible_official) == (
        34990,
        "MLB3",
        False,
    )
    assert (row.trusted_min_price_cents, row.trusted_item_id, row.trusted_official) == (
        37990,
        "MLB4",
        True,
    )
    assert (row.min_official_price_cents, row.official_item_id) == (37990, "MLB4")
    assert sellers == 4


async def _many_due(sessions: async_sessionmaker[AsyncSession], n: int) -> None:
    now = utcnow()
    async with sessions() as session, session.begin():
        niche = Niche(slug="casa-mimos", name="Casa", active=True, created_at=now)
        session.add(niche)
        await session.flush()
        for i in range(n):
            session.add(
                CatalogProduct(
                    marketplace="mercadolivre",
                    external_id=f"MLBX{i}",
                    niche_id=niche.id,
                    name=f"Produto {i}",
                    discovered_via="highlights",
                    active=True,
                    tier="warm",
                    next_check_at=now - timedelta(minutes=1),
                    consecutive_failures=0,
                    first_seen_at=now,
                )
            )


async def test_unstable_ml_stops_the_batch_and_nobody_takes_the_blame(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """14/10/2026: 51 min de 400/404 em produtos com oferta. Muitos falhando juntos é o ML: o lote
    para, nenhum produto leva falha nem é desativado, e o worker pausa."""
    import httpx

    await _many_due(sessions, 30)
    fake = FakeML(offers={})
    fake.override = lambda r: (
        httpx.Response(400, text="bad request") if r.url.path.endswith("/items") else None
    )
    client = MercadoLivreClient(settings, transport=fake.transport())
    collector = Collector(sessions, client, settings)
    try:
        with pytest.raises(ml.UnstableError):
            await collector.refresh_due()
    finally:
        await client.aclose()
    items_calls = [c for c in fake.calls if c.endswith("/items")]
    assert len(items_calls) < 15  # parou cedo, não varreu o lote todo
    async with sessions() as session:
        products = (await session.execute(select(CatalogProduct))).scalars().all()
        run = (await session.execute(select(CollectionRun))).scalar_one()
        stored = (await session.execute(select(IntegrationError.message))).scalars().all()
    assert all(p.active and p.consecutive_failures == 0 for p in products)
    assert all(p.next_check_at <= utcnow() for p in products)  # continuam vencidos
    assert run.status == "partial" and "ML instável" in (run.note or "")
    assert stored and all("bad request" in m for m in stored)  # o que o ML respondeu


async def test_batch_cut_by_rate_limit_writes_nothing(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """Lote cortado no meio (429/teto): sem a reputação dos vendedores o resumo sairia sem preço
    elegível e viraria mudança falsa. Nada é gravado; o lote volta depois da pausa."""
    import httpx

    await _many_due(sessions, 5)
    fake = FakeML(offers={f"MLBX{i}": [offer(f"MLB{i}", 100.0)] for i in range(5)})
    seen = {"items": 0}

    def third_is_429(request: httpx.Request) -> httpx.Response | None:
        if request.url.path.endswith("/items"):
            seen["items"] += 1
            if seen["items"] == 3:
                return httpx.Response(429)
        return None

    fake.override = third_is_429
    client = MercadoLivreClient(settings, transport=fake.transport())
    collector = Collector(sessions, client, settings)
    try:
        with pytest.raises(ml.RateLimitedError):
            await collector.refresh_due()
    finally:
        await client.aclose()
    async with sessions() as session:
        observations = (
            await session.execute(select(func.count()).select_from(PriceObservation))
        ).scalar_one()
        due = (
            await session.execute(
                select(func.count()).where(CatalogProduct.next_check_at <= utcnow())
            )
        ).scalar_one()
    assert observations == 0 and due == 5
