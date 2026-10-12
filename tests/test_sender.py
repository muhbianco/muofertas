"""Envio automático (Fase 2c): aprovar → link montado → envio pelo chip, no máximo uma vez."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.distribution.links import resolve_links
from app.distribution.sender import send_due
from app.distribution.service import publications_for_approval
from app.domain.distribution import build_affiliate_url, pix_to_show
from app.integrations import mercadolivre as ml
from app.integrations.mercadolivre import MercadoLivreClient
from app.integrations.ml_affiliate import AffiliatePortal
from app.integrations.wuzapi import WuzapiClient
from app.models import (
    CatalogProduct,
    Channel,
    MarketplaceSeller,
    Niche,
    OfferEvaluation,
    OperatorDecision,
    Publication,
)
from tests.fake_ml import FakeML
from tests.fake_wuzapi import ADMIN, SESSION, FakeWuzapi

ITEM = "MLB7083641270"
GROUP = "120363333333333333@g.us"
# 15h em Brasília: dentro da janela padrão (8h às 22h)
NOW = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ml, "_backoff", lambda attempt: 0.0)


def test_affiliate_url_and_pix_window() -> None:
    url = build_affiliate_url(
        "https://www.mercadolivre.com.br/p/MLB1?pdp_filters=item_id:MLB9",
        tag="megapromooz",
        tool_id="19964746",
    )
    assert url.endswith(
        "?pdp_filters=item_id:MLB9&matt_word=megapromooz&matt_tool=19964746&forceInApp=true"
    )
    assert pix_to_show(4950, NOW, 5500, NOW + timedelta(hours=2)) == 4950
    assert pix_to_show(4950, NOW, 5500, NOW + timedelta(hours=4)) is None  # venceu
    assert pix_to_show(4950, NOW, 4900, NOW) is None  # preço caiu abaixo do Pix


def cfg() -> Settings:
    return Settings(
        database_url_override="sqlite+aiosqlite://",
        ml_client_id="x",
        ml_client_secret=SecretStr("y"),
        ml_requests_per_second=1000.0,
        wuzapi_base_url="http://wuzapi.test",
        wuzapi_admin_token=SecretStr(ADMIN),
        wuzapi_session_token=SecretStr(SESSION),
        ml_affiliate_tool_id="19964746",
        ml_session_file="/nao/existe/ml-session",  # sem sessão do portal: link longo
    )


async def approve(
    sessions: async_sessionmaker[AsyncSession],
    *,
    transport: str = "wuzapi",
    pix: int | None = None,
    last_units: int | None = None,
) -> int:
    async with sessions() as session, session.begin():
        niche = Niche(slug="tenis-esporte", name="Tênis e Esporte", active=True, created_at=NOW)
        session.add(niche)
        await session.flush()
        session.add(
            Channel(
                niche_id=niche.id,
                name="MegaPromoOz - Tênis e Esportes",
                transport=transport,
                ml_tag="megapromooz",
                wa_jid=GROUP,
                max_per_day=10,
                min_interval_minutes=40,
                window_start_hour=8,
                window_end_hour=22,
                paused=False,
                created_at=NOW,
            )
        )
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLBP1",
            niche_id=niche.id,
            name="Bicicleta Ergométrica Bike Spinning 6kg",
            discovered_via="highlights",
            active=True,
            tier="warm",
            next_check_at=NOW,
            consecutive_failures=0,
            first_seen_at=NOW,
        )
        session.add(product)
        await session.flush()
        ev = OfferEvaluation(
            product_id=product.id,
            evaluated_at=NOW,
            trigger="price_change",
            ruleset="v2",
            tier="boa",
            score=70.0,
            price_cents=44990,
            used_official=True,
            snapshot=json.dumps({"offer_item_id": ITEM}),
        )
        session.add(ev)
        decision = OperatorDecision(
            product_id=product.id, decision="approved", actor="dono", decided_at=NOW
        )
        session.add(decision)
        await session.flush()
        result = await publications_for_approval(
            session,
            product=product,
            evaluation=ev,
            decision=decision,
            now=NOW,
            pix_cents=pix,
            affiliate_tool_id="19964746",
            last_units=last_units,
        )
    assert result.created == 1 and result.auto_link == 1
    async with sessions() as session:
        pub = (await session.execute(select(Publication))).scalar_one()
    assert pub.state == "awaiting_link" and pub.affiliate_url is None  # o link é do worker
    portal = AffiliatePortal(cfg())
    try:
        assert await resolve_links(sessions, portal, cfg(), NOW, pause=0) == 1
    finally:
        await portal.aclose()
    async with sessions() as session:
        pub = (await session.execute(select(Publication))).scalar_one()
    assert pub.state == "ready" and pub.affiliate_url and "matt_tool=19964746" in pub.affiliate_url
    return pub.id


async def run(
    sessions: async_sessionmaker[AsyncSession],
    wa: FakeWuzapi,
    *,
    price: float = 449.90,
    now: datetime = NOW,
    others: list[dict[str, object]] | None = None,
) -> int:
    offers = [{"item_id": ITEM, "price": price, "currency_id": "BRL"}, *(others or [])]
    fake_ml = FakeML(offers={"MLBP1": offers})
    ml_client = MercadoLivreClient(cfg(), transport=fake_ml.transport())
    wa_client = WuzapiClient(cfg(), transport=wa.transport())
    try:
        return await send_due(sessions, ml_client, wa_client, now)
    finally:
        await ml_client.aclose()
        await wa_client.aclose()


async def state(sessions: async_sessionmaker[AsyncSession]) -> Publication:
    async with sessions() as session:
        return (await session.execute(select(Publication))).scalar_one()


async def test_approved_offer_goes_to_the_group_once(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions, pix=40490)
    wa = FakeWuzapi()
    assert await run(sessions, wa) == 1
    assert await run(sessions, wa, now=NOW + timedelta(minutes=1)) == 0  # já foi
    assert len(wa.sent) == 1
    sent = wa.sent[0]
    assert sent["Phone"] == GROUP and sent["LinkPreview"] is False
    assert sent["Body"].startswith("#publi · ")
    assert "⚡ *R$ 404,90 no Pix*" in sent["Body"]
    assert "matt_word=megapromooz&matt_tool=19964746" in sent["Body"]
    pub = await state(sessions)
    assert (pub.state, pub.actor, pub.external_message_id) == ("sent", "envio automático", "MSG1")


async def test_common_seller_post_compares_with_the_official_store(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions)
    async with sessions() as session, session.begin():
        ev = (await session.execute(select(OfferEvaluation))).scalar_one()
        ev.used_official = False
        ev.snapshot = json.dumps({"offer_item_id": ITEM, "offer_seller_id": 7})
        session.add(
            MarketplaceSeller(
                marketplace="mercadolivre",
                external_id=7,
                level_id="5_green",
                transactions_total=2211,
                checked_at=NOW,
            )
        )
    wa = FakeWuzapi()
    official = {"item_id": "MLB999", "price": 1049.30, "currency_id": "BRL", "official_store_id": 3}
    assert await run(sessions, wa, others=[official]) == 1
    body = wa.sent[0]["Body"]
    assert "🔥 *57% mais barato que a loja oficial*" in body
    assert "🏬 Na loja oficial: ~R$ 1.049,30~" in body
    assert "💰 Aqui: *R$ 449,90* no Mercado Livre" in body
    assert "👍 Vendedor com reputação verde e 2.211 vendas" in body
    assert (await state(sessions)).official_price_cents == 104930


async def test_last_units_go_in_the_post_while_fresh(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions, last_units=4)
    wa = FakeWuzapi()
    assert await run(sessions, wa) == 1
    assert "⏳ *Últimas 4 unidades!*" in wa.sent[0]["Body"]


async def test_last_units_expire_after_two_hours(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions, last_units=4)
    wa = FakeWuzapi()
    assert await run(sessions, wa, now=NOW + timedelta(hours=3)) == 1
    assert "⏳" not in wa.sent[0]["Body"]


async def test_price_up_and_window_and_manual_groups_do_not_send(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions)
    wa = FakeWuzapi()
    night = datetime(2026, 10, 22, 2, 0, tzinfo=UTC)  # 23h em Brasília
    assert await run(sessions, wa, now=night) == 0 and not wa.sent
    assert await run(sessions, wa, price=479.90) == 0  # subiu 6,7%
    pub = await state(sessions)
    assert pub.state == "skipped" and "subiu" in (pub.note or "") and not wa.sent


async def test_manual_group_is_never_sent_automatically(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions, transport="manual")
    wa = FakeWuzapi()
    assert await run(sessions, wa) == 0 and not wa.sent


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("timeout", "unknown"), ("refuse", "failed"), ("connect", "ready")],
)
async def test_send_failures_never_resend_blindly(
    sessions: async_sessionmaker[AsyncSession], mode: str, expected: str
) -> None:
    await approve(sessions)
    wa = FakeWuzapi(send_mode=mode)
    assert await run(sessions, wa) == 0
    pub = await state(sessions)
    assert pub.state == expected
    # incerto ou recusado: a próxima rodada não tenta de novo sozinha
    wa.send_mode = "ok"
    again = await run(sessions, wa, now=NOW + timedelta(minutes=5))
    assert again == (1 if expected == "ready" else 0)


async def test_interrupted_send_becomes_unknown(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    pub_id = await approve(sessions)
    async with sessions() as session, session.begin():
        pub = await session.get(Publication, pub_id)
        assert pub is not None
        pub.state, pub.sending_at = "sending", NOW - timedelta(minutes=11)
    wa = FakeWuzapi()
    assert await run(sessions, wa) == 0 and not wa.sent
    assert (await state(sessions)).state == "unknown"


async def test_panel_approval_with_pix_and_group_checks(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import re

    from app.core.config import settings
    from tests.test_panel import login, seed_admin

    monkeypatch.setattr(settings, "ml_affiliate_tool_id", "19964746")
    secret = await seed_admin(sessions)
    pub_id = await approve(sessions, transport="manual")
    async with sessions() as session, session.begin():
        # recomeça do zero o produto aprovado no helper: a aprovação agora vem pelo painel
        pub = await session.get(Publication, pub_id)
        assert pub is not None
        await session.delete(pub)
        channel = (await session.execute(select(Channel))).scalar_one()
        channel.wa_jid = None
        product = (await session.execute(select(CatalogProduct))).scalar_one()
        product.last_tier, product.last_score = "boa", 70.0
        ev_id = (await session.execute(select(OfferEvaluation.id))).scalar_one()
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/grupos")).text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    assert csrf is not None
    token = csrf.group(1)

    form = {
        "csrf": token,
        "name": channel.name,
        "niche_id": channel.niche_id,
        "ml_tag": "megapromooz",
        "max_per_day": 10,
        "min_interval_minutes": 40,
        "window_start_hour": 8,
        "window_end_hour": 22,
        "transport": "wuzapi",
    }
    refused = await client.post(f"/grupos/{channel.id}", data=form)
    assert refused.status_code == 422 and "grupo ligado ao WhatsApp" in refused.text

    base = {"csrf": token, "product_id": product.id, "evaluation_id": ev_id, "decision": "approved"}
    bad_coupon = await client.post("/decisao", data={**base, "cupom": "100,00"})
    assert bad_coupon.headers["location"].endswith("aviso=cupom")
    bad = await client.post("/decisao", data={**base, "pix": "500,00"})
    assert bad.headers["location"].endswith("aviso=pix")
    async with sessions() as session:
        assert (await session.execute(select(Publication))).scalar_one_or_none() is None
    ok = await client.post("/decisao", data={**base, "pix": "404,90", "cupom": "419,90"})
    assert ok.headers["location"].endswith("aviso=pronta")  # grupo manual, link já montado
    pub = await state(sessions)
    assert pub.state == "awaiting_link" and pub.pix_price_cents == 40490 and pub.pix_at is not None
    assert pub.coupon_price_cents == 41990 and pub.coupon_at is not None

    # envio incerto: o operador confere no grupo e marca
    async with sessions() as session, session.begin():
        row = await session.get(Publication, pub.id)
        assert row is not None
        row.state, row.note = "unknown", "o WhatsApp não confirmou o envio"
    assert "Conferir no grupo" in (await client.get("/postar")).text
    await client.post(f"/postar/{pub.id}/conferido", data={"csrf": token, "outcome": "saiu"})
    assert (await state(sessions)).state == "sent"


IMAGE_URL = "https://http2.mlstatic.com/D_NQ_NP_745785-MLA114987927277_072026-F.jpg"
JPEG = bytes.fromhex("ffd8ffe000104a46494600") + b"0" * 64


def image_server(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})


async def run_with_photo(
    sessions: async_sessionmaker[AsyncSession], wa: FakeWuzapi, image_url: str
) -> int:
    async with sessions() as session, session.begin():
        product = (await session.execute(select(CatalogProduct))).scalar_one()
        product.image_url = image_url
    fake_ml = FakeML(offers={"MLBP1": [{"item_id": ITEM, "price": 449.90, "currency_id": "BRL"}]})
    ml_client = MercadoLivreClient(cfg(), transport=fake_ml.transport())
    wa_client = WuzapiClient(cfg(), transport=wa.transport())
    images = httpx.AsyncClient(transport=httpx.MockTransport(image_server))
    try:
        return await send_due(sessions, ml_client, wa_client, NOW, images=images)
    finally:
        await ml_client.aclose()
        await wa_client.aclose()
        await images.aclose()


async def test_post_goes_as_product_photo_with_caption(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions)
    wa = FakeWuzapi()
    assert await run_with_photo(sessions, wa, IMAGE_URL) == 1
    (sent,) = wa.sent
    assert sent["Image"].startswith("data:image/jpeg;base64,")
    assert sent["Caption"].startswith("#publi · ") and "Body" not in sent
    assert (await state(sessions)).external_message_id == "IMG1"


async def test_refused_photo_falls_back_to_text_and_foreign_host_is_never_fetched(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions)
    wa = FakeWuzapi(image_mode="refuse")
    assert await run_with_photo(sessions, wa, IMAGE_URL) == 1
    (sent,) = wa.sent
    assert "Body" in sent and "Image" not in sent  # a foto não saiu; foi o texto

    from app.integrations.images import fetch_product_image

    async with httpx.AsyncClient(transport=httpx.MockTransport(image_server)) as http:
        assert await fetch_product_image(http, "https://evil.example/x.jpg") is None
        assert await fetch_product_image(http, "http://http2.mlstatic.com/x.jpg") is None
        assert (await fetch_product_image(http, IMAGE_URL) or "").startswith("data:image/jpeg")


async def test_resend_now_copies_and_sends_immediately(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    import re

    from app.main import app
    from app.panel.distribution_routes import get_ml_client
    from app.panel.whatsapp_routes import get_wa_client
    from tests.test_panel import login, seed_admin

    await approve(sessions)
    wa = FakeWuzapi()
    assert await run(sessions, wa) == 1
    fake_ml = FakeML(offers={"MLBP1": [{"item_id": ITEM, "price": 449.90, "currency_id": "BRL"}]})
    ml_client = MercadoLivreClient(cfg(), transport=fake_ml.transport())
    wa_client = WuzapiClient(cfg(), transport=wa.transport())
    app.dependency_overrides[get_ml_client] = lambda: ml_client
    app.dependency_overrides[get_wa_client] = lambda: wa_client
    try:
        secret = await seed_admin(sessions)
        assert (await login(client, secret)).status_code == 303
        page = (await client.get("/postar")).text
        assert "Reenviar agora" in page
        token = re.search(r'name="csrf" value="([^"]+)"', page)
        assert token is not None
        async with sessions() as session:
            original = (await session.execute(select(Publication))).scalar_one()
        r = await client.post(f"/postar/{original.id}/reenviar", data={"csrf": token.group(1)})
        assert r.status_code == 303
    finally:
        await ml_client.aclose()
        await wa_client.aclose()
    assert len(wa.sent) == 2  # dentro do intervalo de 40 min, mas foi pedido explícito
    async with sessions() as session:
        rows = list((await session.execute(select(Publication).order_by(Publication.id))).scalars())
    assert [r.state for r in rows] == ["sent", "sent"]
    assert rows[1].idempotency_key == rows[0].idempotency_key + ":r1"


async def test_post_shows_listing_discount_from_the_linked_offer(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await approve(sessions)
    wa = FakeWuzapi()
    fake_ml = FakeML(
        offers={
            "MLBP1": [
                {"item_id": ITEM, "price": 449.90, "original_price": 599.90, "currency_id": "BRL"}
            ]
        }
    )
    ml_client = MercadoLivreClient(cfg(), transport=fake_ml.transport())
    wa_client = WuzapiClient(cfg(), transport=wa.transport())
    try:
        assert await send_due(sessions, ml_client, wa_client, NOW) == 1
    finally:
        await ml_client.aclose()
        await wa_client.aclose()
    body = wa.sent[0]["Body"]
    assert "🔥 *25% OFF*" in body and "De ~R$ 599,90~ por *R$ 449,90*" in body
    assert (await state(sessions)).original_price_cents == 59990


async def test_post_shows_coupon_price_from_approval(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    pub_id = await approve(sessions)
    async with sessions() as session, session.begin():
        pub = await session.get(Publication, pub_id)
        assert pub is not None
        pub.coupon_price_cents, pub.coupon_at = 42990, NOW - timedelta(minutes=30)
    wa = FakeWuzapi()
    assert await run(sessions, wa) == 1
    assert "🎟️ *R$ 429,90 com cupom da loja* (ative na página)" in wa.sent[0]["Body"]


async def test_send_now_skips_the_interval_and_never_sends_twice(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    from app.distribution.sender import send_now
    from app.distribution.service import DistributionError

    pub_id = await approve(sessions)
    async with sessions() as session, session.begin():
        pub = await session.get(Publication, pub_id)
        assert pub is not None
        # Outro post saiu há 1 minuto: o robô esperaria o intervalo do grupo.
        session.add(
            Publication(
                channel_id=pub.channel_id,
                product_id=pub.product_id,
                idempotency_key="anterior",
                dedup_key="anterior",
                state="sent",
                tier="boa",
                target_url=pub.target_url,
                approved_price_cents=pub.approved_price_cents,
                created_at=NOW - timedelta(hours=1),
                sent_at=NOW - timedelta(minutes=1),
            )
        )
    wa = FakeWuzapi()
    assert await run(sessions, wa) == 0  # o automático respeita o intervalo
    fake_ml = FakeML(offers={"MLBP1": [{"item_id": ITEM, "price": 449.90, "currency_id": "BRL"}]})
    ml_client = MercadoLivreClient(cfg(), transport=fake_ml.transport())
    wa_client = WuzapiClient(cfg(), transport=wa.transport())
    try:
        assert await send_now(
            sessions, ml_client, wa_client, publication_id=pub_id, actor="dono", now=NOW
        )
        with pytest.raises(DistributionError, match="pronta"):
            await send_now(
                sessions, ml_client, wa_client, publication_id=pub_id, actor="dono", now=NOW
            )
    finally:
        await ml_client.aclose()
        await wa_client.aclose()
    assert len(wa.sent) == 1
    async with sessions() as session:
        sent = await session.get(Publication, pub_id)
    assert sent is not None and (sent.state, sent.actor) == ("sent", "dono")


async def test_send_now_refuses_a_manual_group(sessions: async_sessionmaker[AsyncSession]) -> None:
    from app.distribution.sender import send_now
    from app.distribution.service import DistributionError

    async with sessions() as session, session.begin():
        niche = Niche(slug="beleza", name="Beleza", active=True, created_at=NOW)
        session.add(niche)
        await session.flush()
        channel = Channel(niche_id=niche.id, name="Manual", transport="manual", created_at=NOW)
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLBM",
            niche_id=niche.id,
            name="Batom",
            discovered_via="highlights",
            active=True,
            tier="warm",
            next_check_at=NOW,
            consecutive_failures=0,
            first_seen_at=NOW,
        )
        session.add_all([channel, product])
        await session.flush()
        pub = Publication(
            channel_id=channel.id,
            product_id=product.id,
            idempotency_key="m1",
            dedup_key="m1",
            state="ready",
            tier="boa",
            target_url="https://www.mercadolivre.com.br/p/x",
            approved_price_cents=1000,
            created_at=NOW,
        )
        session.add(pub)
        await session.flush()
        pub_id = pub.id
    wa_client = WuzapiClient(cfg(), transport=FakeWuzapi().transport())
    ml_client = MercadoLivreClient(cfg(), transport=FakeML().transport())
    try:
        with pytest.raises(DistributionError, match="manual"):
            await send_now(
                sessions, ml_client, wa_client, publication_id=pub_id, actor="dono", now=NOW
            )
    finally:
        await ml_client.aclose()
        await wa_client.aclose()


async def test_offer_waits_a_few_minutes_after_an_invite(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    from app.models import ChannelInvite

    pub_id = await approve(sessions)
    async with sessions() as session, session.begin():
        pub = await session.get(Publication, pub_id)
        assert pub is not None
        session.add(
            ChannelInvite(
                channel_id=pub.channel_id,
                local_date=NOW.date(),
                slot="noite",
                kind="geral",
                message="convite",
                state="sent",
                created_at=NOW - timedelta(minutes=1),
                sent_at=NOW - timedelta(minutes=1),
            )
        )
    wa = FakeWuzapi()
    assert await run(sessions, wa) == 0  # o convite acabou de sair
    assert await run(sessions, wa, now=NOW + timedelta(minutes=5)) == 1
