from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import CatalogProduct, Channel, Niche, Publication
from tests.test_panel import login, seed_admin, seed_product


async def test_side_panel_counts_what_is_ready_to_post_per_niche(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    product_id = await seed_product(sessions)  # nicho Ferramentas
    now = utcnow()
    async with sessions() as s, s.begin():
        product = await s.get(CatalogProduct, product_id)
        assert product is not None
        beauty = Niche(slug="beleza", name="Beleza: maquiagem", active=True, created_at=now)
        s.add(beauty)
        await s.flush()
        tools = Channel(
            niche_id=product.niche_id, name="F", transport="wuzapi", max_per_day=30, created_at=now
        )
        s.add_all(
            [tools, Channel(niche_id=beauty.id, name="B", transport="wuzapi", created_at=now)]
        )
        await s.flush()
        states = [
            ("ready", None),
            ("ready", None),
            ("awaiting_link", None),  # o link sai em segundos: já conta como pronto
            ("sent", now),  # hoje
            ("sent", now - timedelta(days=2)),  # outro dia: não conta
            ("discarded", None),
            ("skipped", None),
        ]
        for n, (state, sent_at) in enumerate(states):
            s.add(
                Publication(
                    channel_id=tools.id,
                    product_id=product_id,
                    idempotency_key=f"k{n}",
                    dedup_key=f"d{n}",
                    state=state,
                    tier="boa",
                    target_url="https://x",
                    approved_price_cents=10000,
                    created_at=now,
                    sent_at=sent_at,
                )
            )
    assert (await login(client, secret)).status_code == 303

    page = (await client.get("/?nivel=off")).text
    assert "Prontos pra postar <b>3</b>" in page
    tools_row = page[page.index('<span class="nome">Ferramentas</span>') :][:200]
    assert "<b>3</b>" in tools_row and "hoje 1/30" in tools_row
    beauty_row = page[page.index('<span class="nome">Beleza</span>') :][:200]
    assert "<b>0</b>" in beauty_row and "hoje 0/30" in beauty_row
    assert 'href="/?nicho=beleza&amp;nivel=off"' in page  # o clique mantém o filtro
    assert '<main class="com-lado">' in page
