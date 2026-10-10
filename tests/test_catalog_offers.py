from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.catalog_offers import (
    CatalogSnapshot,
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


def test_differs_from() -> None:
    assert snap(1000).differs_from(None)
    assert not snap(1000).differs_from(snap(1000, offers_count=9))  # só contagem não é mudança
    assert snap(900).differs_from(snap(1000))
    assert snap(1000, min_official_price_cents=1100).differs_from(snap(1000))
    assert snap(1000, deal_offers_count=2).differs_from(snap(1000))
    assert snap(1000, min_offer_item_id="MLB2").differs_from(snap(1000))


def test_next_tier_goes_hot_on_relevant_drop() -> None:
    tier = next_tier(
        Tier.WARM,
        previous=snap(10_000),
        new=snap(9_000),
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
