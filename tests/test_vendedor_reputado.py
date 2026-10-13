"""Régua "reputado" (13/10/2026): nos nichos de loja oficial, verde com 500+ vendas entra."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.domain.catalog_offers import SellerInfo, summarize
from app.domain.scoring import kind_price
from app.evaluation.service import Evaluator
from app.models import CatalogProduct, Niche, OfferEvaluation, PriceObservation


def _offer(item: str, price: float, seller: int, *, official: bool = False) -> dict[str, Any]:
    return {
        "item_id": item,
        "price": price,
        "currency_id": "BRL",
        "seller_id": seller,
        "official_store_id": 7 if official else None,
        "condition": "new",
        "shipping": {"mode": "me2", "free_shipping": True},
    }


# A máscara L'Oréal de 13/10: loja oficial a R$ 249,99, vendedor verde com 1.733 vendas a
# R$ 81,61, amarelo com 131 a R$ 87 e, para os limites, verde com 300 vendas e MercadoLíder Gold.
OFFERS = [
    _offer("MLBOFICIAL", 249.99, 1, official=True),
    _offer("MLBVERDE", 81.61, 2),
    _offer("MLBAMARELO", 87.00, 3),
    _offer("MLBPOUCAS", 70.00, 4),
    _offer("MLBGOLD", 90.00, 5),
]
SELLERS = {
    1: SellerInfo("5_green", None, 100000),
    2: SellerInfo("5_green", None, 1733),
    3: SellerInfo("3_yellow", None, 131),
    4: SellerInfo("5_green", None, 300),
    5: SellerInfo("5_green", "gold", 60),
}


def test_reputable_is_green_with_500_sales_or_trusted() -> None:
    snap = summarize(OFFERS, SELLERS)
    assert snap is not None
    assert (snap.eligible_min_price_cents, snap.eligible_item_id) == (7000, "MLBPOUCAS")
    assert (snap.trusted_min_price_cents, snap.trusted_item_id) == (9000, "MLBGOLD")
    assert (snap.reputable_min_price_cents, snap.reputable_item_id) == (8161, "MLBVERDE")
    assert not snap.reputable_official
    assert kind_price(snap, "reputado") == (8161, "MLBVERDE", 2)
    # Linha de antes da 0026: a régua de então (confiável), não um preço inventado.
    old = replace(snap, reputable_known=False)
    assert kind_price(old, "reputado") == (9000, "MLBGOLD", 5)


def test_old_row_gets_the_new_data_as_heartbeat_not_as_a_price_change() -> None:
    snap = summarize(OFFERS, SELLERS)
    assert snap is not None
    old = replace(
        snap,
        reputable_known=False,
        reputable_min_price_cents=None,
        reputable_item_id=None,
        reputable_seller_id=None,
    )
    assert not snap.differs_from(old)  # não volta à fila nem mexe nas camadas
    assert snap.originals_differ_from(old)  # mas grava a linha e reavalia
    cheaper = replace(snap, reputable_min_price_cents=7900)
    assert cheaper.differs_from(snap)  # depois disso, mudança da régua é mudança


async def _beleza_with_history(sessions: async_sessionmaker[AsyncSession]) -> int:
    """Duas linhas antigas (sem a régua nova) a R$ 249,99 e uma nova com o verde a R$ 81,61."""
    now = utcnow()
    snap = summarize(OFFERS, SELLERS)
    assert snap is not None
    async with sessions() as s, s.begin():
        niche = Niche(
            slug="beleza",
            name="Beleza",
            active=True,
            created_at=now,
            commission_rate_bp=1600,
            require_official=True,
            accept_trusted=True,
            official_only_domains=json.dumps(["MLB-PERFUMES"]),
        )
        s.add(niche)
        await s.flush()
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLB27987921",
            niche_id=niche.id,
            name="Absolut Repair Gold Quinoa Máscara 500g - L'oréal",
            brand="L'Oréal Professionnel",
            domain_id="MLB-HAIR_MASKS",
            discovered_via="highlights",
            active=True,
            tier="hot",
            next_check_at=now,
            last_checked_at=now - timedelta(minutes=5),
            last_changed_at=now - timedelta(days=2),
            last_observation_at=now - timedelta(minutes=5),
            consecutive_failures=0,
            first_seen_at=now - timedelta(days=40),
            best_highlight_position=3,
            needs_evaluation=True,
        )
        s.add(product)
        await s.flush()
        for days_ago, known in ((30, False), (15, False), (0.0035, True)):
            s.add(
                PriceObservation(
                    product_id=product.id,
                    observed_at=now - timedelta(days=days_ago),
                    kind="change",
                    currency="BRL",
                    min_price_cents=snap.min_price_cents,
                    min_official_price_cents=24999,
                    offers_count=5,
                    official_offers_count=1,
                    deal_offers_count=0,
                    free_shipping_offers_count=5,
                    min_offer_item_id="MLBPOUCAS",
                    min_offer_seller_id=4,
                    min_offer_original_price_cents=None,
                    min_offer_free_shipping=True,
                    eligible_min_price_cents=7000,
                    eligible_item_id="MLBPOUCAS",
                    eligible_seller_id=4,
                    eligible_offers_count=5,
                    # antes da régua MercadoLíder, a confiável era só a oficial
                    trusted_min_price_cents=24999,
                    trusted_item_id="MLBOFICIAL",
                    trusted_seller_id=1,
                    trusted_official=True,
                    official_item_id="MLBOFICIAL",
                    official_seller_id=1,
                    reputable_min_price_cents=8161 if known else None,
                    reputable_item_id="MLBVERDE" if known else None,
                    reputable_seller_id=2 if known else None,
                    reputable_known=known,
                )
            )
        return product.id


async def test_beauty_niche_prices_the_reputable_seller_without_a_fake_discount(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _beleza_with_history(sessions)
    assert await Evaluator(sessions).evaluate_due() == 1
    async with sessions() as s:
        ev = (await s.execute(select(OfferEvaluation))).scalar_one()
    snapshot = json.loads(ev.snapshot)
    assert ev.price_cents == 8161 and not ev.used_official
    assert snapshot["offer_kind"] == "reputado" and snapshot["offer_item_id"] == "MLBVERDE"
    # As linhas antigas (R$ 249,99 de outra régua) ficam fora da série: sem elas, o verde que
    # sempre esteve lá viraria um "desconto real" de 67%.
    assert ev.real_discount_bp is None
