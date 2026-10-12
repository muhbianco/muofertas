from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import CatalogProduct, Channel, Niche, Publication
from app.panel import distribution_routes
from tests.test_panel import login, seed_admin


async def test_every_group_shows_up_even_when_another_fills_the_cap(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(distribution_routes, "PER_GROUP", 3)
    secret = await seed_admin(sessions)
    now = utcnow()
    async with sessions() as s, s.begin():
        niche = Niche(slug="casa-mimos", name="Casa", active=True, created_at=now)
        s.add(niche)
        await s.flush()
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLBX",
            niche_id=niche.id,
            name="Panela de arroz",
            discovered_via="highlights",
            active=True,
            tier="warm",
            next_check_at=now,
            consecutive_failures=0,
            first_seen_at=now,
        )
        s.add(product)
        channels = [
            Channel(niche_id=niche.id, name=name, transport="manual", created_at=now)
            for name in ("Grupo Cheio", "Grupo Tecnologia", "Grupo Vazio")
        ]
        s.add_all(channels)
        await s.flush()
        # O primeiro grupo passa do teto; o segundo vem depois na ordem e não pode sumir.
        for n, channel in enumerate([channels[0]] * 5 + [channels[1]] * 2):
            s.add(
                Publication(
                    channel_id=channel.id,
                    product_id=product.id,
                    idempotency_key=f"k{n}",
                    dedup_key=f"d{n}",
                    state="ready",
                    tier="boa",
                    target_url="https://x",
                    approved_price_cents=10000,
                    created_at=now,
                )
            )
    assert (await login(client, secret)).status_code == 303

    page = (await client.get("/postar")).text
    assert "<b>7</b> prontos em 3 grupos" in page
    for name, total in (("Grupo Cheio", 5), ("Grupo Tecnologia", 2), ("Grupo Vazio", 0)):
        block = page[page.index(f'<span class="titulo">{name}</span>') :][:400]
        assert f"<b>{total}</b> prontos" in block, name
    assert "Mostrando os 3 primeiros de 5" in page
    assert page.count('<li data-pub="') == 5  # 3 do cheio + 2 do segundo
    assert '<details class="bloco grupo" id="grupo-' in page  # recolhível
    assert "Nada pronto para este grupo" in page
