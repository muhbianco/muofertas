"""Fila de envio: o operador sobe um post e o robô obedece a ordem."""

from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.distribution.service import READY_ORDER, move_up
from app.models import CatalogProduct, Channel, Niche, Publication
from tests.test_panel import login, seed_admin


async def seed(sessions: async_sessionmaker[AsyncSession]) -> list[int]:
    now = utcnow()
    async with sessions() as session, session.begin():
        niche = Niche(slug="casa-mimos", name="Casa", active=True, created_at=now)
        session.add(niche)
        await session.flush()
        channel = Channel(niche_id=niche.id, name="Casa", transport="manual", created_at=now)
        session.add(channel)
        await session.flush()
        ids = []
        for n, name in enumerate(("Air fryer", "Panela", "Toalha")):
            product = CatalogProduct(
                marketplace="mercadolivre",
                external_id=f"MLBP{n}",
                niche_id=niche.id,
                name=name,
                discovered_via="highlights",
                active=True,
                tier="warm",
                next_check_at=now,
                consecutive_failures=0,
                first_seen_at=now,
            )
            session.add(product)
            await session.flush()
            pub = Publication(
                channel_id=channel.id,
                product_id=product.id,
                idempotency_key=f"k{n}",
                dedup_key=f"d{n}",
                state="ready",
                tier="boa",
                target_url="https://www.mercadolivre.com.br/p/x",
                approved_price_cents=10000,
                created_at=now + timedelta(minutes=n),
            )
            session.add(pub)
            await session.flush()
            ids.append(pub.id)
    return ids


async def order(sessions: async_sessionmaker[AsyncSession]) -> list[int]:
    async with sessions() as session:
        return list(
            (
                await session.execute(
                    select(Publication.id)
                    .where(Publication.state == "ready")
                    .order_by(*READY_ORDER)
                )
            ).scalars()
        )


async def test_move_up_only_goes_up_and_new_ones_stay_at_the_end(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    fryer, pan, towel = await seed(sessions)
    assert await order(sessions) == [fryer, pan, towel]  # ordem de aprovação

    async with sessions() as session, session.begin():
        assert await move_up(session, publication_id=towel, before_id=fryer)
    assert await order(sessions) == [towel, fryer, pan]

    async with sessions() as session, session.begin():
        # Descer não vale: a toalha (1ª) não vai para antes da panela (3ª).
        assert not await move_up(session, publication_id=towel, before_id=pan)
        assert await move_up(session, publication_id=pan, before_id=None)  # vira o próximo
    assert await order(sessions) == [pan, towel, fryer]

    # Aprovação nova entra no fim, depois das posicionadas.
    async with sessions() as session, session.begin():
        first = await session.get(Publication, fryer)
        assert first is not None
        session.add(
            Publication(
                channel_id=first.channel_id,
                product_id=first.product_id,
                idempotency_key="k9",
                dedup_key="d9",
                state="ready",
                tier="boa",
                target_url=first.target_url,
                approved_price_cents=10000,
                created_at=utcnow() - timedelta(days=1),
            )
        )
    assert (await order(sessions))[:3] == [pan, towel, fryer]


async def test_drag_form_and_buttons_on_the_post_page(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    fryer, _pan, towel = await seed(sessions)
    assert (await login(client, secret)).status_code == 303
    page = await client.get("/postar")
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    assert '<script src="/static/fila.js?v=6" defer></script>' in page.text
    assert page.text.index("Air fryer") < page.text.index("Toalha")
    assert "/enviar-agora" not in page.text  # grupo manual: posta pelo celular
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    r = await client.post(f"/postar/{towel}/subir", data={"csrf": csrf, "antes_de": fryer})
    assert r.status_code == 303 and r.headers["location"].startswith("/postar#grupo-")
    page = (await client.get("/postar")).text
    assert page.index("Toalha") < page.index("Air fryer") < page.index("Panela")
    assert (await client.get("/static/fila.js")).status_code == 200
