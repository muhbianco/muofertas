"""Link avulso: o operador cola um anúncio, digita o preço e o post sai em 1º, sem a API."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.distribution.links import resolve_links
from app.distribution.manual import ManualError, ManualPost, check_image_url, queue_manual
from app.distribution.sender import send_due
from app.domain.distribution import (
    FlashError,
    LinkError,
    manual_block_reason,
    parse_flash_left,
    parse_manual_link,
    render_message,
)
from app.integrations.mercadolivre import MercadoLivreClient
from app.integrations.ml_affiliate import AffiliatePortal
from app.integrations.wuzapi import WuzapiClient
from app.models import CatalogProduct, Channel, Publication
from tests.fake_ml import FakeML
from tests.fake_wuzapi import FakeWuzapi
from tests.test_mudar_grupo import _approved_in_the_wrong_group
from tests.test_panel import login, seed_admin
from tests.test_sender import ITEM, NOW, approve, cfg

PANELAS = (
    "https://www.mercadolivre.com.br/jogo-de-panelas-antiaderente-inducao-12-pecas/up/"
    "MLBU3012345678?pdp_filters=item_id:MLB5123456789#polycard_client=search"
)


def test_manual_link_reads_the_ml_page_kinds() -> None:
    up = parse_manual_link(PANELAS)
    assert (up.external_id, up.item_id) == ("MLBU3012345678", "MLB5123456789")
    assert up.target_url == (
        "https://www.mercadolivre.com.br/jogo-de-panelas-antiaderente-inducao-12-pecas/up/"
        "MLBU3012345678?pdp_filters=item_id:MLB5123456789"
    )  # sem o rastreio da busca
    listing = parse_manual_link("https://produto.mercadolivre.com.br/MLB-4012345678-panela-_JM?x=1")
    assert (listing.external_id, listing.item_id) == ("MLB4012345678", "MLB4012345678")
    assert listing.target_url == "https://produto.mercadolivre.com.br/MLB-4012345678-panela-_JM"
    catalog = parse_manual_link("https://www.mercadolivre.com.br/panela/p/MLB19615372?wid=MLB40123")
    assert (catalog.external_id, catalog.item_id) == ("MLB19615372", None)  # wid curto não vale
    for bad, why in [
        ("https://meli.la/2xYz", "encurtado"),
        ("http://www.mercadolivre.com.br/p/MLB19615372", "mercadolivre.com.br"),
        ("https://mercadolivre.com.br.golpe.com/p/MLB19615372", "mercadolivre.com.br"),
        ("https://www.mercadolivre.com.br/ofertas", "código do anúncio"),
    ]:
        with pytest.raises(LinkError, match=why):
            parse_manual_link(bad)


def test_flash_timer_and_validity() -> None:
    assert parse_flash_left("07:10:38") == timedelta(hours=7, minutes=10, seconds=38)
    assert parse_flash_left(" 1:30 ") == timedelta(hours=1, minutes=30)
    for bad in ["0:09:59", "25:00", "amanhã", "7:99"]:
        with pytest.raises(FlashError):
            parse_flash_left(bad)
    ends = NOW + timedelta(hours=1)
    assert manual_block_reason(NOW, ends, NOW + timedelta(minutes=49)) is None
    assert "relâmpago acabou" in (manual_block_reason(NOW, ends, NOW + timedelta(minutes=51)) or "")
    # A relâmpago manda na validade; sem ela, 6 horas.
    assert manual_block_reason(NOW, NOW + timedelta(hours=9), NOW + timedelta(hours=7)) is None
    assert manual_block_reason(NOW, None, NOW + timedelta(hours=5, minutes=59)) is None
    assert "6 h" in (manual_block_reason(NOW, None, NOW + timedelta(hours=6, minutes=1)) or "")


def test_flash_line_in_the_message() -> None:
    def body(ends: datetime) -> str:
        return render_message(
            niche_slug="casa-mimos",
            niche_name="Casa e mimos",
            product_name="Jogo de Panelas",
            price_cents=30800,
            checked_at=NOW,  # 15h em Brasília
            official=False,
            url="https://meli.la/x",
            flash_ends_at=ends,
        )

    assert "⏰ *OFERTA RELÂMPAGO* · acaba às 22:10" in body(NOW + timedelta(hours=7, minutes=10))
    assert "acaba em 22/10 às 03:00" in body(NOW + timedelta(hours=12))  # vira o dia


def _post(channel_ids: list[int], **extra: object) -> ManualPost:
    values: dict[str, object] = {
        "url": PANELAS,
        "name": "Jogo De Panelas Antiaderente Indução 12 Peças Fundo Triplo",
        "price_cents": 30800,
        "original_cents": 69900,
        "pix_cents": None,
        "coupon_cents": None,
        "last_units": 4,
        "image_url": None,
        "flash_left": timedelta(hours=7, minutes=10, seconds=38),
        "channel_ids": channel_ids,
        "nonce": "nonce-teste-1",
    }
    return ManualPost(**(values | extra))  # type: ignore[arg-type]


async def _links(sessions: async_sessionmaker[AsyncSession], now: datetime) -> None:
    portal = AffiliatePortal(cfg())
    try:
        await resolve_links(sessions, portal, cfg(), now, pause=0)
    finally:
        await portal.aclose()


async def _send(
    sessions: async_sessionmaker[AsyncSession], wa: FakeWuzapi, now: datetime
) -> FakeML:
    fake_ml = FakeML(offers={"MLBP1": [{"item_id": ITEM, "price": 449.90, "currency_id": "BRL"}]})
    ml_client = MercadoLivreClient(cfg(), transport=fake_ml.transport())
    wa_client = WuzapiClient(cfg(), transport=wa.transport())
    try:
        await send_due(sessions, ml_client, wa_client, now)
    finally:
        await ml_client.aclose()
        await wa_client.aclose()
    return fake_ml


async def test_manual_post_jumps_the_queue_and_skips_the_ml(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions)  # um post comum já pronto no grupo
    async with sessions() as s, s.begin():
        channel = (await s.execute(select(Channel))).scalar_one()
        result = await queue_manual(s, _post([channel.id]), actor="dono", now=NOW)
    assert (result.created, result.skipped) == (1, [])
    await _links(sessions, NOW)
    wa = FakeWuzapi()
    fake_ml = await _send(sessions, wa, NOW + timedelta(minutes=2))
    assert len(wa.sent) == 1
    body = wa.sent[0]["Body"]
    assert "*Jogo De Panelas Antiaderente Indução 12 Peças Fundo Triplo*" in body  # o avulso
    assert "⏰ *OFERTA RELÂMPAGO* · acaba às 22:10" in body
    assert "De ~R$ 699,00~ por *R$ 308,00*" in body
    assert "⏳ *Últimas 4 unidades!*" in body
    assert "Preço conferido em 21/10 às 15:00" in body  # a hora em que o operador viu
    assert not any(c.startswith("/products/") for c in fake_ml.calls)  # nada de revalidação
    async with sessions() as s:
        pubs = (await s.execute(select(Publication).order_by(Publication.id))).scalars().all()
        product = (
            await s.execute(select(CatalogProduct).where(CatalogProduct.external_id.like("MLBU%")))
        ).scalar_one()
    assert [(p.tier, p.state) for p in pubs] == [("boa", "ready"), ("avulso", "sent")]
    assert (product.active, product.discovered_via) == (False, "avulso")  # o robô não acompanha


async def test_manual_post_does_not_go_after_it_expires(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions)
    async with sessions() as s, s.begin():
        channel = (await s.execute(select(Channel))).scalar_one()
        await queue_manual(
            s, _post([channel.id], flash_left=timedelta(minutes=30)), actor="dono", now=NOW
        )
    await _links(sessions, NOW)
    wa = FakeWuzapi()
    await _send(sessions, wa, NOW + timedelta(minutes=25))  # acaba em 5 min: não sai
    assert wa.sent == []
    async with sessions() as s:
        manual = (
            await s.execute(select(Publication).where(Publication.tier == "avulso"))
        ).scalar_one()
    assert manual.state == "skipped" and "relâmpago acabou" in (manual.note or "")


async def test_manual_link_form(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    await _approved_in_the_wrong_group(sessions)  # "Controle Redfin" pronto em Ferramentas
    async with sessions() as s:
        tools_ch, tech_ch = (await s.execute(select(Channel.id).order_by(Channel.id))).scalars()
    assert (await login(client, secret)).status_code == 303
    assert 'href="/postar/avulso"' in (await client.get("/postar")).text
    page = (await client.get("/postar/avulso")).text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    nonce = re.search(r'name="nonce" value="([^"]+)"', page)
    assert csrf is not None and nonce is not None
    assert f'name="channel_id" value="{tech_ch}"' in page
    form = {
        "csrf": csrf.group(1),
        "nonce": nonce.group(1),
        "url": PANELAS,
        "nome": "Jogo De Panelas Antiaderente",
        "preco": "308,00",
        "de": "699,00",
        "ultimas": "4",
        "relampago": "1",
        "acaba_em": "07:10:38",
        "channel_id": [str(tools_ch), str(tech_ch)],
    }

    short = await client.post("/postar/avulso", data=form | {"url": "https://meli.la/2xYz"})
    assert short.status_code == 422 and "não o encurtado" in short.text
    assert 'value="https://meli.la/2xYz"' in short.text and 'value="308,00"' in short.text
    no_time = await client.post("/postar/avulso", data=form | {"acaba_em": ""})
    assert no_time.status_code == 422 and "digite quanto falta" in no_time.text

    r = await client.post("/postar/avulso", data=form)
    assert r.headers["location"] == f"/postar?aviso=avulso#grupo-{tools_ch}"
    again = await client.post("/postar/avulso", data=form)  # o mesmo formulário de novo
    assert again.status_code == 303
    async with sessions() as s, s.begin():
        manual = (
            (await s.execute(select(Publication).where(Publication.tier == "avulso")))
            .scalars()
            .all()
        )
        assert sorted(p.channel_id for p in manual) == [tools_ch, tech_ch]  # sem duplicar
        assert all(p.flash_ends_at and p.state == "awaiting_link" for p in manual)
        for pub in (await s.execute(select(Publication))).scalars():
            pub.state, pub.affiliate_url = "ready", f"https://meli.la/{pub.id}"
    postar = (await client.get("/postar")).text
    tools = postar[postar.index(f'id="grupo-{tools_ch}"') :]
    assert tools.index("Jogo De Panelas") < tools.index("Controle Redfin")  # em 1º na fila
    assert "relâmpago até" in tools and ">avulso</span>" in tools


def test_photo_address_from_the_page_becomes_jpeg() -> None:
    page = "https://http2.mlstatic.com/D_NQ_NP_2X_889169-MLA96868779849_102025-F.webp?x=1"
    assert check_image_url(page) == (
        "https://http2.mlstatic.com/D_NQ_NP_2X_889169-MLA96868779849_102025-F.jpg"
    )
    assert check_image_url("  ") is None
    with pytest.raises(ManualError, match="mlstatic"):
        check_image_url("https://golpe.com/foto.jpg")


async def test_photo_can_be_set_on_a_queued_manual_post(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    pub_id, _tech, _tools = await _approved_in_the_wrong_group(sessions)  # post comum
    async with sessions() as s, s.begin():
        channel = (await s.execute(select(Channel).order_by(Channel.id))).scalars().first()
        assert channel is not None
        await queue_manual(s, _post([channel.id]), actor="dono", now=NOW)
        manual_id = (
            await s.execute(select(Publication.id).where(Publication.tier == "avulso"))
        ).scalar_one()
    assert (await login(client, secret)).status_code == 303
    page = (await client.get(f"/postar/{manual_id}")).text
    assert f'action="/postar/{manual_id}/foto"' in page and "Sem foto" in page
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    assert csrf is not None
    url = f"/postar/{manual_id}/foto"
    bad = await client.post(url, data={"csrf": csrf.group(1), "foto": "https://golpe.com/a.jpg"})
    assert bad.headers["location"] == f"/postar/{manual_id}?erro=foto#foto"
    webp = "https://http2.mlstatic.com/D_NQ_NP_2X_1-MLA1_1-F.webp"
    ok = await client.post(url, data={"csrf": csrf.group(1), "foto": webp})
    assert ok.headers["location"] == f"/postar/{manual_id}#foto"
    async with sessions() as s:
        product = (
            await s.execute(select(CatalogProduct).where(CatalogProduct.discovered_via == "avulso"))
        ).scalar_one()
    assert product.image_url == "https://http2.mlstatic.com/D_NQ_NP_2X_1-MLA1_1-F.jpg"
    # Só o produto do link avulso: a foto do catálogo vem da API e não se troca à mão.
    other = await client.post(f"/postar/{pub_id}/foto", data={"csrf": csrf.group(1), "foto": webp})
    assert other.status_code == 404
