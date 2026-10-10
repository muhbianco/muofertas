"""Resumo das ofertas de um produto de catálogo e regras de agenda do coletor. Puro, sem I/O.

O ML não entrega preço de anúncio avulso para afiliado (sondagem de 10/10/2026), mas entrega, por
`/products/{id}/items`, todas as ofertas de um produto de catálogo. A unidade do histórico é o
produto: menor preço geral, menor preço de loja oficial e contadores. O `original_price` (o "de"
do vendedor) é guardado como dado auxiliar e nunca vira base de desconto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any


class Tier(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


def to_cents(value: Any) -> int | None:
    """Preço da API (float em reais) → centavos inteiros. Nunca float no banco."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        amount = Decimal(str(value))
    except ArithmeticError:
        return None
    if amount <= 0:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class Offer:
    item_id: str
    seller_id: int | None
    price_cents: int
    original_price_cents: int | None
    official_store_id: int | None
    free_shipping: bool
    has_deal: bool
    condition: str | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Offer | None:
        price = to_cents(raw.get("price"))
        item_id = raw.get("item_id")
        if price is None or not isinstance(item_id, str) or raw.get("currency_id", "BRL") != "BRL":
            return None
        original = to_cents(raw.get("original_price"))
        shipping = raw.get("shipping") or {}
        seller = raw.get("seller_id")
        official = raw.get("official_store_id")
        return cls(
            item_id=item_id,
            seller_id=seller if isinstance(seller, int) else None,
            price_cents=price,
            original_price_cents=original if original and original > price else None,
            official_store_id=official if isinstance(official, int) else None,
            free_shipping=bool(shipping.get("free_shipping")),
            has_deal=bool(raw.get("deal_ids")),
            condition=raw.get("condition") if isinstance(raw.get("condition"), str) else None,
        )


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """O que vira uma linha de `price_observation`."""

    min_price_cents: int
    min_official_price_cents: int | None
    offers_count: int
    official_offers_count: int
    deal_offers_count: int
    free_shipping_offers_count: int
    min_offer_item_id: str
    min_offer_seller_id: int | None
    min_offer_original_price_cents: int | None
    min_offer_free_shipping: bool

    def differs_from(self, previous: CatalogSnapshot | None) -> bool:
        """Mudança que merece linha nova no histórico (o resto vira batimento diário)."""
        if previous is None:
            return True
        return (
            self.min_price_cents != previous.min_price_cents
            or self.min_official_price_cents != previous.min_official_price_cents
            or (self.deal_offers_count > 0) != (previous.deal_offers_count > 0)
            or self.min_offer_item_id != previous.min_offer_item_id
        )


def summarize(raw_offers: list[dict[str, Any]]) -> CatalogSnapshot | None:
    """Só ofertas novas (condition=new) entram: usado não é recomendável no programa."""
    offers = [
        o for o in (Offer.from_api(r) for r in raw_offers) if o and o.condition in (None, "new")
    ]
    if not offers:
        return None
    best = min(offers, key=lambda o: (o.price_cents, o.official_store_id is None, o.item_id))
    official = [o.price_cents for o in offers if o.official_store_id is not None]
    return CatalogSnapshot(
        min_price_cents=best.price_cents,
        min_official_price_cents=min(official) if official else None,
        offers_count=len(offers),
        official_offers_count=len(official),
        deal_offers_count=sum(o.has_deal for o in offers),
        free_shipping_offers_count=sum(o.free_shipping for o in offers),
        min_offer_item_id=best.item_id,
        min_offer_seller_id=best.seller_id,
        min_offer_original_price_cents=best.original_price_cents,
        min_offer_free_shipping=best.free_shipping,
    )


@dataclass(frozen=True, slots=True)
class TierPolicy:
    hot: timedelta
    warm: timedelta
    cold: timedelta
    # queda que promove para a camada quente
    hot_drop_ratio: float = 0.08
    # sem mudança por este tempo, desce de camada
    cool_down_hot: timedelta = timedelta(hours=48)
    cool_down_warm: timedelta = timedelta(days=7)

    def interval(self, tier: Tier) -> timedelta:
        return {Tier.HOT: self.hot, Tier.WARM: self.warm, Tier.COLD: self.cold}[tier]


def next_tier(
    current: Tier,
    *,
    previous: CatalogSnapshot | None,
    new: CatalogSnapshot | None,
    last_changed_at: datetime | None,
    now: datetime,
    policy: TierPolicy,
) -> Tier:
    """Sobe para quente numa queda relevante; desce devagar quando nada muda."""
    if (
        previous
        and new
        and new.min_price_cents <= previous.min_price_cents * (1 - policy.hot_drop_ratio)
    ):
        return Tier.HOT
    if new and previous is None:
        return current
    quiet_for = now - last_changed_at if last_changed_at else timedelta(0)
    if current is Tier.HOT and quiet_for >= policy.cool_down_hot:
        return Tier.WARM
    if current is Tier.WARM and quiet_for >= policy.cool_down_warm:
        return Tier.COLD
    return current


def needs_heartbeat(last_observation_at: datetime | None, now: datetime, every: timedelta) -> bool:
    return last_observation_at is None or now - last_observation_at >= every
