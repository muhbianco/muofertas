from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.core.database import get_session
from app.main import app
from app.models import (
    AdminUser,
    CatalogProduct,
    MarketplaceSeller,
    Niche,
    OfferEvaluation,
    OperatorDecision,
    PriceObservation,
)
from app.panel import routes
from app.panel.security import (
    TOTP_STEP_SECONDS,
    LoginThrottle,
    hash_password,
    new_totp_secret,
    totp_at,
    verify_totp,
)

PASSWORD = "senha-forte-de-teste-123"


def test_totp_matches_rfc6238_vector() -> None:
    # RFC 6238, apêndice B: segredo ASCII "12345678901234567890", T=59 s, SHA1 → 94287082
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp_at(secret, 59 // 30, digits=8) == "94287082"


def test_totp_rejects_replay_and_garbage() -> None:
    secret = new_totp_secret()
    now = time.time()
    step = int(now // TOTP_STEP_SECONDS)
    code = totp_at(secret, step)
    assert verify_totp(secret, code, last_step=0, now=now) == step
    assert verify_totp(secret, code, last_step=step, now=now) is None  # replay
    assert verify_totp(secret, "12ab56", last_step=0, now=now) is None


def test_throttle_per_ip() -> None:
    t = LoginThrottle()
    for _ in range(5):
        assert t.allowed("1.1.1.1", now=100.0)
        t.failure("1.1.1.1", now=100.0)
    assert not t.allowed("1.1.1.1", now=101.0)
    assert t.allowed("2.2.2.2", now=101.0)
    assert t.allowed("1.1.1.1", now=100.0 + 16 * 60)  # janela andou


@pytest.fixture
async def client(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[httpx.AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    routes.throttle = LoginThrottle()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def seed_admin(sessions: async_sessionmaker[AsyncSession]) -> str:
    secret = new_totp_secret()
    async with sessions() as session, session.begin():
        session.add(
            AdminUser(
                username="dono",
                password_hash=hash_password(PASSWORD),
                totp_secret=secret,
                totp_last_step=0,
                active=True,
                created_at=utcnow(),
            )
        )
    return secret


async def seed_product(sessions: async_sessionmaker[AsyncSession]) -> int:
    now = utcnow()
    async with sessions() as session, session.begin():
        niche = Niche(slug="ferramentas", name="Ferramentas", active=True, created_at=now)
        session.add(niche)
        await session.flush()
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLBP1",
            niche_id=niche.id,
            name="Parafusadeira X",
            discovered_via="highlights",
            active=True,
            tier="warm",
            next_check_at=now,
            consecutive_failures=0,
            first_seen_at=now,
            last_tier="boa",
            last_score=62.0,
        )
        session.add(product)
        await session.flush()
        return product.id


async def login(c: httpx.AsyncClient, secret: str, *, step_offset: int = 0) -> httpx.Response:
    code = totp_at(secret, int(time.time() // TOTP_STEP_SECONDS) + step_offset)
    return await c.post("/login", data={"username": "dono", "password": PASSWORD, "code": code})


async def test_pages_require_login(client: httpx.AsyncClient) -> None:
    for path in ("/", "/nichos", "/saude", "/produto/1", "/health/collector"):
        r = await client.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/login", path
    assert (await client.get("/health/live")).status_code == 200


async def test_login_flow_and_replay(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    bad = await client.post(
        "/login", data={"username": "dono", "password": "errada-123456", "code": "000000"}
    )
    assert bad.status_code == 401
    ok = await login(client, secret)
    assert ok.status_code == 303 and "mpo_session" in ok.headers.get("set-cookie", "")
    assert (
        "httponly" in ok.headers["set-cookie"].lower()
        and "samesite=strict" in ok.headers["set-cookie"].lower()
    )
    page = await client.get("/")
    assert page.status_code == 200 and "Fila de aprovação" in page.text
    assert "Content-Security-Policy" in page.headers
    # o mesmo código de novo (outra aba, outro aparelho): recusado
    client.cookies.clear()
    assert (await login(client, secret)).status_code == 401


async def test_decision_needs_csrf_and_blocks_product(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    product_id = await seed_product(sessions)
    assert (await login(client, secret)).status_code == 303
    page = await client.get("/")
    assert "Parafusadeira X" in page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert csrf is not None

    no_csrf = await client.post("/decisao", data={"product_id": product_id, "decision": "blocked"})
    assert no_csrf.status_code == 403

    r = await client.post(
        "/decisao",
        data={
            "csrf": csrf.group(1),
            "product_id": product_id,
            "decision": "blocked",
            "next": "//evil.example",
        },
    )
    assert r.status_code == 303 and r.headers["location"] == "/"  # redirect aberto recusado
    async with sessions() as session:
        decision = (await session.execute(select(OperatorDecision))).scalar_one()
        product = await session.get(CatalogProduct, product_id)
    assert decision.decision == "blocked" and decision.actor == "dono"
    assert product is not None and product.operator_blocked
    assert "Parafusadeira X" not in (await client.get("/")).text


async def test_login_throttle_returns_429(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await seed_admin(sessions)
    for _ in range(5):
        r = await client.post(
            "/login", data={"username": "dono", "password": "x" * 12, "code": "123456"}
        )
        assert r.status_code == 401
    r = await client.post(
        "/login", data={"username": "dono", "password": "x" * 12, "code": "123456"}
    )
    assert r.status_code == 429


async def test_card_shows_where_the_price_comes_from(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Galaxy A17 de 10/10/2026: o preço é do vendedor verde (R$ 1.299), não do sem reputação.
    secret = await seed_admin(sessions)
    product_id = await seed_product(sessions)
    now = utcnow()
    async with sessions() as session, session.begin():
        product = await session.get(CatalogProduct, product_id)
        assert product is not None
        product.last_checked_at = now
        session.add(
            MarketplaceSeller(
                marketplace="mercadolivre",
                external_id=2,
                nickname="LOJA_BOA",
                level_id="5_green",
                power_seller_status="silver",
                transactions_total=3520,
                checked_at=now,
            )
        )
        session.add(
            PriceObservation(
                product_id=product_id,
                observed_at=now,
                kind="change",
                currency="BRL",
                min_price_cents=117040,
                min_official_price_cents=149900,
                offers_count=12,
                official_offers_count=1,
                deal_offers_count=0,
                free_shipping_offers_count=12,
                min_offer_item_id="MLB1",
                min_offer_seller_id=1,
                min_offer_original_price_cents=None,
                min_offer_free_shipping=True,
                eligible_min_price_cents=129900,
                eligible_item_id="MLB7083641270",
                eligible_seller_id=2,
                eligible_offers_count=6,
            )
        )
        snapshot = {
            "tier": "boa",
            "gates_failed": [],
            "signals": [],
            "notes": ["há oferta 10% mais barata de vendedor não elegível (ignorada)"],
            "offer_kind": "elegivel",
            "offer_item_id": "MLB7083641270",
            "offer_seller_id": 2,
            "used_official": False,
            "price_cents": 129900,
            "score": 62.0,
            "ruleset": "v2-2026-10-10",
            "components": {},
            "real_discount": None,
        }
        session.add(
            OfferEvaluation(
                product_id=product_id,
                evaluated_at=now,
                trigger="price_change",
                ruleset="v2-2026-10-10",
                tier="boa",
                score=62.0,
                price_cents=129900,
                used_official=False,
                snapshot=json.dumps(snapshot),
            )
        )
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/")).text
    assert "R$ 1.299,00" in page
    assert "LOJA_BOA · reputação verde · MercadoLíder · 3520 vendas" in page
    assert "pdp_filters=item_id:MLB7083641270" in page
    assert "10% mais barata de vendedor não elegível" in page
    detail = await client.get(f"/produto/{product_id}")
    assert detail.status_code == 200 and "LOJA_BOA" in detail.text
    assert "6/12 elegíveis" in detail.text
