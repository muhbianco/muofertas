from __future__ import annotations

from datetime import UTC, datetime

from app.domain.distribution import (
    ListingDiscount,
    official_comparison,
    recheck_offer,
    render_message,
    seller_line,
)

NOW = datetime(2026, 10, 12, 11, 23, tzinfo=UTC)
MANTA = "Manta Jolitex Lilo & Stitch Solteiro de Microfibra, Cor Verde-Claro Hipoalergênica"


def test_official_comparison_needs_10_to_70_percent() -> None:
    assert official_comparison(4536, 10493) == ListingDiscount(10493, 56)
    assert official_comparison(9500, 10000) is None  # 5%: não vale a linha
    assert official_comparison(2500, 10000) is None  # 75%: falsificação ou erro de preço
    assert official_comparison(4536, None) is None
    assert official_comparison(4536, 4000) is None  # loja oficial mais barata


def test_recheck_brings_the_cheapest_new_official_price() -> None:
    offers = [
        {"item_id": "MLB1", "price": 45.36, "currency_id": "BRL", "seller_id": 7},
        {"item_id": "MLB2", "price": 104.93, "currency_id": "BRL", "official_store_id": 9},
        {"item_id": "MLB3", "price": 129.9, "currency_id": "BRL", "official_store_id": 9},
        {  # usado de loja oficial não vale como comparação
            "item_id": "MLB4",
            "price": 60.0,
            "currency_id": "BRL",
            "official_store_id": 9,
            "condition": "used",
        },
    ]
    check = recheck_offer(offers, "MLB1", 4536)
    assert (check.status, check.price_cents, check.official_cents) == ("ok", 4536, 10493)
    assert recheck_offer(offers, "MLB9", 4536).status == "gone"


def test_message_compares_with_the_official_store_and_names_the_seller() -> None:
    text = render_message(
        niche_slug="casa-mimos",
        niche_name="Casa e mimos: cama, mesa e banho",
        product_name=MANTA,
        price_cents=4536,
        checked_at=NOW,
        official=False,
        url="https://meli.la/x",
        versus_official=official_comparison(4536, 10493),
        seller=seller_line("5_green", 2211),
    )
    assert text.startswith("#publi · 🏠 *CASA E MIMOS*")
    assert (
        "🔥 *56% mais barato que a loja oficial*\n"
        "🏬 Na loja oficial: ~R$ 104,93~\n"
        "💰 Aqui: *R$ 45,36* no Mercado Livre\n"
        "👍 Vendedor com reputação verde e 2.211 vendas"
    ) in text


def test_listing_discount_wins_and_official_offer_has_no_seller_line() -> None:
    text = render_message(
        niche_slug="beleza",
        niche_name="Beleza",
        product_name="Gel de Limpeza Facial CeraVe 454g",
        price_cents=7432,
        checked_at=NOW,
        official=True,
        url="https://meli.la/x",
        discount=ListingDiscount(12690, 41),
        versus_official=ListingDiscount(20000, 62),
        seller=seller_line("5_green", 50000),
    )
    assert "💰 De ~R$ 126,90~ por *R$ 74,32*" in text
    assert "loja oficial*" not in text and "👍" not in text
    assert seller_line(None, 10) is None and seller_line("3_yellow", 0) == (
        "Vendedor com reputação amarela"
    )
