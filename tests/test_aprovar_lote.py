"""Fila de aprovação sem voltar ao topo: decisão por fetch (JSON) e marcar ✓/✕ para aplicar de uma
vez. Sem JavaScript, o redirect volta para o card seguinte."""

from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import CatalogProduct, OperatorDecision, Publication
from tests.test_aprovar_grupos import _whey
from tests.test_distribution_panel import csrf_of, evaluation
from tests.test_panel import login, seed_admin

JSON = {"Accept": "application/json"}


async def _second(sessions: async_sessionmaker[AsyncSession], niche_id: int) -> int:
    now = utcnow()
    async with sessions() as s, s.begin():
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLBCREA",
            niche_id=niche_id,
            name="Creatina Monohidratada 300 g",
            discovered_via="highlights",
            active=True,
            tier="warm",
            next_check_at=now,
            consecutive_failures=0,
            first_seen_at=now - timedelta(days=20),
            last_changed_at=now - timedelta(hours=2),
            last_tier="boa",
            last_score=60.0,
        )
        s.add(product)
        await s.flush()
        s.add(evaluation(product.id, "boa", price=8990))
        return product.id


async def test_queue_cards_have_marks_floating_bar_and_next_card(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    other = await _second(sessions, ids["sport_niche"])
    assert (await login(client, secret)).status_code == 303
    response = await client.get("/")
    page = response.text
    # O fetch da fila precisa do connect-src do próprio painel (era 'none' e bloqueava tudo).
    csp = response.headers["Content-Security-Policy"]
    assert "connect-src 'self'" in csp and "script-src 'self'" in csp
    for product in (ids["product"], other):
        assert f'id="p-{product}" data-card="{product}"' in page
    assert page.count('data-marca="approved"') == 2 and page.count('data-marca="rejected"') == 2
    assert 'data-lote role="region"' in page and "data-lote-aplicar disabled" in page
    assert 'data-decisao="approved"' in page and 'data-decisao="blocked"' in page
    # Cada card aponta o seguinte; o último aponta o anterior.
    assert f'name="seguinte" value="{other}"' in page
    assert f'name="seguinte" value="{ids["product"]}"' in page
    assert '<script src="/static/fila.js?v=7" defer></script>' in page


async def test_queue_without_cards_has_no_floating_bar(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    assert (await login(client, secret)).status_code == 303
    assert "data-lote" not in (await client.get("/")).text


async def test_decision_by_fetch_answers_json_and_never_reloads(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """15/10/2026: cada aprovação jogava o dono para o topo da fila. A fila decide por fetch e
    recebe o aviso em JSON; o card some sem recarregar a página."""
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    other = await _second(sessions, ids["sport_niche"])
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/")).text)
    base = {
        "csrf": csrf,
        "product_id": ids["product"],
        "evaluation_id": ids["ev"],
        "decision": "approved",
        "next": "/",
        "grupos": "1",
        "seguinte": other,
    }

    no_group = await client.post("/decisao", data=base, headers=JSON)
    assert no_group.status_code == 422
    assert no_group.json() == {
        "ok": False,
        "aviso": "grupos",
        "mensagem": "Não aprovada: marque pelo menos um grupo ativo em “Grupos”.",
    }
    bad_pix = await client.post(
        "/decisao",
        data=base | {"channel_id": str(ids["sport"]), "pix": "999,00"},
        headers=JSON,
    )
    assert bad_pix.status_code == 422 and bad_pix.json()["aviso"] == "pix"
    async with sessions() as s:
        assert (await s.execute(select(func.count(OperatorDecision.id)))).scalar_one() == 0

    ok = await client.post(
        "/decisao",
        data=base | {"channel_id": [str(ids["sport"]), str(ids["family"])]},
        headers=JSON,
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True and body["aviso"] == "varios"
    assert body["mensagem"].startswith("Aprovada para os grupos marcados")

    rejected = await client.post(
        "/decisao",
        data={"csrf": csrf, "product_id": other, "decision": "rejected", "next": "/"},
        headers=JSON,
    )
    assert rejected.json() == {"ok": True, "aviso": None, "mensagem": "Rejeitada: saiu da fila."}
    async with sessions() as s:
        decisions = (
            (await s.execute(select(OperatorDecision.decision).order_by(OperatorDecision.id)))
            .scalars()
            .all()
        )
        pubs = (await s.execute(select(func.count(Publication.id)))).scalar_one()
    assert decisions == ["approved", "rejected"] and pubs == 2
    page = (await client.get("/")).text
    assert f'data-card="{ids["product"]}"' not in page and f'data-card="{other}"' not in page


async def test_without_javascript_the_redirect_goes_to_the_next_card(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    other = await _second(sessions, ids["sport_niche"])
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/?nicho=tenis-esporte")).text)
    base = {
        "csrf": csrf,
        "product_id": ids["product"],
        "evaluation_id": ids["ev"],
        "decision": "approved",
        "next": "/?nicho=tenis-esporte",
        "grupos": "1",
        "seguinte": other,
    }
    error = await client.post("/decisao", data=base)
    assert error.headers["location"] == "/?nicho=tenis-esporte&aviso=grupos"  # aviso no topo
    r = await client.post("/decisao", data=base | {"channel_id": str(ids["sport"])})
    assert r.status_code == 303
    assert r.headers["location"] == f"/?nicho=tenis-esporte&aviso=pronta#p-{other}"
    blocked = await client.post(
        "/decisao",
        data={"csrf": csrf, "product_id": other, "decision": "blocked", "next": "/"},
    )
    assert blocked.headers["location"] == "/"  # último card, sem seguinte: o topo
