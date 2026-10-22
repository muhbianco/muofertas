"""Boas-vindas a quem entra no grupo: uma saudação por leva, na janela, sem guardar quem entrou."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.core.config import Settings
from app.distribution.greetings import Greeter
from app.domain.distribution import BATCH_GAP, BatchPlan, ChannelPolicy
from app.domain.greetings import (
    GreetingConfig,
    GreetingConfigError,
    GreetingRules,
    greeting_due,
    new_members,
    parse_config,
    render_greeting,
)
from app.integrations.wuzapi import WuzapiClient, member_keys
from app.models import Channel, ChannelGreeting, Niche
from tests.fake_wuzapi import ADMIN, SESSION, FakeWuzapi, group, person
from tests.test_panel import login, seed_admin

FAMILY = "120363000001@g.us"
# 11h de quinta, 22/10/2026, em Brasília (UTC-3).
MORNING = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
BATCH = ChannelPolicy(
    max_per_day=30,
    min_interval=BATCH_GAP,
    window_start_hour=7,
    window_end_hour=22,
    batch=BatchPlan(size=20, every=timedelta(minutes=180)),
)
STEADY = ChannelPolicy(
    max_per_day=30, min_interval=timedelta(minutes=30), window_start_hour=7, window_end_hour=22
)


def real_config() -> GreetingConfig:
    return parse_config(json.loads(Path("config/boas_vindas.json").read_text(encoding="utf-8")))


def at(hour: int, minute: int = 0, *, day: int = 22) -> datetime:
    """Hora de Brasília → UTC."""
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC) + timedelta(hours=3)


# --------------------------------------------------------------------------- regras


def test_real_file_is_valid() -> None:
    config = real_config()
    assert config.instagram == "https://www.instagram.com/megapromovipoz/"
    assert config.rules == GreetingRules(
        poll_every=timedelta(minutes=5),
        quiet=timedelta(minutes=5),
        max_wait=timedelta(minutes=30),
        cooldown=timedelta(hours=2),
    )


def test_broken_file_is_refused() -> None:
    base = json.loads(Path("config/boas_vindas.json").read_text(encoding="utf-8"))
    with pytest.raises(GreetingConfigError, match="placeholder desconhecido"):
        parse_config({**base, "texto_lote": "às {horario}"})
    with pytest.raises(GreetingConfigError, match="placeholder desconhecido"):
        parse_config({**base, "texto_ritmo": "lote de {lote}"})
    with pytest.raises(GreetingConfigError, match="chave mal fechada"):
        parse_config({**base, "texto_lote": "às {horarios"})
    with pytest.raises(GreetingConfigError, match="minutos"):
        parse_config({**base, "intervalo_minimo_minutos": 5})
    with pytest.raises(GreetingConfigError, match="https"):
        parse_config({**base, "instagram": "instagram.com/x"})


def test_batch_text_tells_the_hours_and_the_next_batch() -> None:
    config = real_config()
    text = render_greeting(config, BATCH, at(11))
    assert "20 ofertas às 7h, 10h, 13h, 16h e 19h." in text
    assert "O próximo lote sai às 13h." in text
    assert "*Em breve:* ofertas da Shopee e da Amazon também!" in text
    assert text.endswith("Segue a gente no Insta: https://www.instagram.com/megapromovipoz/")
    assert "O lote das 10h está saindo agora." in render_greeting(config, BATCH, at(10, 20))
    # Depois do último lote (19h, aberto até 19h40): o de amanhã.
    assert "O próximo lote sai amanhã às 7h." in render_greeting(config, BATCH, at(20))
    assert "{" not in text and "}" not in text


def test_steady_text_tells_the_daily_limit() -> None:
    text = render_greeting(real_config(), STEADY, at(11))
    assert "Até 30 ofertas por dia, das 7h às 22h." in text
    assert "lote" not in text


def test_new_members_counts_who_came_in() -> None:
    before = frozenset({"a", "b", "c"})
    assert new_members(None, before) is None  # primeira olhada: só referência
    assert new_members(before, before | {"d", "e"}) == 2
    assert new_members(before, frozenset({"a", "b"})) == 0  # alguém saiu
    assert new_members(before, frozenset()) is None  # lista vazia não é o grupo
    # Mais da metade "nova" de uma vez: o WhatsApp trocou o formato do identificador.
    big = frozenset(str(i) for i in range(100))
    flipped = frozenset(f"lid-{i}" for i in range(100))
    assert new_members(big, flipped) is None


def test_member_keys_hide_the_phone_and_depend_on_the_salt() -> None:
    raw = {"Participants": [person(1), person(2), {"JID": ""}, "lixo"]}
    one = member_keys(raw, b"sal-1")
    assert len(one) == 2
    assert not any("55119" in key for key in one)
    assert one != member_keys(raw, b"sal-2")
    assert member_keys({"Participants": None}, b"sal") == frozenset()


def due(now: datetime, *, first: datetime, last: datetime, greeted: datetime | None) -> bool:
    return greeting_due(
        first_join_at=first,
        last_join_at=last,
        last_greeting_at=greeted,
        now=now,
        window_start_hour=7,
        window_end_hour=22,
        rules=GreetingRules(),
    )


def test_greeting_waits_for_the_burst_to_end() -> None:
    first = at(11)
    assert not due(at(11, 3), first=first, last=at(11, 2), greeted=None)  # ainda chegando gente
    assert due(at(11, 7), first=first, last=at(11, 2), greeted=None)  # 5 min de silêncio
    # Gente pingando sem parar: sai 30 min depois da primeira entrada.
    assert due(at(11, 30), first=first, last=at(11, 28), greeted=None)


def test_greeting_respects_the_cooldown_and_the_window() -> None:
    assert not due(at(11, 10), first=at(11), last=at(11), greeted=at(10))
    assert due(at(12, 1), first=at(11), last=at(11), greeted=at(10))
    # De madrugada não sai; na abertura, sim.
    assert not due(at(23, 30), first=at(23), last=at(23), greeted=None)
    assert due(at(7, day=23), first=at(23), last=at(23), greeted=None)


# --------------------------------------------------------------------------- worker


def wa_settings() -> Settings:
    return Settings(
        database_url_override="sqlite+aiosqlite://",
        wuzapi_base_url="http://wuzapi.test",
        wuzapi_admin_token=SecretStr(ADMIN),
        wuzapi_session_token=SecretStr(SESSION),
    )


async def seed_channels(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    now = utcnow()
    ids: dict[str, int] = {}
    async with sessions() as session, session.begin():
        niche = Niche(slug="pais-maes-filhos", name="Pais", active=True, created_at=now)
        session.add(niche)
        await session.flush()
        for name, extra in (
            ("Família", {"wa_jid": FAMILY}),
            ("Pausado", {"wa_jid": "120363000002@g.us", "paused": True}),
            ("Manual", {"transport": "manual", "wa_jid": None}),
        ):
            values: dict[str, Any] = {
                "niche_id": niche.id,
                "name": name,
                "transport": "wuzapi",
                "window_start_hour": 7,
                "window_end_hour": 22,
                "send_mode": "lote",
                "batch_size": 20,
                "batch_every_minutes": 180,
                "created_at": now,
                **extra,
            }
            channel = Channel(**values)
            session.add(channel)
            await session.flush()
            ids[name] = channel.id
    return ids


class Harness:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], members: int = 10) -> None:
        self.sessions = sessions
        self.wa = FakeWuzapi(
            groups=[group(FAMILY, "Família", members), group("120363000002@g.us", "P", 5)]
        )
        self.client = WuzapiClient(wa_settings(), transport=self.wa.transport())
        self.greeter = Greeter(sessions, self.client, real_config())
        self.next_person = 1000

    def join(self, n: int) -> None:
        for _ in range(n):
            self.wa.groups[0]["Participants"].append(person(self.next_person))
            self.next_person += 1

    def leave(self, n: int) -> None:
        del self.wa.groups[0]["Participants"][-n:]

    async def tick(self, now: datetime) -> int:
        """Uma volta do worker, forçando a olhada na lista (o relógio do teste pula)."""
        self.greeter._next_look = None
        return await self.greeter.run(now)

    def greetings(self) -> list[str]:
        return [m["Body"] for m in self.wa.sent if m["Phone"] == FAMILY]

    async def rows(self) -> list[ChannelGreeting]:
        async with self.sessions() as session:
            return list(
                (await session.execute(select(ChannelGreeting).order_by(ChannelGreeting.id)))
                .scalars()
                .all()
            )

    async def close(self) -> None:
        await self.client.aclose()


async def test_four_people_in_a_row_get_one_greeting(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_channels(sessions)
    h = Harness(sessions)
    try:
        assert await h.tick(at(11)) == 0  # referência
        for minute in (1, 2, 3, 4):  # quatro entram, um por minuto
            h.join(1)
            assert await h.tick(at(11, minute)) == 0
        assert await h.tick(at(11, 8)) == 0  # 4 min de silêncio: ainda espera
        assert await h.tick(at(11, 9)) == 1
        assert await h.tick(at(11, 15)) == 0
        assert len(h.greetings()) == 1
        assert "O próximo lote sai às 13h." in h.greetings()[0]
        assert h.wa.sent[0]["LinkPreview"] is False
        rows = await h.rows()
        assert [(r.state, r.new_members) for r in rows] == [("sent", 4)]
        assert rows[0].message == h.greetings()[0]
        # Uma olhada por volta, só no Família: o pausado e o manual nem foram olhados.
        assert h.wa.calls.count("GET /group/info") == 8
    finally:
        await h.close()


async def test_people_arriving_during_the_cooldown_wait_for_the_next_greeting(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_channels(sessions)
    h = Harness(sessions)
    try:
        await h.tick(at(11))
        h.join(2)
        await h.tick(at(11, 1))
        assert await h.tick(at(11, 6)) == 1
        h.join(1)
        await h.tick(at(11, 30))
        assert await h.tick(at(12, 0)) == 0  # 2 h desde a última
        h.join(2)
        await h.tick(at(12, 50))
        assert await h.tick(at(13, 6)) == 1
        rows = await h.rows()
        assert [(r.state, r.new_members) for r in rows] == [("sent", 2), ("sent", 3)]
    finally:
        await h.close()


async def test_night_joiners_are_greeted_when_the_window_opens(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_channels(sessions)
    h = Harness(sessions)
    try:
        await h.tick(at(22, 30))
        h.join(3)
        await h.tick(at(23, 0))
        assert await h.tick(at(23, 30)) == 0
        assert await h.tick(at(6, 59, day=23)) == 0
        assert await h.tick(at(7, 0, day=23)) == 1
        assert "O lote das 7h está saindo agora." in h.greetings()[0]
    finally:
        await h.close()


async def test_leaving_and_the_first_look_never_greet(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_channels(sessions)
    h = Harness(sessions)
    try:
        await h.tick(at(11))
        h.leave(2)
        await h.tick(at(11, 5))
        await h.tick(at(11, 30))
        assert h.greetings() == [] and await h.rows() == []
        # Worker novo (deploy): a primeira olhada dele é só referência.
        h.join(3)
        h.greeter = Greeter(sessions, h.client, real_config())
        await h.tick(at(11, 35))
        await h.tick(at(11, 45))
        assert h.greetings() == [] and await h.rows() == []
    finally:
        await h.close()


async def test_group_info_failure_keeps_the_reference(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_channels(sessions)
    h = Harness(sessions)
    try:
        await h.tick(at(11))
        h.wa.info_mode = "refuse"
        h.join(2)
        await h.tick(at(11, 5))
        h.wa.info_mode = "ok"
        await h.tick(at(11, 10))  # a referência antiga continua: os dois contam
        assert await h.tick(at(11, 16)) == 1
        assert [r.new_members for r in await h.rows()] == [2]
    finally:
        await h.close()


@pytest.mark.parametrize(
    ("mode", "state"),
    [("timeout", "unknown"), ("refuse", "failed"), ("connect", "waiting")],
)
async def test_send_failures_never_repeat_a_greeting_that_may_have_gone(
    sessions: async_sessionmaker[AsyncSession], mode: str, state: str
) -> None:
    await seed_channels(sessions)
    h = Harness(sessions)
    try:
        await h.tick(at(11))
        h.join(1)
        await h.tick(at(11, 1))
        h.wa.send_mode = mode
        assert await h.tick(at(11, 7)) == 0
        rows = await h.rows()
        assert [r.state for r in rows] == [state]
        h.wa.send_mode = "ok"
        sent = await h.tick(at(11, 8))
        # Só a WuzAPI fora do ar (nem conectou) tenta de novo.
        assert sent == (1 if mode == "connect" else 0)
    finally:
        await h.close()


async def test_old_wait_expires_instead_of_greeting_a_day_late(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await seed_channels(sessions)
    h = Harness(sessions)
    try:
        await h.tick(at(11))
        h.join(2)
        await h.tick(at(11, 1))
        async with sessions() as session, session.begin():
            channel = await session.get(Channel, ids["Família"])
            assert channel is not None
            channel.paused = True
        await h.tick(at(11, 10))
        async with sessions() as session, session.begin():
            channel = await session.get(Channel, ids["Família"])
            assert channel is not None
            channel.paused = False
        assert await h.tick(at(11, 2, day=23)) == 0
        assert [r.state for r in await h.rows()] == ["expired"]
        assert h.greetings() == []
    finally:
        await h.close()


async def test_nothing_about_who_joined_reaches_the_database(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_channels(sessions)
    h = Harness(sessions)
    try:
        await h.tick(at(11))
        h.join(3)
        await h.tick(at(11, 1))
        await h.tick(at(11, 7))
        async with sessions() as session:
            dump = (await session.execute(text("SELECT * FROM channel_greeting"))).all()
        assert dump and "55119" not in repr(dump)
    finally:
        await h.close()


# --------------------------------------------------------------------------- painel


async def test_invites_page_shows_the_greeting_and_the_recent_ones(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    ids = await seed_channels(sessions)
    async with sessions() as session, session.begin():
        session.add(
            ChannelGreeting(
                channel_id=ids["Família"],
                state="sent",
                new_members=4,
                first_join_at=MORNING,
                last_join_at=MORNING,
                created_at=MORNING,
                sent_at=MORNING + timedelta(minutes=6),
                message="oi",
            )
        )
    assert (await login(client, secret)).status_code == 303
    page = await client.get("/convites")
    assert page.status_code == 200
    assert "Boas-vindas ao MegaPromoOz" in page.text
    assert "20 ofertas às 7h, 10h, 13h, 16h e 19h." in page.text
    assert "4 pessoas" in page.text and "enviada" in page.text
    assert "Pausado" not in page.text.split('id="boas-vindas"', 1)[1].split("Últimas levas")[0]
