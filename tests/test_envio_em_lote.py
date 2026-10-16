"""Envio em lote (pedido do dono em 16/10/2026): Y anúncios a cada X minutos, nos horários contados
do começo da janela. Link avulso, relâmpago e últimas unidades saem a qualquer hora e não gastam o
lote; o grupo no modo de sempre não muda."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.distribution.sender import _due
from app.domain.distribution import (
    BATCH_GAP,
    BatchPlan,
    ChannelPolicy,
    batch_starts,
    send_block_reason,
)
from app.models import CatalogProduct, Channel, Niche, Publication
from tests.test_distribution_panel import csrf_of
from tests.test_panel import login, seed_admin

SP = ZoneInfo("America/Sao_Paulo")


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Horário de Brasília em 16/10/2026."""
    return datetime(2026, 10, 16, hour, minute, second, tzinfo=SP)


LOTE = ChannelPolicy(
    max_per_day=200,
    min_interval=BATCH_GAP,
    window_start_hour=7,
    window_end_hour=22,
    jitter_cap_seconds=20,
    batch=BatchPlan(size=20, every=timedelta(minutes=180)),
)


def test_batches_start_with_the_window() -> None:
    slots = batch_starts(at(12), 7, 22, timedelta(minutes=180))
    assert [s.strftime("%H:%M") for s in slots] == ["07:00", "10:00", "13:00", "16:00", "19:00"]
    every_2h = batch_starts(at(12), 8, 20, timedelta(minutes=120))
    assert [s.hour for s in every_2h] == [8, 10, 12, 14, 16, 18]


def test_full_batch_waits_for_the_next_one_but_priority_goes() -> None:
    def reason(now: datetime, batch_sent: int, *, priority: bool = False) -> str | None:
        return send_block_reason(
            LOTE,
            now=now,
            sent_today=40,
            last_sent_at=now - timedelta(minutes=2),
            batch_sent=batch_sent,
            priority=priority,
        )

    assert reason(at(10, 25), 19) is None
    assert reason(at(10, 25), 20) == "lote das 10:00 completo (20); próximo lote às 13:00"
    assert reason(at(10, 25), 20, priority=True) is None  # relâmpago, avulso, últimas unidades
    assert reason(at(19, 40), 20) == "lote das 19:00 completo (20); era o último do dia"
    assert reason(at(6, 59), 0) == "fora da janela (07h às 22h)"
    # Dentro do lote: 1 min entre um anúncio e o seguinte.
    tight = send_block_reason(
        LOTE, now=at(10, 5), sent_today=3, last_sent_at=at(10, 4, 30), batch_sent=3
    )
    assert tight == "próximo envio a partir de 10:05"


async def _group(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, list[int]]:
    """Grupo em lote de 2 a cada 3 h, com três posts prontos (o terceiro é relâmpago)."""
    now = at(9)
    async with sessions() as s, s.begin():
        niche = Niche(slug="pais-maes-filhos", name="Pais", active=True, created_at=now)
        s.add(niche)
        await s.flush()
        channel = Channel(
            niche_id=niche.id,
            name="MegaPromoOz · Ofertas Reais",
            transport="wuzapi",
            wa_jid="120363000000000001@g.us",
            max_per_day=200,
            window_start_hour=7,
            window_end_hour=22,
            send_mode="lote",
            batch_size=2,
            batch_every_minutes=180,
            created_at=now,
        )
        s.add(channel)
        await s.flush()
        ids = []
        for n in range(3):
            product = CatalogProduct(
                marketplace="mercadolivre",
                external_id=f"MLBL{n}",
                niche_id=niche.id,
                name=f"Produto {n}",
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
                idempotency_key=f"l{n}",
                dedup_key=f"mercadolivre:MLBL{n}",
                state="ready",
                tier="boa",
                target_url="https://x",
                affiliate_url=f"https://meli.la/{n}",
                item_id=f"MLB{n}",
                approved_price_cents=1000,
                created_at=now + timedelta(minutes=n),
            )
            s.add(pub)
            await s.flush()
            ids.append(pub.id)
        return channel.id, ids


async def _sent(sessions: async_sessionmaker[AsyncSession], pub_id: int, when: datetime) -> None:
    async with sessions() as s, s.begin():
        pub = await s.get(Publication, pub_id)
        assert pub is not None
        pub.state, pub.sent_at = "sent", when


async def test_the_sender_follows_the_batches(sessions: async_sessionmaker[AsyncSession]) -> None:
    channel, (first, second, third) = await _group(sessions)
    assert await _due(sessions, at(9, 30)) == [(channel, first)]  # lote das 7h, nada saiu
    await _sent(sessions, first, at(9, 30))
    assert await _due(sessions, at(9, 30, 40)) == []  # 1 min entre os anúncios do lote
    assert await _due(sessions, at(9, 32)) == [(channel, second)]
    await _sent(sessions, second, at(9, 32))
    assert await _due(sessions, at(9, 40)) == []  # lote das 7h completo (2)

    # O relâmpago fura o lote completo (e não conta nele).
    async with sessions() as s, s.begin():
        pub = await s.get(Publication, third)
        assert pub is not None
        pub.flash_ends_at = at(12)
    assert await _due(sessions, at(9, 40)) == [(channel, third)]
    async with sessions() as s, s.begin():
        pub = await s.get(Publication, third)
        assert pub is not None
        pub.flash_ends_at = None  # de volta a post comum

    assert await _due(sessions, at(9, 59)) == []
    assert await _due(sessions, at(10, 0, 30)) == [(channel, third)]  # lote das 10h

    # Modo de sempre: o mesmo grupo espalhado volta a seguir o intervalo do dia.
    async with sessions() as s, s.begin():
        row = (await s.execute(select(Channel))).scalar_one()
        row.send_mode = "ritmo"
    assert await _due(sessions, at(9, 40)) == [(channel, third)]


async def test_groups_screen_saves_and_checks_the_batches(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    async with sessions() as s, s.begin():
        niche = Niche(slug="casa-mimos", name="Casa", active=True, created_at=at(9))
        s.add(niche)
        await s.flush()
        niche_id = niche.id
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/grupos")).text
    assert 'name="modo"' in page and 'name="lote_tamanho"' in page
    base = {
        "csrf": csrf_of(page),
        "name": "MegaPromoOz · Ofertas Reais",
        "niche_id": niche_id,
        "max_per_day": 200,
        "window_start_hour": 7,
        "window_end_hour": 22,
        "modo": "lote",
        "lote_tamanho": "20",
        "lote_minutos": "180",
    }

    missing = await client.post("/grupos", data=base | {"lote_tamanho": ""})
    assert missing.status_code == 422 and "anúncios por lote: de 1 a 50" in missing.text
    cut = await client.post("/grupos", data=base | {"max_per_day": 60})
    assert cut.status_code == 422 and "corta os lotes: eles pedem 100" in cut.text
    tight = await client.post("/grupos", data=base | {"lote_tamanho": "40", "lote_minutos": "30"})
    assert tight.status_code == 422 and "o lote não cabe" in tight.text

    ok = await client.post("/grupos", data=base)
    assert ok.status_code == 303
    async with sessions() as s:
        row = (await s.execute(select(Channel))).scalar_one()
    assert (row.send_mode, row.batch_size, row.batch_every_minutes) == ("lote", 20, 180)
    page = (await client.get("/grupos")).text
    assert (
        "Em lotes: 20 a cada 3 h, às 07h, 10h, 13h, 16h, 19h · até 100 por dia, fora as "
        "prioridades (teto de 200)"
    ) in page

    steady = await client.post(f"/grupos/{row.id}", data=base | {"modo": "ritmo"})
    assert steady.status_code == 303
    page = (await client.get("/grupos")).text
    assert "Espalhado: 200 por dia, um a cada ~4 min, das 7h às 22h" in page
