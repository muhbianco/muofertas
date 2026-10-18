"""Link curto no avulso e no Vigiar (pedido do dono em 18/10/2026): pelo celular o app do ML só
compartilha o link curto. O servidor pergunta ao encurtador para onde ele aponta (uma requisição,
sem seguir e sem abrir a página do ML) e limpa o link; link curto de outro afiliado é recusado."""

from __future__ import annotations

import re
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.distribution.short_links import expand
from app.domain.distribution import LinkError, affiliate_redirect, link_in_text, short_link
from app.integrations import ml_shortlink
from app.main import app
from app.models import CatalogProduct, Channel, Niche, Publication
from app.panel.distribution_routes import get_short_client
from tests.test_panel import login, seed_admin

OURS = "19964746"
SOCIAL = (
    "https://www.mercadolivre.com.br/social/murbiancoferreira?matt_word=mpofamilia"
    "&matt_tool={tool}&forceInApp=true&ref=BC4Yb2xh"
)
PAGE = "https://www.mercadolivre.com.br/panela/p/MLB23951248?pdp_filters=item_id:MLB1"
FOTO = "https://http2.mlstatic.com/D_NQ_NP_2X_889169-MLA96868779849_102025-F.jpg"


def short_client(location: str | None, status: int = 301) -> Callable[[], httpx.AsyncClient]:
    """Encurtador falso: responde o redirecionamento e registra o que foi pedido."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        headers = {"location": location} if location else {}
        return httpx.Response(status, headers=headers, text="<html>não leia</html>")

    def build() -> httpx.AsyncClient:
        client = ml_shortlink.new_client(httpx.MockTransport(handle))
        client.seen = seen  # type: ignore[attr-defined]
        return client

    return build


@pytest.fixture
def ours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ml_affiliate_tool_id", OURS)


def test_the_link_inside_the_share_text() -> None:
    shared = "Olha que oferta no Mercado Livre! https://meli.la/2LZUDUb."
    assert link_in_text(shared) == "https://meli.la/2LZUDUb"
    assert link_in_text("  " + PAGE + "  ") == PAGE
    assert short_link("https://meli.la/2LZUDUb?x=1") == short_link("http://meli.la/2LZUDUb")
    link = short_link("https://mercadolivre.com/sec/1AbCd")
    assert link is not None and link.url == "https://mercadolivre.com/sec/1AbCd"
    assert short_link(PAGE) is None
    with pytest.raises(LinkError, match="formato"):
        short_link("https://meli.la/a/b/c")
    redirect = affiliate_redirect(SOCIAL.format(tool=OURS))
    assert redirect is not None
    assert (redirect.affiliate, redirect.tool_id, redirect.tag) == (
        "murbiancoferreira",
        OURS,
        "mpofamilia",
    )
    assert affiliate_redirect(PAGE) is None


async def test_expand_asks_the_shortener_once_and_never_opens_the_page() -> None:
    build = short_client(SOCIAL.format(tool=OURS))
    async with build() as client:
        own = await expand("https://meli.la/2LZUDUb", client=client, our_tool_id=OURS)
        seen = client.seen  # type: ignore[attr-defined]
    assert own.own_short is not None and own.url == "https://meli.la/2LZUDUb"
    assert [str(r.url) for r in seen] == ["https://meli.la/2LZUDUb"]  # não seguiu para o ML
    assert seen[0].headers["user-agent"].startswith("MegaPromoOz/")  # sem se passar por navegador

    async with short_client(SOCIAL.format(tool="111"))() as client:
        with pytest.raises(LinkError, match="de outro afiliado"):
            await expand("https://meli.la/2LZUDUb", client=client, our_tool_id=OURS)
    async with short_client(PAGE)() as client:  # link curto comum: aponta para a página
        plain = await expand(
            "veja https://mercadolivre.com/sec/1AbCd", client=client, our_tool_id=OURS
        )
    assert plain.url == PAGE and plain.own_short is None
    async with short_client(None, status=403)() as client:
        with pytest.raises(ml_shortlink.ShortLinkError, match="respondeu 403"):
            await expand("https://meli.la/2LZUDUb", client=client, our_tool_id=OURS)
    async with short_client(None)() as client:  # link comprido: nem chama o encurtador
        long = await expand(PAGE, client=client, our_tool_id=OURS)
        assert long.url == PAGE and client.seen == []  # type: ignore[attr-defined]


async def _groups(sessions: async_sessionmaker[AsyncSession]) -> list[int]:
    from app.core.clock import utcnow

    now = utcnow()
    async with sessions() as s, s.begin():
        niche = Niche(slug="casa-mimos", name="Casa", active=True, created_at=now)
        s.add(niche)
        await s.flush()
        channels = [
            Channel(
                niche_id=niche.id,
                name=f"Grupo {n}",
                transport="manual",
                ml_tag=f"g{n}",
                created_at=now,
            )
            for n in (1, 2)
        ]
        s.add_all(channels)
        await s.flush()
        return [c.id for c in channels]


async def test_own_short_link_goes_as_is_to_every_group(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession], ours: None
) -> None:
    secret = await seed_admin(sessions)
    groups = await _groups(sessions)
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/postar/avulso")).text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    nonce = re.search(r'name="nonce" value="([^"]+)"', page)
    assert csrf is not None and nonce is not None
    app.dependency_overrides[get_short_client] = short_client(SOCIAL.format(tool=OURS))
    form = {
        "csrf": csrf.group(1),
        "nonce": nonce.group(1),
        "url": "Achei no Mercado Livre: https://meli.la/2LZUDUb",
        "nome": "Panela Elétrica de Arroz Mondial",
        "preco": "119,90",
        "channel_id": [str(g) for g in groups],
    }
    # O produto não é conhecido (o link curto não diz qual é): sem foto não entra.
    no_photo = await client.post("/postar/avulso", data=form)
    assert no_photo.status_code == 422 and "foto: obrigatória" in no_photo.text
    ok = await client.post("/postar/avulso", data=form | {"foto": FOTO})
    assert ok.status_code == 303
    async with sessions() as s:
        product = (await s.execute(select(CatalogProduct))).scalar_one()
        pubs = (await s.execute(select(Publication).order_by(Publication.id))).scalars().all()
    assert product.external_id == "CURTO-2LZUDUb" and product.image_url == FOTO
    assert [(p.channel_id, p.state, p.affiliate_url) for p in pubs] == [
        (groups[0], "ready", "https://meli.la/2LZUDUb"),
        (groups[1], "ready", "https://meli.la/2LZUDUb"),
    ]


async def test_watch_explains_why_an_affiliate_short_link_cannot_be_watched(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession], ours: None
) -> None:
    secret = await seed_admin(sessions)
    await _groups(sessions)
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/vigiar")).text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    assert csrf is not None
    async with sessions() as s:
        niche_id = (await s.execute(select(Niche.id))).scalar_one()
    app.dependency_overrides[get_short_client] = short_client(SOCIAL.format(tool=OURS))
    r = await client.post(
        "/vigiar",
        data={"csrf": csrf.group(1), "url": "https://meli.la/2LZUDUb", "niche_id": niche_id},
    )
    assert r.status_code == 422 and "não diz qual é o produto" in r.text
