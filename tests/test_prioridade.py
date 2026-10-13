"""Relâmpago e últimas unidades ainda valendo saem na frente da fila do grupo (13/10/2026)."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.distribution.service import ready_order
from app.domain.distribution import is_urgent
from app.models import CatalogProduct, Channel, Niche, Publication
from tests.test_panel import login, seed_admin


def test_what_counts_as_urgent() -> None:
    now = utcnow()
    assert is_urgent(now + timedelta(hours=2), None, None, now)
    assert not is_urgent(now + timedelta(minutes=9), None, None, now)  # acaba já: sem pressa
    assert is_urgent(None, 4, now - timedelta(minutes=110), now)
    assert not is_urgent(None, 4, now - timedelta(hours=2, minutes=1), now)  # a linha já sumiu
    assert not is_urgent(None, None, None, now)


async def _queue(sessions: async_sessionmaker[AsyncSession], now: datetime) -> dict[str, int]:
    """Fila de um grupo, na ordem de aprovação: comum posicionado, comum, últimas unidades de agora,
    últimas unidades de 3 h atrás, relâmpago valendo e relâmpago acabando."""
    marks: list[tuple[str, dict[str, object]]] = [
        ("posicionado", {"queue_position": 10}),
        ("comum", {}),
        ("ultimas", {"last_units": 3, "last_units_at": now - timedelta(minutes=30)}),
        ("ultimas-velhas", {"last_units": 2, "last_units_at": now - timedelta(hours=3)}),
        ("relampago", {"flash_ends_at": now + timedelta(hours=2)}),
        ("relampago-acabando", {"flash_ends_at": now + timedelta(minutes=5)}),
    ]
    ids: dict[str, int] = {}
    async with sessions() as s, s.begin():
        niche = Niche(slug="casa-mimos", name="Casa", active=True, created_at=now)
        s.add(niche)
        await s.flush()
        channel = Channel(niche_id=niche.id, name="Grupo Casa", transport="wuzapi", created_at=now)
        s.add(channel)
        await s.flush()
        for n, (label, extra) in enumerate(marks):
            product = CatalogProduct(
                marketplace="mercadolivre",
                external_id=f"MLBQ{n}",
                niche_id=niche.id,
                name=f"Produto {label}",
                discovered_via="highlights",
                active=True,
                tier="warm",
                next_check_at=now,
                consecutive_failures=0,
                first_seen_at=now,
            )
            s.add(product)
            await s.flush()
            pub = Publication(
                channel_id=channel.id,
                product_id=product.id,
                idempotency_key=f"q{n}",
                dedup_key=f"mercadolivre:MLBQ{n}",
                state="ready",
                tier="regular",
                target_url="https://x",
                affiliate_url=f"https://meli.la/{n}",
                approved_price_cents=1000,
                created_at=now - timedelta(hours=10 - n),
                **extra,
            )
            s.add(pub)
            await s.flush()
            ids[label] = pub.id
        ids["channel"] = channel.id
    return ids


async def test_urgent_posts_jump_the_queue_and_expired_ones_go_back(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    ids = await _queue(sessions, now)
    async with sessions() as s:
        order = list(
            (
                await s.execute(
                    select(Publication.id)
                    .where(Publication.channel_id == ids["channel"], Publication.state == "ready")
                    .order_by(*ready_order(now))
                )
            ).scalars()
        )
    label = {v: k for k, v in ids.items() if k != "channel"}
    assert [label[i] for i in order] == [
        "relampago",  # relâmpago primeiro (a que acaba antes)
        "ultimas",
        "posicionado",  # depois, a ordem do operador
        "comum",
        "ultimas-velhas",  # marca vencida: volta ao lugar dela
        "relampago-acabando",
    ]
    # Três horas depois a relâmpago também venceu e a fila volta à ordem do operador.
    async with sessions() as s:
        later = list(
            (
                await s.execute(
                    select(Publication.id)
                    .where(Publication.channel_id == ids["channel"])
                    .order_by(*ready_order(now + timedelta(hours=3)))
                )
            ).scalars()
        )
    assert label[later[0]] == "posicionado"


async def test_postar_shows_the_priority(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    ids = await _queue(sessions, utcnow())
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/postar")).text
    rows = page[page.index(f'id="grupo-{ids["channel"]}"') :]
    positions = {k: rows.index(f'data-pub="{v}"') for k, v in ids.items() if k != "channel"}
    assert positions["relampago"] < positions["ultimas"] < positions["posicionado"]
    first = rows[positions["relampago"] : positions["ultimas"]]
    assert "prioridade" in first
    assert "⏳ últimas 3" in rows[positions["ultimas"] : positions["posicionado"]]
