"""Aprovar já em vários grupos: o "Grupos ▾" da fila manda o post para todos os marcados."""

from __future__ import annotations

import json
import re
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import CatalogProduct, Channel, Niche, OperatorDecision, Publication
from tests.test_distribution_panel import csrf_of, evaluation
from tests.test_panel import login, seed_admin


async def _whey(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Whey no nicho de esportes; grupos de esportes e família, um pausado e um de nicho inativo."""
    now = utcnow()
    async with sessions() as s, s.begin():
        sport = Niche(
            slug="tenis-esporte", name="Moda Unissex e Esportes", active=True, created_at=now
        )
        family = Niche(
            slug="pais-maes-filhos", name="Pais, mães e filhos: bebês", active=True, created_at=now
        )
        old = Niche(slug="observacao", name="Observação", active=False, created_at=now)
        s.add_all([sport, family, old])
        await s.flush()
        channels = {
            "sport": Channel(
                niche_id=sport.id,
                name="Grupo Esportes",
                transport="wuzapi",
                ml_tag="mpotenis",
                created_at=now,
            ),
            "family": Channel(
                niche_id=family.id,
                name="Grupo Família",
                transport="wuzapi",
                ml_tag="mpofamilia",
                created_at=now,
            ),
            "paused": Channel(
                niche_id=family.id, name="Fam 2", transport="wuzapi", paused=True, created_at=now
            ),
            "inactive": Channel(
                niche_id=old.id, name="Grupo Observação", transport="wuzapi", created_at=now
            ),
        }
        s.add_all(channels.values())
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLBWHEY",
            niche_id=sport.id,
            name="Whey Protein Concentrado 900 g",
            discovered_via="highlights",
            active=True,
            tier="warm",
            next_check_at=now,
            consecutive_failures=0,
            first_seen_at=now - timedelta(days=20),
            last_changed_at=now - timedelta(hours=2),
            last_tier="excelente",
            last_score=80.0,
        )
        s.add(product)
        await s.flush()
        ev = evaluation(product.id, "excelente", price=12990)
        snapshot = json.loads(ev.snapshot) | {
            "score": 80.0,
            "ruleset": "v2",
            "real_discount": None,
            "reference_cents": None,
            "components": {},
        }
        ev.snapshot = json.dumps(snapshot)  # o que a página do produto lê
        s.add(ev)
        await s.flush()
        ids = {k: c.id for k, c in channels.items()}
        return ids | {"product": product.id, "ev": ev.id, "sport_niche": sport.id}


def _box(channel_id: int) -> str:
    return f'name="channel_id" value="{channel_id}"'


async def test_approve_into_two_groups_shows_in_both_send_queues(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/")).text
    form = f"aprovar-{ids['product']}"
    assert f'data-grupos="{form}"' in page
    assert f'form="{form}" {_box(ids["sport"])} checked' in page  # o grupo do nicho vem marcado
    assert f'form="{form}" {_box(ids["family"])}>' in page
    assert _box(ids["paused"]) not in page and _box(ids["inactive"]) not in page
    assert ">Moda Unissex e Esportes<" in page and "Pais, mães e filhos</label>" in page
    assert 'name="grupos" value="1"' in page
    csrf = csrf_of(page)
    base = {
        "csrf": csrf,
        "product_id": ids["product"],
        "evaluation_id": ids["ev"],
        "decision": "approved",
        "next": "/",
        "grupos": "1",
    }

    # Nada marcado: não aprova (nem grava a decisão).
    none = await client.post("/decisao", data=base)
    assert none.headers["location"] == "/?aviso=grupos"
    assert "marque pelo menos um grupo" in (await client.get(none.headers["location"])).text
    async with sessions() as s:
        assert (await s.execute(select(func.count(OperatorDecision.id)))).scalar_one() == 0

    marked = [ids["sport"], ids["family"], ids["paused"], ids["inactive"], 99999]
    r = await client.post("/decisao", data=base | {"channel_id": [str(i) for i in marked]})
    assert r.headers["location"] == "/?aviso=varios"
    async with sessions() as s:
        pubs = (await s.execute(select(Publication).order_by(Publication.id))).scalars().all()
        product = (await s.execute(select(CatalogProduct))).scalar_one()
    # Só os grupos que valem: pausado, de nicho inativo e desconhecido ficam de fora.
    assert [(p.channel_id, p.state) for p in pubs] == [
        (ids["sport"], "awaiting_link"),
        (ids["family"], "awaiting_link"),
    ]
    assert product.niche_id == ids["sport_niche"] and not product.niche_locked

    # Gerado o link de cada grupo (o worker faz em segundos), o produto aparece em Postar na fila
    # de cada um.
    async with sessions() as s, s.begin():
        for pub in (await s.execute(select(Publication))).scalars():
            pub.state, pub.affiliate_url = "ready", f"https://meli.la/{pub.channel_id}"
    postar = (await client.get("/postar")).text
    sections = {
        int(m.group(1)): m.group(2)
        for m in re.finditer(r'id="grupo-(\d+)"(.*?)</details>', postar, re.S)
    }
    assert "Whey Protein" in sections[ids["sport"]] and "Whey Protein" in sections[ids["family"]]

    # O mesmo toque de novo não duplica.
    await client.post("/decisao", data=base | {"channel_id": [str(ids["family"])]})
    async with sessions() as s:
        assert (await s.execute(select(func.count(Publication.id)))).scalar_one() == 2


async def test_product_page_offers_the_groups_too(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    assert (await login(client, secret)).status_code == 303
    page = (await client.get(f"/produto/{ids['product']}")).text
    assert f'data-grupos="aprovar-{ids["product"]}"' in page
    assert _box(ids["family"]) in page and 'name="grupos" value="1"' in page
    assert '<script src="/static/fila.js?v=6" defer></script>' in page
