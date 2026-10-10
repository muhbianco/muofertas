from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import pytest

from app.domain.distribution import (
    ChannelPolicy,
    LinkError,
    PriorPost,
    local_day_start,
    offer_page_url,
    parse_affiliate_links,
    recheck_offer,
    render_message,
    repeat_block_reason,
    send_block_reason,
    share_url,
)

# 15h em Brasília (UTC-3)
NOW = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
POLICY = ChannelPolicy(
    max_per_day=3, min_interval=timedelta(minutes=40), window_start_hour=8, window_end_hour=22
)


def test_policy_allows_inside_window_and_limits() -> None:
    assert send_block_reason(POLICY, now=NOW, sent_today=0, last_sent_at=None) is None
    assert send_block_reason(POLICY, now=NOW, sent_today=3, last_sent_at=None) == (
        "limite do dia atingido (3)"
    )
    last = NOW - timedelta(minutes=10)
    assert send_block_reason(POLICY, now=NOW, sent_today=1, last_sent_at=last) == (
        "próximo envio a partir de 15:30"
    )
    paused = ChannelPolicy(3, timedelta(minutes=40), 8, 22, paused=True)
    assert send_block_reason(paused, now=NOW, sent_today=0, last_sent_at=None) == "grupo pausado"


def test_policy_window_is_in_brasilia_time() -> None:
    # 01h UTC = 22h em Brasília: janela fechada, embora em UTC fosse madrugada "cedo".
    late = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)
    assert send_block_reason(POLICY, now=late, sent_today=0, last_sent_at=None) == (
        "fora da janela (08h às 22h)"
    )
    assert local_day_start(late).hour == 0 and local_day_start(late).day == 21


def test_repeat_needs_ten_percent_drop_and_high_tier() -> None:
    prior = [PriorPost(10_000, NOW - timedelta(days=2))]
    assert repeat_block_reason(prior, price_cents=9_500, tier="excepcional", now=NOW) == (
        "já foi para este grupo em 19/10"
    )
    assert repeat_block_reason(prior, price_cents=9_000, tier="excelente", now=NOW) is None
    assert repeat_block_reason(prior, price_cents=8_000, tier="boa", now=NOW) is not None
    old = [PriorPost(10_000, NOW - timedelta(days=8))]
    assert repeat_block_reason(old, price_cents=10_000, tier="boa", now=NOW) is None


def test_affiliate_links_only_official_short_links_in_order() -> None:
    text = "https://meli.la/2AbC9\n\n https://mercadolivre.com/sec/1xYz \n"
    assert parse_affiliate_links(text, 2) == [
        "https://meli.la/2AbC9",
        "https://mercadolivre.com/sec/1xYz",
    ]
    with pytest.raises(LinkError, match="vieram 1 links para 2"):
        parse_affiliate_links("https://meli.la/a", 2)
    with pytest.raises(LinkError, match="linha 1"):
        parse_affiliate_links("http://meli.la/a", 1)
    with pytest.raises(LinkError, match="linha 1"):
        parse_affiliate_links("https://encurta.dor/abc", 1)
    with pytest.raises(LinkError, match="sem /sec/"):
        parse_affiliate_links("https://mercadolivre.com/p/MLB1", 1)
    with pytest.raises(LinkError, match="linha 1"):
        parse_affiliate_links("https://www.mercadolivre.com.br/p/MLB1?matt_tool=1", 1)


def test_recheck_reads_the_linked_offer_not_the_cheapest() -> None:
    offers = [
        {"item_id": "MLB1", "price": 99.0, "currency_id": "BRL"},
        {"item_id": "MLB2", "price": 129.9, "currency_id": "BRL"},
    ]
    assert recheck_offer(offers, "MLB2", 12990).status == "ok"
    assert recheck_offer(offers, "MLB2", 13500).price_cents == 12990  # caiu: vale o novo
    up = recheck_offer(offers, "MLB2", 12000)
    assert (up.status, up.price_cents) == ("price_up", 12990)
    assert recheck_offer(offers, "MLB3", 12990).status == "gone"


def test_message_starts_with_publi_and_has_check_time() -> None:
    text = render_message(
        niche_slug="ferramentas",
        niche_name="Ferramentas",
        product_name="Parafusadeira X " * 20,
        price_cents=38990,
        checked_at=NOW,
        official=True,
        url="https://meli.la/abc",
    )
    lines = text.split("\n")
    # #publi em primeiro plano, numa linha só com o nicho e a hora (CDC 36, CONAR 2026)
    assert lines[0] == "#publi · 🔧 *FERRAMENTAS*"
    assert lines[2].startswith("*Parafusadeira X") and lines[2].endswith("…*")
    assert len(lines[2]) <= 92 and lines[3] == "✅ Loja oficial"
    assert "💰 *R$ 389,90* no Mercado Livre" in lines
    assert "👉 https://meli.la/abc" in lines
    assert lines[-1] == "_Preço conferido em 21/10 às 15:00; pode mudar a qualquer momento._"
    assert "comissão" not in text
    assert "abaixo" not in text  # nada calculado sobre histórico do ML (P6)
    assert unquote(share_url(text).removeprefix("https://wa.me/?text=")) == text


def test_offer_page_url_selects_the_offer() -> None:
    assert offer_page_url(None, "MLB123", "MLB9") == (
        "https://www.mercadolivre.com.br/p/MLB123?pdp_filters=item_id:MLB9"
    )
    assert offer_page_url("https://x/p/MLB1?a=1", "MLB1", "MLB9").endswith(
        "&pdp_filters=item_id:MLB9"
    )


def test_parse_brl_accepts_brazilian_formats() -> None:
    from app.domain.distribution import PixError, check_pix, parse_brl

    assert parse_brl("49,50") == 4950
    assert parse_brl(" R$ 1.299,00 ") == 129900
    assert parse_brl("49.5") == 4950
    assert parse_brl("1.299") == 129900
    assert parse_brl("") is None
    for bad in ("abc", "NaN", "Infinity", "-5", "0", "49,505"):
        with pytest.raises(PixError):
            parse_brl(bad)
    check_pix(4950, 5500)
    with pytest.raises(PixError, match="menor"):
        check_pix(5500, 5500)
    with pytest.raises(PixError, match="30%"):
        check_pix(3000, 5500)


def test_message_with_pix_shows_both_prices() -> None:
    text = render_message(
        niche_slug="observacao",
        niche_name="Observação",
        product_name="Microfone Lapela A'Gold MCF-38D",
        price_cents=5500,
        checked_at=NOW,
        official=False,
        url="https://meli.la/x",
        pix_cents=4950,
    )
    assert "💰 *R$ 55,00* no Mercado Livre\n⚡ *R$ 49,50 no Pix*" in text


def test_listing_discount_matches_the_page_and_blocks_inflated_anchors() -> None:
    from app.domain.distribution import listing_discount

    # Jequiti oficial: de 79,90 por 49,90 = 37% OFF na página (truncado, não 38)
    jequiti = listing_discount(4990, 7990, None)
    assert jequiti is not None and (jequiti.original_cents, jequiti.percent) == (7990, 37)
    assert listing_discount(4490, 9646, None).percent == 53  # type: ignore[union-attr]
    assert listing_discount(4990, None, None) is None
    assert listing_discount(4990, 5100, None) is None  # 2%: não vale a linha
    assert listing_discount(2000, 9990, None) is None  # 79%: erro de preço ou golpe
    # com histórico: "de" 1,5x acima da nossa referência é âncora inflada
    assert listing_discount(4490, 9646, 5000) is None
    assert listing_discount(4490, 7000, 5000) is not None


def test_message_with_listing_discount() -> None:
    from app.domain.distribution import ListingDiscount

    text = render_message(
        niche_slug="beleza",
        niche_name="Beleza: maquiagem, perfumes e cuidados",
        product_name="Colônia Jequiti Turma da Mônica Cebolinha 50 mL",
        price_cents=4990,
        checked_at=NOW,
        official=True,
        url="https://meli.la/x",
        discount=ListingDiscount(7990, 37),
    )
    assert text.startswith("#publi · 💄 *BELEZA*")
    assert "🔥 *37% OFF*" in text
    assert "💰 De ~R$ 79,90~ por *R$ 49,90* no Mercado Livre" in text
