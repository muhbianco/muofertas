from __future__ import annotations

import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import seed_niches
from app.collector.recategorize import recategorize
from app.core.clock import utcnow
from app.models import CatalogProduct, Channel, Niche, Publication
from tests.test_cli_seed import _niche, _niche_of, _product
from tests.test_panel import login, seed_admin, seed_product


async def _extra_niches(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    now = utcnow()
    async with sessions() as s, s.begin():
        sport = Niche(slug="tenis-esporte", name="Tênis e Esporte", active=True, created_at=now)
        parked = Niche(slug="observacao", name="Observação", active=False, created_at=now)
        s.add_all([sport, parked])
        await s.flush()
        return sport.id, parked.id


async def _publications(sessions: async_sessionmaker[AsyncSession], product_id: int) -> None:
    now = utcnow()
    async with sessions() as s, s.begin():
        product = await s.get(CatalogProduct, product_id)
        assert product is not None
        channel = Channel(
            niche_id=product.niche_id, name="Ferramentas", transport="wuzapi", created_at=now
        )
        s.add(channel)
        await s.flush()
        for n, state in enumerate(("ready", "sent")):
            s.add(
                Publication(
                    channel_id=channel.id,
                    product_id=product_id,
                    idempotency_key=f"k{n}",
                    dedup_key="d",
                    state=state,
                    tier="boa",
                    target_url="https://x",
                    approved_price_cents=10000,
                    created_at=now,
                    sent_at=now if state == "sent" else None,
                )
            )


async def test_queue_moves_product_to_another_niche(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    product_id = await seed_product(sessions)
    sport_id, parked_id = await _extra_niches(sessions)
    assert (await login(client, secret)).status_code == 303

    page = (await client.get("/")).text
    assert f'action="/produto/{product_id}/nicho"' in page
    assert ">Tênis e Esporte</option>" in page
    assert ">Observação</option>" not in page.split("trocar-nicho", 1)[1]  # nicho desligado
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    assert csrf is not None
    url = f"/produto/{product_id}/nicho"
    # Aprovado ontem, ainda na fila de envio do grupo de Ferramentas (e um envio antigo).
    await _publications(sessions, product_id)

    assert (await client.post(url, data={"niche_id": sport_id})).status_code == 403

    invalid = await client.post(url, data={"csrf": csrf.group(1), "niche_id": parked_id})
    assert invalid.headers["location"] == "/?aviso=nicho-invalido"

    r = await client.post(
        url, data={"csrf": csrf.group(1), "niche_id": sport_id, "next": "/?nicho=ferramentas"}
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/?nicho=ferramentas&aviso=nicho-fila"
    async with sessions() as s:
        product = await s.get(CatalogProduct, product_id)
        pubs = (await s.execute(select(Publication).order_by(Publication.id))).scalars().all()
    assert product is not None
    assert (product.niche_id, product.niche_locked, product.needs_evaluation) == (
        sport_id,
        True,
        True,
    )
    # O que esperava horário no grupo do nicho antigo sai; o que já foi enviado fica.
    assert [p.state for p in pubs] == ["discarded", "sent"]
    assert pubs[0].actor == "dono" and "Tênis e Esporte" in (pubs[0].note or "")

    detail = await client.get(f"/produto/{product_id}?aviso=nicho")
    assert "Nicho trocado." in detail.text
    assert re.search(rf'<option value="{sport_id}" selected>Tênis e Esporte', detail.text)

    missing = await client.post(
        "/produto/999999/nicho", data={"csrf": csrf.group(1), "niche_id": sport_id}
    )
    assert missing.status_code == 404


async def test_seed_does_not_move_a_product_the_operator_placed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_niches(
        {
            "nichos": [
                _niche("tenis-esporte", ["MLB1292"]),
                _niche("ferramentas", ["MLB5"]),
                _niche("beleza", ["MLB7"]),
            ]
        },
        sessions=sessions,
    )
    await _product(sessions, "MLBBOMBA", "tenis-esporte", "MLB1292")
    await _product(sessions, "MLBBIKE", "tenis-esporte", "MLB1292")
    async with sessions() as s, s.begin():
        pump = (
            await s.execute(select(CatalogProduct).where(CatalogProduct.external_id == "MLBBOMBA"))
        ).scalar_one()
        beauty = (await s.execute(select(Niche).where(Niche.slug == "beleza"))).scalar_one()
        moved = await recategorize(s, product_id=pump.id, niche_id=beauty.id, actor="dono")
    assert moved.changed and moved.niche == "Beleza" and moved.discarded == 0

    # Ciclismo muda de nicho no JSON: a bicicleta vai junto, a bomba fica onde o operador pôs.
    await seed_niches(
        {
            "nichos": [
                _niche("tenis-esporte", ["MLB6"]),
                _niche("ferramentas", ["MLB5", "MLB1292"]),
                _niche("beleza", ["MLB7"]),
            ]
        },
        sessions=sessions,
    )
    assert (await _niche_of(sessions, "MLBBIKE"))[0] == "ferramentas"
    assert (await _niche_of(sessions, "MLBBOMBA"))[0] == "beleza"
