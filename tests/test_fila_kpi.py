from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import CatalogProduct, Niche
from tests.test_panel import login, seed_admin, seed_product


async def test_queue_kpi_counts_each_niche_with_the_same_filters(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    await seed_product(sessions)  # Ferramentas: 1 "boa", sem % OFF
    now = utcnow()
    async with sessions() as s, s.begin():
        beauty = Niche(slug="beleza", name="Beleza: maquiagem", active=True, created_at=now)
        s.add(beauty)
        await s.flush()
        for n, (tier, off) in enumerate([("boa", 30), ("boa", None), ("regular", 40)]):
            s.add(
                CatalogProduct(
                    marketplace="mercadolivre",
                    external_id=f"MLBB{n}",
                    niche_id=beauty.id,
                    name=f"Batom {n}",
                    discovered_via="highlights",
                    active=True,
                    tier="warm",
                    next_check_at=now,
                    consecutive_failures=0,
                    first_seen_at=now,
                    last_tier=tier,
                    last_score=60.0,
                    listing_discount_pct=off,
                )
            )
    assert (await login(client, secret)).status_code == 303

    page = (await client.get("/")).text  # padrão: excepcional a suspeita
    assert "<b>3</b><span>na fila</span>" in page
    assert "<b>1</b><span>Ferramentas</span>" in page and "<b>2</b><span>Beleza</span>" in page

    off = (await client.get("/?nivel=off&nicho=beleza")).text  # todos os níveis, só % OFF
    assert "<b>2</b><span>na fila</span>" in off
    assert "<b>0</b><span>Ferramentas</span>" in off and "<b>2</b><span>Beleza</span>" in off
    assert 'href="/?nicho=ferramentas&amp;nivel=off"' in off  # o clique mantém o filtro
