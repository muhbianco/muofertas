"""Repetição em 24 h (pedido do dono em 20/10/2026): o produto que saiu para o grupo há menos de
24 h entra na fila agendado e sai quando completar 24 h do último envio, na frente dos comuns."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.distribution.sender import _due
from app.models import Channel, Publication
from tests.test_envio_em_lote import _group, at


async def test_scheduled_repeat_waits_and_then_goes_first(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    channel, (first, _second, third) = await _group(sessions)
    async with sessions() as s, s.begin():
        row = (await s.execute(select(Channel))).scalar_one()
        row.send_mode = "ritmo"  # o modo de sempre: a fila na ordem de aprovação
        repeat = await s.get(Publication, third)
        assert repeat is not None
        repeat.not_before = at(10)  # saiu ontem às 10h

    # Antes da hora: o agendado nem é visto, o primeiro da fila sai.
    assert await _due(sessions, at(9, 30)) == [(channel, first)]
    # Na hora: passa na frente dos comuns que estavam antes dele.
    assert await _due(sessions, at(10, 1)) == [(channel, third)]


async def test_waiting_repeat_is_listed_last_with_its_time(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    from app.distribution.service import ready_order

    channel, (first, second, third) = await _group(sessions)
    async with sessions() as s, s.begin():
        early = await s.get(Publication, first)
        assert early is not None
        early.not_before = at(12)
    async with sessions() as s:
        order = (
            (
                await s.execute(
                    select(Publication.id)
                    .where(Publication.channel_id == channel, Publication.state == "ready")
                    .order_by(*ready_order(at(9)))
                )
            )
            .scalars()
            .all()
        )
    assert order == [second, third, first]  # o que espera as 24 h fica no fim, até a hora
    assert at(12) - at(9) == timedelta(hours=3)
