"""Fluxo manual inteiro: grupo → aprovar → links do ML → revalidar → postar (uma vez só)."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import timedelta
from urllib.parse import unquote

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.core.config import Settings
from app.integrations import mercadolivre as ml
from app.integrations.mercadolivre import MercadoLivreClient
from app.main import app
from app.models import CatalogProduct, Channel, Niche, OfferEvaluation, Publication
from app.panel.distribution_routes import get_ml_client
from tests.fake_ml import FakeML
from tests.test_panel import login, seed_admin

ITEM = "MLB7083641270"


@pytest.fixture
async def fake(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeML]:
    monkeypatch.setattr(ml, "_backoff", lambda attempt: 0.0)
    fake = FakeML(offers={"MLBP1": [{"item_id": ITEM, "price": 1299.0, "currency_id": "BRL"}]})
    ml_client = MercadoLivreClient(settings, transport=fake.transport())
    app.dependency_overrides[get_ml_client] = lambda: ml_client
    yield fake
    await ml_client.aclose()


async def seed(sessions: async_sessionmaker[AsyncSession], *, tier: str = "excelente") -> int:
    now = utcnow()
    async with sessions() as session, session.begin():
        niche = Niche(slug="observacao", name="Observação", active=True, created_at=now)
        session.add(niche)
        await session.flush()
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLBP1",
            niche_id=niche.id,
            name="Galaxy A17 256 GB",
            discovered_via="highlights",
            active=True,
            tier="warm",
            next_check_at=now,
            consecutive_failures=0,
            first_seen_at=now - timedelta(days=20),
            last_changed_at=now - timedelta(hours=2),
            last_tier=tier,
            last_score=80.0,
        )
        session.add(product)
        await session.flush()
        session.add(evaluation(product.id, tier))
        return product.id


def evaluation(product_id: int, tier: str, price: int = 129900) -> OfferEvaluation:
    return OfferEvaluation(
        product_id=product_id,
        evaluated_at=utcnow(),
        trigger="price_change",
        ruleset="v2-2026-10-10",
        tier=tier,
        score=80.0,
        price_cents=price,
        used_official=False,
        snapshot=json.dumps({"tier": tier, "offer_item_id": ITEM, "offer_kind": "confiavel"}),
    )


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


async def create_group(client: httpx.AsyncClient, csrf: str, niche_id: int) -> httpx.Response:
    return await client.post(
        "/grupos",
        data={
            "csrf": csrf,
            "name": "Ofertas Observação",
            "niche_id": niche_id,
            "ml_tag": "obs1",
            "invite_url": "https://chat.whatsapp.com/AbCdEf",
            "max_per_day": 10,
            "min_interval_minutes": 5,
            "window_start_hour": 0,
            "window_end_hour": 24,  # o teste não depende da hora em que roda
        },
    )


async def approve(client: httpx.AsyncClient, csrf: str, product_id: int, ev_id: int) -> str:
    r = await client.post(
        "/decisao",
        data={
            "csrf": csrf,
            "product_id": product_id,
            "evaluation_id": ev_id,
            "decision": "approved",
        },
    )
    assert r.status_code == 303
    return r.headers["location"]


async def test_manual_flow_from_approval_to_posted_once(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession], fake: FakeML
) -> None:
    secret = await seed_admin(sessions)
    product_id = await seed(sessions)
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/grupos")).text)
    async with sessions() as session:
        niche_id = (await session.execute(select(Niche.id))).scalar_one()
        ev_id = (await session.execute(select(OfferEvaluation.id))).scalar_one()

    bad = await client.post(
        "/grupos",
        data={
            "csrf": csrf,
            "name": "x",
            "niche_id": niche_id,
            "ml_tag": "Com Espaço",
            "max_per_day": 10,
            "min_interval_minutes": 40,
            "window_start_hour": 8,
            "window_end_hour": 22,
        },
    )
    assert bad.status_code == 422 and "etiqueta do ML" in bad.text
    assert (await create_group(client, csrf, niche_id)).status_code == 303

    # aprovar: vira publicação esperando link; aprovar de novo não duplica; some da fila
    assert (await approve(client, csrf, product_id, ev_id)).endswith("aviso=links")
    assert "aviso" not in await approve(client, csrf, product_id, ev_id)
    assert "Galaxy A17" not in (await client.get("/")).text
    async with sessions() as session:
        pub = (await session.execute(select(Publication))).scalar_one()
    assert pub.state == "awaiting_link"
    assert pub.target_url.endswith(f"pdp_filters=item_id:{ITEM}")

    # links: contagem errada é recusada; o curto oficial passa
    page = await client.get("/links")
    assert "obs1" in page.text and pub.target_url in page.text
    wrong = await client.post(
        f"/links/{pub.channel_id}",
        data={
            "csrf": csrf,
            "publication_id": [pub.id],
            "links": "https://meli.la/a\nhttps://meli.la/b",
        },
    )
    assert wrong.status_code == 422 and "vieram 2 links para 1" in wrong.text
    ok = await client.post(
        f"/links/{pub.channel_id}",
        data={"csrf": csrf, "publication_id": [pub.id], "links": "https://meli.la/2AbC9"},
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/postar"

    # postar: sem revalidar não há mensagem; revalidar lê a oferta do link e monta o texto
    detail = await client.get(f"/postar/{pub.id}")
    assert "Revalidar preço" in detail.text and "wa.me" not in detail.text
    r = await client.post(f"/postar/{pub.id}/revalidar", data={"csrf": csrf})
    assert r.status_code == 303
    assert fake.calls.count("/products/MLBP1/items") == 1
    detail = (await client.get(f"/postar/{pub.id}")).text
    share = re.search(r'href="(https://wa\.me/\?text=[^"]+)"', detail)
    assert share is not None
    text = unquote(share.group(1).replace("&amp;", "&").removeprefix("https://wa.me/?text="))
    assert text.startswith("#publi · 🛍️ *OBSERVAÇÃO*\n")
    assert "💰 *R$ 1.299,00* no Mercado Livre" in text and "👉 https://meli.la/2AbC9" in text

    # Pix visto na página pelo operador: entra na mensagem; valor absurdo é recusado
    bad_pix = await client.post(f"/postar/{pub.id}/pix", data={"csrf": csrf, "pix": "500,00"})
    assert bad_pix.headers["location"].endswith("erro=pix")
    await client.post(f"/postar/{pub.id}/pix", data={"csrf": csrf, "pix": "1.169,10"})
    detail = (await client.get(f"/postar/{pub.id}")).text
    assert "⚡ *R$ 1.169,10 no Pix*" in detail
    assert 'value="1169,10"' in detail

    # postei: uma vez só
    for _ in range(2):
        assert (
            await client.post(f"/postar/{pub.id}/postei", data={"csrf": csrf})
        ).status_code == 303
    async with sessions() as session:
        sent = (await session.execute(select(Publication))).scalar_one()
        count = (await session.execute(select(func.count()).select_from(Publication))).scalar_one()
    assert sent.state == "sent" and sent.actor == "dono" and count == 1

    # nova avaliação do mesmo produto em 7 dias, sem queda de 10%: não volta ao grupo
    async with sessions() as session, session.begin():
        session.add(evaluation(product_id, "excelente", price=125000))
        product = await session.get(CatalogProduct, product_id)
        assert product is not None
        product.last_changed_at = utcnow()
    async with sessions() as session:
        ev2 = (await session.execute(select(func.max(OfferEvaluation.id)))).scalar_one()
    assert (await approve(client, csrf, product_id, ev2)).endswith("aviso=repetida")


async def test_price_up_blocks_the_post(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession], fake: FakeML
) -> None:
    secret = await seed_admin(sessions)
    product_id = await seed(sessions)
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/grupos")).text)
    async with sessions() as session:
        niche_id = (await session.execute(select(Niche.id))).scalar_one()
        ev_id = (await session.execute(select(OfferEvaluation.id))).scalar_one()
    await create_group(client, csrf, niche_id)
    await approve(client, csrf, product_id, ev_id)
    async with sessions() as session:
        pub = (await session.execute(select(Publication))).scalar_one()
    await client.post(
        f"/links/{pub.channel_id}",
        data={"csrf": csrf, "publication_id": [pub.id], "links": "https://meli.la/x"},
    )
    fake.offers["MLBP1"] = [{"item_id": ITEM, "price": 1399.0, "currency_id": "BRL"}]
    await client.post(f"/postar/{pub.id}/revalidar", data={"csrf": csrf})
    detail = (await client.get(f"/postar/{pub.id}")).text
    assert "o preço subiu mais de 3%" in detail and "wa.me" not in detail
    refused = await client.post(f"/postar/{pub.id}/descartar", data={"csrf": "errado"})
    assert refused.status_code == 403


async def test_approval_without_group_warns(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    product_id = await seed(sessions)
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/")).text)
    async with sessions() as session:
        ev_id = (await session.execute(select(OfferEvaluation.id))).scalar_one()
        groups = (await session.execute(select(func.count()).select_from(Channel))).scalar_one()
    assert groups == 0
    location = await approve(client, csrf, product_id, ev_id)
    assert location.endswith("aviso=sem-grupo")
    assert "não tem grupo cadastrado" in (await client.get(location)).text
    # o caso de 10/10/2026: aprovou antes de cadastrar o grupo. O produto não some da fila;
    # cadastrado o grupo, aprovar de novo leva para Links.
    assert "Galaxy A17" in (await client.get("/")).text
    async with sessions() as session:
        niche_id = (await session.execute(select(Niche.id))).scalar_one()
    await create_group(client, csrf, niche_id)
    assert (await approve(client, csrf, product_id, ev_id)).endswith("aviso=links")
    assert "Galaxy A17" not in (await client.get("/")).text


def _group_form(csrf: str, niche_id: int, **extra: object) -> dict[str, object]:
    return {
        "csrf": csrf,
        "name": "MegaPromoOz - Tecnologia",
        "niche_id": niche_id,
        "max_per_day": 20,
        "window_start_hour": 7,
        "window_end_hour": 22,
        **extra,
    }


async def test_interval_is_computed_and_shown_read_only(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    await seed(sessions)
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/grupos")).text)
    async with sessions() as session:
        niche_id = (await session.execute(select(Niche.id))).scalar_one()

    r = await client.post("/grupos", data=_group_form(csrf, niche_id))
    assert r.status_code == 303
    async with sessions() as session:
        channel = (await session.execute(select(Channel))).scalar_one()
    assert (channel.max_per_day, channel.min_interval_minutes) == (20, 45)
    page = (await client.get("/grupos")).text
    assert 'readonly tabindex="-1" aria-readonly="true" value="45 min"' in page
    assert 'name="min_interval_minutes"' not in page

    too_many = await client.post(
        "/grupos", data=_group_form(csrf, niche_id, name="Outro", window_end_hour=8, max_per_day=16)
    )
    assert too_many.status_code == 422
    assert "cabem no máximo 15 envios por dia" in too_many.text  # 60 min ÷ 4 min
    over = await client.post(
        "/grupos", data=_group_form(csrf, niche_id, name="Outro", max_per_day=201)
    )
    assert over.status_code == 422 and "de 1 a 200" in over.text


async def test_new_group_with_auto_send_explains_instead_of_crashing(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Regressão de 11/10/2026: o grupo vazio entrava na sessão antes da checagem e dava 500."""
    secret = await seed_admin(sessions)
    await seed(sessions)
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/grupos")).text)
    async with sessions() as session:
        niche_id = (await session.execute(select(Niche.id))).scalar_one()

    r = await client.post("/grupos", data=_group_form(csrf, niche_id, transport="wuzapi"))
    assert r.status_code == 422
    assert "No WhatsApp do chip" in r.text
    async with sessions() as session:
        assert (await session.execute(select(func.count()).select_from(Channel))).scalar_one() == 0
