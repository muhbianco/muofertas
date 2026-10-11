from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import CatalogProduct, Channel, Publication
from tests.test_panel import login, seed_admin, seed_product


async def _seed(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Parafusadeira X foi ao grupo há 2 dias (e o preço mudou depois); Serra Y há 10 dias; Trena Z
    nunca foi."""
    first_id = await seed_product(sessions)
    now = utcnow()
    async with sessions() as s, s.begin():
        first = await s.get(CatalogProduct, first_id)
        assert first is not None
        first.last_changed_at = now - timedelta(hours=1)  # voltou para a fila depois do envio
        channel = Channel(
            niche_id=first.niche_id, name="Ferramentas", transport="wuzapi", created_at=now
        )
        s.add(channel)
        others = []
        for external_id, name in (("MLBP2", "Serra Y"), ("MLBP3", "Trena Z")):
            p = CatalogProduct(
                marketplace="mercadolivre",
                external_id=external_id,
                niche_id=first.niche_id,
                name=name,
                discovered_via="highlights",
                active=True,
                tier="warm",
                next_check_at=now,
                consecutive_failures=0,
                first_seen_at=now - timedelta(days=20),
                last_changed_at=now - timedelta(hours=1),
                last_tier="boa",
                last_score=61.0,
            )
            s.add(p)
            others.append(p)
        await s.flush()
        for n, (product, days) in enumerate(((first, 2), (others[0], 10))):
            when = now - timedelta(days=days)
            s.add(
                Publication(
                    channel_id=channel.id,
                    product_id=product.id,
                    idempotency_key=f"k{n}",
                    dedup_key=f"d{n}",
                    state="sent",
                    tier="boa",
                    target_url="https://x",
                    approved_price_cents=10000,
                    created_at=when,
                    sent_at=when,
                )
            )


async def test_queue_marks_and_filters_products_sent_in_the_last_7_days(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    await _seed(sessions)
    assert (await login(client, secret)).status_code == 303

    page = (await client.get("/")).text
    assert all(name in page for name in ("Parafusadeira X", "Serra Y", "Trena Z"))
    # Só o envio de 2 dias atrás conta; o de 10 dias já saiu da janela.
    assert page.count('class="selo enviado"') == 1
    card = page[page.index("Parafusadeira X") - 900 : page.index("Parafusadeira X")]
    assert "já foi pro grupo" in card

    hidden = (await client.get("/?enviados=ocultar")).text
    assert "Parafusadeira X" not in hidden and "Serra Y" in hidden and "Trena Z" in hidden
    assert '<option value="ocultar" selected>' in hidden

    only = (await client.get("/?enviados=so")).text
    assert "Parafusadeira X" in only and "Serra Y" not in only and "Trena Z" not in only

    unknown = (await client.get("/?enviados=qualquer")).text  # valor estranho: lista tudo
    assert all(name in unknown for name in ("Parafusadeira X", "Serra Y", "Trena Z"))
