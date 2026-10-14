from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.catalog_offers import (
    CatalogSnapshot,
    OfferPolicy,
    SellerInfo,
    Tier,
    TierPolicy,
    needs_heartbeat,
    next_tier,
    summarize,
    to_cents,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
POLICY = TierPolicy(hot=timedelta(minutes=20), warm=timedelta(hours=3), cold=timedelta(days=1))


def offer(item: str, price: float, **extra: object) -> dict[str, object]:
    return {"item_id": item, "price": price, "currency_id": "BRL", "condition": "new", **extra}


def snap(price: int, **extra: object) -> CatalogSnapshot:
    base: dict[str, object] = {
        "min_price_cents": price,
        "min_official_price_cents": None,
        "offers_count": 3,
        "official_offers_count": 0,
        "deal_offers_count": 0,
        "free_shipping_offers_count": 1,
        "min_offer_item_id": "MLB1",
        "min_offer_seller_id": 10,
        "min_offer_original_price_cents": None,
        "min_offer_free_shipping": True,
    }
    base.update(extra)
    return CatalogSnapshot(**base)  # type: ignore[arg-type]


def test_to_cents_rounds_half_up_and_rejects_garbage() -> None:
    assert to_cents(34.95) == 3495
    assert to_cents(18.985) == 1899
    assert to_cents("19.9") == 1990
    assert to_cents(0) is None
    assert to_cents(-1) is None
    assert to_cents(True) is None
    assert to_cents(None) is None


def test_summarize_picks_min_and_official_min_and_counts() -> None:
    raw = [
        offer("MLB1", 49.59),
        offer(
            "MLB2",
            34.95,
            official_store_id=140844,
            original_price=69.9,
            shipping={"free_shipping": True},
        ),
        offer("MLB3", 34.10, deal_ids=["D1"]),
        offer("MLB4", 20.00, condition="used"),  # usado não conta
        {
            "item_id": "MLB5",
            "price": 10.0,
            "currency_id": "USD",
            "condition": "new",
        },  # moeda errada
    ]
    s = summarize(raw)
    assert s is not None
    assert s.min_price_cents == 3410
    assert s.min_offer_item_id == "MLB3"
    assert s.min_official_price_cents == 3495
    assert s.offers_count == 3
    assert s.official_offers_count == 1
    assert s.deal_offers_count == 1
    assert s.free_shipping_offers_count == 1


def test_original_price_kept_only_when_above_price() -> None:
    s = summarize([offer("MLB1", 50.0, original_price=40.0)])
    assert s is not None and s.min_offer_original_price_cents is None
    s = summarize([offer("MLB1", 50.0, original_price=80.0)])
    assert s is not None and s.min_offer_original_price_cents == 8000


def test_tie_prefers_official_store() -> None:
    s = summarize([offer("MLB9", 30.0), offer("MLB2", 30.0, official_store_id=7)])
    assert s is not None and s.min_offer_item_id == "MLB2"


def test_summarize_empty_or_only_used() -> None:
    assert summarize([]) is None
    assert summarize([offer("MLB1", 10.0, condition="used")]) is None


def test_differs_from_ignores_raw_min_from_ineligible_sellers() -> None:
    base = snap(1000, eligible_min_price_cents=1200, eligible_item_id="MLB2")
    assert base.differs_from(None)
    # vendedor sem reputação entrando mais barato: não é mudança
    assert not base.differs_from(snap(500, eligible_min_price_cents=1200, eligible_item_id="MLB2"))
    assert snap(1000, eligible_min_price_cents=1100, eligible_item_id="MLB2").differs_from(base)
    assert snap(1000, eligible_min_price_cents=1200, eligible_item_id="MLB3").differs_from(base)
    assert snap(
        1000, eligible_min_price_cents=1200, eligible_item_id="MLB2", trusted_min_price_cents=1300
    ).differs_from(base)
    assert snap(
        1000, eligible_min_price_cents=1200, eligible_item_id="MLB2", deal_offers_count=2
    ).differs_from(base)


GREEN_SILVER = SellerInfo("5_green", "silver", 3520)
GREEN_GOLD = SellerInfo("5_green", "gold", 12000)
GREEN_NEW = SellerInfo("5_green", None, 10)
NO_REP = SellerInfo(None, None, 0)
ME2 = {"mode": "me2", "free_shipping": True}


def test_galaxy_case_ignores_seller_without_reputation() -> None:
    # Caso real de 10/10/2026 (Galaxy A17): R$ 1.170,40 de vendedor sem reputação e 0 vendas,
    # R$ 1.299 de verde/MercadoLíder, R$ 1.309 de verde/Gold, loja oficial a R$ 1.499.
    raw = [
        offer("MLB5360397725", 1170.40, seller_id=1, shipping=ME2),
        offer("MLB7083641270", 1299.00, seller_id=2, shipping=ME2),
        offer("MLB6000000001", 1309.00, seller_id=4, shipping=ME2),
        offer("MLB4217058289", 1499.00, seller_id=3, official_store_id=2962, shipping=ME2),
    ]
    official = SellerInfo("5_green", "platinum", 10**6)
    sellers = {1: NO_REP, 2: GREEN_SILVER, 3: official, 4: GREEN_GOLD}
    s = summarize(raw, sellers)
    assert s is not None
    assert s.min_price_cents == 117040  # bruto: só sinal
    assert (s.eligible_min_price_cents, s.eligible_item_id) == (129900, "MLB7083641270")
    assert (s.trusted_min_price_cents, s.trusted_item_id) == (130900, "MLB6000000001")
    assert (s.min_official_price_cents, s.official_item_id) == (149900, "MLB4217058289")
    assert not s.eligible_official and not s.trusted_official


def test_gift_card_case_only_the_official_store_is_eligible() -> None:
    # Caso real (gift card PSN R$ 150): R$ 70 com 3 vendas e "entrega a combinar", R$ 125 com
    # 1 venda. Só a loja oficial vale; ela vende o código sem frete, e isso não a desqualifica.
    raw = [
        offer("MLB5359577131", 70.0, seller_id=1, shipping={"mode": "custom"}),
        offer("MLB5365840309", 125.0, seller_id=2, shipping={"mode": "custom"}),
        offer(
            "MLB5408400744",
            150.0,
            seller_id=3,
            official_store_id=1473,
            shipping={"mode": "not_specified"},
        ),
    ]
    sellers = {
        1: SellerInfo(None, None, 3),
        2: SellerInfo("4_light_green", None, 1),
        3: SellerInfo("5_green", "platinum", 1_156_788),
    }
    s = summarize(raw, sellers)
    assert s is not None
    assert s.min_price_cents == 7000
    assert (s.eligible_min_price_cents, s.eligible_item_id, s.eligible_official) == (
        15000,
        "MLB5408400744",
        True,
    )
    assert s.trusted_min_price_cents == s.min_official_price_cents == 15000


def test_min_sales_and_trusted_rules() -> None:
    raw = [
        offer("A", 100.0, seller_id=1, shipping=ME2),
        offer("B", 110.0, seller_id=2, shipping=ME2),
    ]
    s = summarize(raw, {1: GREEN_NEW, 2: SellerInfo("4_light_green", None, 500)})
    assert s is not None
    assert s.eligible_item_id == "B"  # A tem só 10 vendas
    assert s.trusted_min_price_cents is None  # B não é verde ouro/platina
    s = summarize(raw, {1: GREEN_NEW}, OfferPolicy(min_seller_sales=5))
    assert s is not None and s.eligible_item_id == "A"


def test_unknown_sellers_are_not_eligible() -> None:
    s = summarize([offer("A", 100.0, seller_id=9, shipping=ME2)])
    assert s is not None and s.eligible_min_price_cents is None


def test_next_tier_goes_hot_on_relevant_drop() -> None:
    tier = next_tier(
        Tier.WARM,
        previous=snap(10_000, eligible_min_price_cents=10_000),
        new=snap(5_000, eligible_min_price_cents=9_000),
        last_changed_at=NOW,
        now=NOW,
        policy=POLICY,
    )
    assert tier is Tier.HOT


def test_next_tier_small_drop_keeps_tier() -> None:
    tier = next_tier(
        Tier.WARM,
        previous=snap(10_000),
        new=snap(9_500),
        last_changed_at=NOW,
        now=NOW,
        policy=POLICY,
    )
    assert tier is Tier.WARM


def test_next_tier_cools_down_when_quiet() -> None:
    quiet = NOW - timedelta(hours=49)
    assert (
        next_tier(
            Tier.HOT, previous=snap(1), new=snap(1), last_changed_at=quiet, now=NOW, policy=POLICY
        )
        is Tier.WARM
    )
    very_quiet = NOW - timedelta(days=8)
    assert (
        next_tier(
            Tier.WARM,
            previous=snap(1),
            new=snap(1),
            last_changed_at=very_quiet,
            now=NOW,
            policy=POLICY,
        )
        is Tier.COLD
    )


def test_needs_heartbeat() -> None:
    assert needs_heartbeat(None, NOW, timedelta(hours=24))
    assert not needs_heartbeat(NOW - timedelta(hours=23), NOW, timedelta(hours=24))
    assert needs_heartbeat(NOW - timedelta(hours=24), NOW, timedelta(hours=24))


def test_summarize_keeps_the_strikethrough_price_of_each_ruler() -> None:
    raw = [
        offer("MLB1", 30.0, original_price=50.0, seller_id=7, shipping={"mode": "me2"}),
        offer("MLB2", 35.0, original_price=60.0, official_store_id=5, seller_id=8),
    ]
    sellers = {7: SellerInfo("5_green", None, 500), 8: SellerInfo("5_green", "platinum", 9000)}
    s = summarize(raw, sellers)
    assert s is not None
    assert (s.eligible_item_id, s.eligible_original_cents) == ("MLB1", 5000)
    assert (s.trusted_item_id, s.trusted_original_cents) == ("MLB2", 6000)
    assert (s.official_item_id, s.official_original_cents) == ("MLB2", 6000)
    assert s.originals_known


def test_originals_differ_only_when_the_strikethrough_changes_or_was_unknown() -> None:
    from dataclasses import replace

    old = snap(10000, eligible_original_cents=None, originals_known=False)
    new = snap(10000, eligible_original_cents=15000)
    assert not new.differs_from(old)  # preço igual: não é mudança de preço
    assert new.originals_differ_from(old)  # linha antiga sem o dado: completa
    assert not new.originals_differ_from(new)
    assert new.originals_differ_from(replace(new, eligible_original_cents=16000))
    assert not new.originals_differ_from(None)


def test_hot_lasts_12h_from_the_drop_even_if_the_price_keeps_moving() -> None:
    """14/10/2026: quente durava 48 h desde qualquer mudança; com preço mudando todo dia, 579
    produtos não esfriavam. Agora conta da última queda relevante."""
    moved_recently = NOW - timedelta(hours=1)
    still = next_tier(
        Tier.HOT,
        previous=snap(10_000),
        new=snap(9_800),
        last_changed_at=moved_recently,
        now=NOW,
        policy=POLICY,
        hot_since=NOW - timedelta(hours=11),
    )
    cooled = next_tier(
        Tier.HOT,
        previous=snap(10_000),
        new=snap(9_800),
        last_changed_at=moved_recently,
        now=NOW,
        policy=POLICY,
        hot_since=NOW - timedelta(hours=13),
    )
    assert (still, cooled) == (Tier.HOT, Tier.WARM)
    # Nova queda relevante: volta (ou fica) quente.
    assert (
        next_tier(
            Tier.HOT,
            previous=snap(10_000),
            new=snap(9_000),
            last_changed_at=NOW,
            now=NOW,
            policy=POLICY,
            hot_since=NOW - timedelta(hours=13),
        )
        is Tier.HOT
    )
