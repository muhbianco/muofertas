"""Filtro "Só os vigiados" (pedido do dono em 15/10/2026): o vigiado entra na fila assim que é
vigiado; depois de aprovado (ou rejeitado), só volta quando o preço cair abaixo do daquela decisão.
Em nenhuma outra condição: preço que sobe ou se repete não traz de volta."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import CatalogProduct, Niche, OfferEvaluation
from tests.test_distribution_panel import csrf_of
from tests.test_panel import login, seed_admin

WATCHED = "/?origem=vigiados"


async def _niche(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as s, s.begin():
        niche = Niche(slug="casa-mimos", name="Casa e mimos", active=True, created_at=utcnow())
        s.add(niche)
        await s.flush()
        return niche.id


async def _product(
    sessions: async_sessionmaker[AsyncSession],
    niche_id: int,
    name: str,
    *,
    via: str = "watchlist",
    tier: str | None = None,
) -> int:
    now = utcnow()
    async with sessions() as s, s.begin():
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id=f"MLB{abs(hash(name)) % 10**8}",
            niche_id=niche_id,
            name=name,
            discovered_via=via,
            active=True,
            tier="hot",
            next_check_at=now,
            consecutive_failures=0,
            first_seen_at=now - timedelta(days=3),
            last_changed_at=now - timedelta(days=1),
            last_tier=tier,
            last_score=50.0 if tier else None,
        )
        s.add(product)
        await s.flush()
        return product.id


async def _read(
    sessions: async_sessionmaker[AsyncSession], product_id: int, price: int, tier: str = "regular"
) -> int:
    """Uma leitura nova do robô: avaliação com o preço e o nível."""
    async with sessions() as s, s.begin():
        product = await s.get(CatalogProduct, product_id)
        assert product is not None
        product.last_tier = tier
        product.last_changed_at = utcnow()
        ev = OfferEvaluation(
            product_id=product_id,
            evaluated_at=utcnow(),
            trigger="price_change",
            ruleset="v3",
            tier=tier,
            score=50.0,
            price_cents=price,
            used_official=True,
            snapshot=json.dumps({"tier": tier, "offer_item_id": "MLB1"}),
        )
        s.add(ev)
        await s.flush()
        return ev.id


async def _decide(
    client: httpx.AsyncClient, product_id: int, ev_id: int, decision: str = "approved"
) -> None:
    page = (await client.get("/vigiar")).text
    r = await client.post(
        "/decisao",
        data={
            "csrf": csrf_of(page),
            "product_id": product_id,
            "evaluation_id": ev_id,
            "decision": decision,
            "next": "/",
        },
    )
    assert r.status_code == 303


def _card(product_id: int) -> str:
    return f'data-card="{product_id}"'


async def test_watched_enters_at_once_and_returns_only_cheaper(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    niche = await _niche(sessions)
    watched = await _product(sessions, niche, "Cafeteira Nespresso Essenza")
    assert (await login(client, secret)).status_code == 303

    # Acabou de ser vigiado, sem leitura ainda: já está no filtro, sem aprovar (não há preço).
    page = (await client.get(WATCHED)).text
    assert _card(watched) in page and "Vigiados na fila" in page
    assert "Aguardando a primeira leitura do preço" in page
    assert 'disabled title="Aguardando a primeira leitura do preço">Aprovar' in page
    assert "👁 vigiados: 1" in page
    assert _card(watched) not in (await client.get("/")).text  # a fila geral pede nível

    # Leu "regular" (fora do padrão da fila geral): o filtro mostra mesmo assim.
    ev = await _read(sessions, watched, 10000)
    page = (await client.get(WATCHED)).text
    assert _card(watched) in page and "Vigiado, ainda sem decisão." in page
    await _decide(client, watched, ev)
    assert _card(watched) not in (await client.get(WATCHED)).text

    # Subiu, voltou ao mesmo preço: não volta. Nem na fila geral, mesmo com nível bom.
    await _read(sessions, watched, 10500, tier="boa")
    await _read(sessions, watched, 10000, tier="boa")
    assert _card(watched) not in (await client.get(WATCHED)).text
    assert _card(watched) not in (await client.get("/")).text
    page = (await client.get(WATCHED)).text
    assert "Nenhum vigiado esperando agora" in page and "👁 vigiados:" not in page

    # Caiu abaixo da aprovação: volta, dizendo de quanto era.
    await _read(sessions, watched, 9990, tier="boa")
    page = (await client.get(WATCHED)).text
    assert _card(watched) in page
    assert "Caiu abaixo da última aprovação: era R$ 100,00." in page
    assert _card(watched) in (await client.get("/")).text  # e na fila geral, que pede "boa"


async def test_rejection_is_also_a_baseline(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    niche = await _niche(sessions)
    watched = await _product(sessions, niche, "Air Fryer Mondial 4L")
    assert (await login(client, secret)).status_code == 303
    ev = await _read(sessions, watched, 30000)
    await _decide(client, watched, ev, "rejected")
    await _read(sessions, watched, 30000)
    assert _card(watched) not in (await client.get(WATCHED)).text
    await _read(sessions, watched, 29900)
    page = (await client.get(WATCHED)).text
    assert "Caiu abaixo da última rejeição: era R$ 300,00." in page


async def test_only_watched_in_the_filter_and_the_rest_keeps_its_rule(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """O filtro só tem vigiados; o produto dos mais vendidos segue a regra de sempre (volta com
    qualquer mudança de preço depois de rejeitado)."""
    secret = await seed_admin(sessions)
    niche = await _niche(sessions)
    watched = await _product(sessions, niche, "Panela de Pressão Clock 4,5L")
    top = await _product(sessions, niche, "Liquidificador Oster", via="highlights", tier="boa")
    assert (await login(client, secret)).status_code == 303
    ev = await _read(sessions, top, 20000, tier="boa")
    page = (await client.get(WATCHED)).text
    assert _card(watched) in page and _card(top) not in page
    assert _card(top) in (await client.get("/")).text
    await _decide(client, top, ev, "rejected")
    await _read(sessions, top, 21000, tier="boa")  # subiu: o mais vendido volta, como antes
    assert _card(top) in (await client.get("/")).text


async def test_watch_page_tells_what_happened_to_each(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    niche = await _niche(sessions)
    new = await _product(sessions, niche, "Garrafa Térmica Stanley")
    waiting = await _product(sessions, niche, "Sanduicheira Britânia")
    approved = await _product(sessions, niche, "Ventilador Arno 40cm")
    back = await _product(sessions, niche, "Mixer Philips Walita")
    assert (await login(client, secret)).status_code == 303
    await _read(sessions, waiting, 8990)
    await _decide(client, approved, await _read(sessions, approved, 19900))
    await _read(sessions, approved, 21000)
    await _decide(client, back, await _read(sessions, back, 15000))
    await _read(sessions, back, 12990)

    page = (await client.get("/vigiar")).text
    assert '<a href="/?origem=vigiados">Ver os vigiados na fila</a>' in page

    def block(name: str) -> str:
        return page.split(name, 1)[1].split("</article>", 1)[0]

    assert "Aguardando a primeira leitura do preço." in block("Garrafa Térmica Stanley")
    assert "Na fila, esperando decisão · agora R$ 89,90." in block("Sanduicheira Britânia")
    fan = block("Ventilador Arno 40cm")
    assert "Aprovado a R$ 199,00 (" in fan and ", dono) · agora R$ 210,00." in fan
    assert "Volta para a fila abaixo de R$ 199,00." in fan
    mixer = block("Mixer Philips Walita")
    assert "Voltou para a fila: aprovado a R$ 150,00 (" in mixer
    assert "e agora está R$ 129,90." in mixer and 'class="situacao na-fila"' in mixer
    assert new and waiting and approved and back
