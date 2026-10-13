"""Resumo das ofertas de um produto de catálogo e regras de agenda do coletor. Puro, sem I/O.

O ML não entrega preço de anúncio avulso para afiliado (sondagem de 10/10/2026), mas entrega, por
`/products/{id}/items`, todas as ofertas de um produto de catálogo. A unidade do histórico é o
produto: menor preço geral, menor preço de loja oficial e contadores. O `original_price` (o "de"
do vendedor) é guardado como dado auxiliar e nunca vira base de desconto.
"""

from __future__ import annotations

from collections.abc import Mapping
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


# Regra do programa de afiliados do ML (ajuda 30088): só vendedor com reputação amarela ou verde.
ELIGIBLE_LEVELS = frozenset({"3_yellow", "4_light_green", "5_green"})
TRUSTED_POWER = frozenset({"gold", "platinum"})
# Vendedor reputado (13/10/2026, nichos de loja oficial): verde com histórico grande também
# entra, não só a loja oficial e o MercadoLíder Gold/Platinum.
REPUTABLE_MIN_SALES = 500
# Envio pelo Mercado Envios. "custom"/"not_specified" é "entrega a combinar com o vendedor".
ML_SHIPPING_MODES = frozenset({"me1", "me2"})


@dataclass(frozen=True, slots=True)
class SellerInfo:
    level_id: str | None
    power_seller_status: str | None
    transactions_total: int


@dataclass(frozen=True, slots=True)
class OfferPolicy:
    min_seller_sales: int = 50


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
    shipping_mode: str | None = None

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
        mode = shipping.get("mode")
        return cls(
            item_id=item_id,
            seller_id=seller if isinstance(seller, int) else None,
            price_cents=price,
            original_price_cents=original if original and original > price else None,
            official_store_id=official if isinstance(official, int) else None,
            free_shipping=bool(shipping.get("free_shipping")),
            has_deal=bool(raw.get("deal_ids")),
            condition=raw.get("condition") if isinstance(raw.get("condition"), str) else None,
            shipping_mode=mode if isinstance(mode, str) else None,
        )


def is_eligible(offer: Offer, seller: SellerInfo | None, policy: OfferPolicy) -> bool:
    """Oferta que o programa paga e que dá para recomendar.

    Nova e de vendedor amarelo/verde com envio pelo ML e histórico mínimo. Loja oficial é a marca:
    dispensa a contagem de vendas e o envio pelo ML (gift card e serviço digital vêm sem frete).
    """
    if offer.condition not in (None, "new"):
        return False
    if offer.official_store_id is not None:
        return seller is None or seller.level_id is None or seller.level_id in ELIGIBLE_LEVELS
    return (
        offer.shipping_mode in ML_SHIPPING_MODES
        and seller is not None
        and seller.level_id in ELIGIBLE_LEVELS
        and seller.transactions_total >= policy.min_seller_sales
    )


def is_trusted(offer: Offer, seller: SellerInfo | None, policy: OfferPolicy) -> bool:
    """Para marca de alto risco: loja oficial, ou vendedor verde com MercadoLíder ouro/platina."""
    if not is_eligible(offer, seller, policy):
        return False
    return offer.official_store_id is not None or (
        seller is not None
        and seller.level_id == "5_green"
        and seller.power_seller_status in TRUSTED_POWER
    )


def is_reputable(offer: Offer, seller: SellerInfo | None, policy: OfferPolicy) -> bool:
    """Para os nichos de loja oficial: a confiável (oficial ou MercadoLíder Gold/Platinum verde)
    ou vendedor verde com 500+ vendas. Caso de 13/10: a máscara L'Oréal a R$ 249,99 na loja
    oficial e a R$ 81,61 de vendedor verde com 1.733 vendas, que a régua anterior não via."""
    if is_trusted(offer, seller, policy):
        return True
    return (
        is_eligible(offer, seller, policy)
        and seller is not None
        and seller.level_id == "5_green"
        and seller.transactions_total >= REPUTABLE_MIN_SALES
    )


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """O que vira uma linha de `price_observation`.

    `min_*` é o menor preço bruto (qualquer vendedor): serve só de sinal. O preço que vale para a
    nota sai de uma das três réguas: `eligible_*` (vendedor elegível), `trusted_*` (loja oficial
    ou MercadoLíder Gold/Platinum verde) ou `*official*` (só loja oficial elegível).
    """

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
    eligible_min_price_cents: int | None = None
    eligible_item_id: str | None = None
    eligible_seller_id: int | None = None
    eligible_offers_count: int = 0
    trusted_min_price_cents: int | None = None
    trusted_item_id: str | None = None
    trusted_seller_id: int | None = None
    eligible_official: bool = False
    trusted_official: bool = False
    official_item_id: str | None = None
    official_seller_id: int | None = None
    # O "de" (preço riscado) da oferta de cada régua. `originals_known` é falso nas linhas
    # gravadas antes da 0013, que não tinham esse dado.
    eligible_original_cents: int | None = None
    trusted_original_cents: int | None = None
    official_original_cents: int | None = None
    originals_known: bool = True
    # 0026: régua "reputado" (oficial, MercadoLíder Gold/Platinum ou verde com 500+ vendas).
    # `reputable_known` é falso nas linhas de antes dela: fora da série dessa régua.
    reputable_min_price_cents: int | None = None
    reputable_item_id: str | None = None
    reputable_seller_id: int | None = None
    reputable_official: bool = False
    reputable_original_cents: int | None = None
    reputable_known: bool = True

    @property
    def tracking_price_cents(self) -> int:
        """Preço que move as camadas do coletor: o elegível quando existe."""
        return self.eligible_min_price_cents or self.trusted_min_price_cents or self.min_price_cents

    def differs_from(self, previous: CatalogSnapshot | None) -> bool:
        """Mudança que merece linha nova no histórico (o resto vira batimento diário).

        O menor preço bruto fica de fora: vendedor sem reputação entrando e saindo não é sinal.
        """
        if previous is None:
            return True
        return (
            self.eligible_min_price_cents != previous.eligible_min_price_cents
            or self.trusted_min_price_cents != previous.trusted_min_price_cents
            or self.min_official_price_cents != previous.min_official_price_cents
            or self.eligible_item_id != previous.eligible_item_id
            or (
                previous.reputable_known
                and self.reputable_min_price_cents != previous.reputable_min_price_cents
            )
            or (self.deal_offers_count > 0) != (previous.deal_offers_count > 0)
        )

    def originals_differ_from(self, previous: CatalogSnapshot | None) -> bool:
        """O "de" mudou sem o preço mudar (ou a linha anterior nem tinha o dado).

        Vira batimento, não mudança: não mexe na fila nem nas camadas, só atualiza o selo.
        A linha anterior sem a régua "reputado" (antes da 0026) também: o dado novo entra como
        batimento, sem fingir mudança de preço.
        """
        if previous is None:
            return False
        if not previous.originals_known or not previous.reputable_known:
            return True
        return (
            self.eligible_original_cents,
            self.trusted_original_cents,
            self.official_original_cents,
            self.reputable_original_cents,
        ) != (
            previous.eligible_original_cents,
            previous.trusted_original_cents,
            previous.official_original_cents,
            previous.reputable_original_cents,
        )


def seller_ids(raw_offers: list[dict[str, Any]]) -> set[int]:
    return {s for s in (r.get("seller_id") for r in raw_offers) if isinstance(s, int)}


def summarize(
    raw_offers: list[dict[str, Any]],
    sellers: Mapping[int, SellerInfo] | None = None,
    policy: OfferPolicy | None = None,
) -> CatalogSnapshot | None:
    """Resume as ofertas novas do produto.

    Sem reputação conhecida, nenhum vendedor comum é elegível (só loja oficial).
    """
    policy = policy or OfferPolicy()
    known = sellers or {}
    offers = [
        o for o in (Offer.from_api(r) for r in raw_offers) if o and o.condition in (None, "new")
    ]
    if not offers:
        return None

    def key(o: Offer) -> tuple[int, bool, str]:
        return (o.price_cents, o.official_store_id is None, o.item_id)

    best = min(offers, key=key)
    eligible = [o for o in offers if is_eligible(o, known.get(o.seller_id or -1), policy)]
    trusted = [o for o in eligible if is_trusted(o, known.get(o.seller_id or -1), policy)]
    reputable = [o for o in eligible if is_reputable(o, known.get(o.seller_id or -1), policy)]
    official = [o for o in eligible if o.official_store_id is not None]
    best_eligible = min(eligible, key=key) if eligible else None
    best_trusted = min(trusted, key=key) if trusted else None
    best_reputable = min(reputable, key=key) if reputable else None
    best_official = min(official, key=key) if official else None
    return CatalogSnapshot(
        min_price_cents=best.price_cents,
        min_official_price_cents=best_official.price_cents if best_official else None,
        offers_count=len(offers),
        official_offers_count=len(official),
        deal_offers_count=sum(o.has_deal for o in offers),
        free_shipping_offers_count=sum(o.free_shipping for o in offers),
        min_offer_item_id=best.item_id,
        min_offer_seller_id=best.seller_id,
        min_offer_original_price_cents=best.original_price_cents,
        min_offer_free_shipping=best.free_shipping,
        eligible_min_price_cents=best_eligible.price_cents if best_eligible else None,
        eligible_item_id=best_eligible.item_id if best_eligible else None,
        eligible_seller_id=best_eligible.seller_id if best_eligible else None,
        eligible_offers_count=len(eligible),
        trusted_min_price_cents=best_trusted.price_cents if best_trusted else None,
        trusted_item_id=best_trusted.item_id if best_trusted else None,
        trusted_seller_id=best_trusted.seller_id if best_trusted else None,
        eligible_official=bool(best_eligible and best_eligible.official_store_id is not None),
        trusted_official=bool(best_trusted and best_trusted.official_store_id is not None),
        official_item_id=best_official.item_id if best_official else None,
        official_seller_id=best_official.seller_id if best_official else None,
        eligible_original_cents=best_eligible.original_price_cents if best_eligible else None,
        trusted_original_cents=best_trusted.original_price_cents if best_trusted else None,
        official_original_cents=best_official.original_price_cents if best_official else None,
        reputable_min_price_cents=best_reputable.price_cents if best_reputable else None,
        reputable_item_id=best_reputable.item_id if best_reputable else None,
        reputable_seller_id=best_reputable.seller_id if best_reputable else None,
        reputable_official=bool(best_reputable and best_reputable.official_store_id is not None),
        reputable_original_cents=best_reputable.original_price_cents if best_reputable else None,
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
        and new.tracking_price_cents <= previous.tracking_price_cents * (1 - policy.hot_drop_ratio)
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
