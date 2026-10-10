"""WhatsApp (Fase 2b): pareamento pelo painel, grupos ligados aos nichos, tamanho dos grupos."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import check_once
from app.core.clock import utcnow
from app.core.config import Settings
from app.integrations.wuzapi import WuzapiClient, summarize_group
from app.main import app
from app.models import Alert, Channel, ChannelStat, CollectionRun, Niche, WaSession, WorkerLease
from app.panel.whatsapp_routes import get_wa_client
from app.whatsapp.service import masked_number
from tests.fake_wuzapi import ADMIN, SESSION, FakeWuzapi, group
from tests.test_panel import login, seed_admin

GROUP_FERR = "120363111111111111@g.us"
GROUP_BEL = "120363222222222222@g.us"


def wa_settings() -> Settings:
    return Settings(
        database_url_override="sqlite+aiosqlite://",
        wuzapi_base_url="http://wuzapi.test",
        wuzapi_admin_token=SecretStr(ADMIN),
        wuzapi_session_token=SecretStr(SESSION),
    )


@pytest.fixture
async def fake() -> AsyncIterator[FakeWuzapi]:
    fake = FakeWuzapi(
        groups=[
            group(GROUP_FERR, "MegaPromoOz - Ferramentas", 12),
            group(GROUP_BEL, "MegaPromoOz - Beleza", 5, announce=True),
        ]
    )
    client = WuzapiClient(wa_settings(), transport=fake.transport())
    app.dependency_overrides[get_wa_client] = lambda: client
    yield fake
    await client.aclose()


def test_group_summary_keeps_only_counts() -> None:
    summary = summarize_group(group(GROUP_FERR, "Ferramentas", 4))
    assert summary is not None
    assert (summary.members, summary.admins, summary.announce) == (4, 1, False)
    assert not any("s.whatsapp.net" in str(v) for v in asdict(summary).values())
    assert summarize_group({"JID": "5511@s.whatsapp.net"}) is None  # conversa, não grupo


def test_masked_number() -> None:
    assert masked_number("5511999998888:12@s.whatsapp.net") == "+55 11 •••• 8888"
    assert masked_number(None) is None and masked_number("abc@lid") is None


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


async def test_pair_link_groups_and_count_members(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession], fake: FakeWuzapi
) -> None:
    secret = await seed_admin(sessions)
    now = utcnow()
    async with sessions() as session, session.begin():
        niche = Niche(slug="ferramentas", name="Ferramentas", active=True, created_at=now)
        session.add(niche)
        await session.flush()
        session.add(
            Channel(
                niche_id=niche.id,
                name="Ofertas Ferramentas",
                transport="manual",
                max_per_day=10,
                min_interval_minutes=40,
                window_start_hour=8,
                window_end_hour=22,
                paused=False,
                created_at=now,
            )
        )
    assert (await login(client, secret)).status_code == 303

    page = (await client.get("/numero")).text
    assert "Criar sessão e conectar" in page
    csrf = csrf_of(page)
    assert (await client.post("/numero/conectar", data={"csrf": csrf})).status_code == 303
    assert "POST /session/connect" in fake.calls
    async with sessions() as session:
        row = (await session.execute(select(WaSession))).scalar_one()
    assert row.external_id == "abc123" and not row.logged_in

    # sem pareamento: QR e código; o número digitado não fica gravado
    page = (await client.get("/numero")).text
    assert 'src="data:image/png;base64,' in page
    code = await client.post("/numero/codigo", data={"csrf": csrf, "phone": "55 11 99999-8888"})
    assert "9H3J-H3J8" in code.text
    bad = await client.post("/numero/codigo", data={"csrf": csrf, "phone": "123"})
    assert bad.headers["location"].endswith("erro=telefone")

    # pareado
    fake.logged_in, fake.jid = True, "5511999998888:12@s.whatsapp.net"
    page = (await client.get("/numero")).text
    assert "+55 11 •••• 8888" in page and SESSION not in page and ADMIN not in page
    async with sessions() as session:
        row = (await session.execute(select(WaSession))).scalar_one()
        channel = (await session.execute(select(Channel))).scalar_one()
    assert row.logged_in and row.jid == "5511999998888:12@s.whatsapp.net"

    # grupos do WhatsApp aparecem com contagem; ligar traz o convite e mede o tamanho
    page = (await client.get("/grupos")).text
    assert "MegaPromoOz - Ferramentas" in page and "12 membros" in page
    assert "55119000000" not in page  # telefones dos membros nunca aparecem
    r = await client.post(
        "/grupos/whatsapp/vincular",
        data={"csrf": csrf, "jid": GROUP_FERR, "target": f"c:{channel.id}"},
    )
    assert r.status_code == 303 and r.headers["location"] == "/grupos"
    async with sessions() as session:
        channel = (await session.execute(select(Channel))).scalar_one()
        stat = (await session.execute(select(ChannelStat))).scalar_one()
    assert channel.wa_jid == GROUP_FERR
    assert channel.invite_url == "https://chat.whatsapp.com/120363X"
    assert (stat.members, stat.admins) == (12, 1)
    assert "12 membros hoje" in (await client.get("/grupos")).text

    # só admins enviam
    await client.post("/grupos/whatsapp/so-admins", data={"csrf": csrf, "jid": GROUP_FERR})
    assert fake.groups[0]["IsAnnounce"] is True

    # desconectar pede confirmação
    no = await client.post("/numero/sair", data={"csrf": csrf})
    assert no.headers["location"].endswith("erro=confirmar") and fake.logged_in
    await client.post("/numero/sair", data={"csrf": csrf, "confirm": "sim"})
    assert not fake.logged_in


async def test_watcher_alerts_when_paired_number_drops(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    cfg = wa_settings()
    fake = FakeWuzapi(groups=[group(GROUP_FERR, "Ferramentas", 30)])
    now = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
    async with sessions() as session, session.begin():
        session.add(WorkerLease(name="collector", holder="w", expires_at=now + timedelta(hours=9)))
        session.add(
            CollectionRun(source="ml_refresh", started_at=now, finished_at=now, status="ok")
        )
        session.add(WaSession(name="megapromooz", external_id="abc123", created_at=now))
    fake.sessions["abc123"] = {"name": "megapromooz"}
    fake.logged_in, fake.jid = True, "5511999998888:12@s.whatsapp.net"
    wa_client = WuzapiClient(cfg, transport=fake.transport())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as h:
        await check_once(sessions, h, cfg, "web-1", now, wa_client=wa_client)
        fake.logged_in = False
        await check_once(sessions, h, cfg, "web-1", now + timedelta(minutes=5), wa_client=wa_client)
        fake.down = True  # WuzAPI fora não derruba o vigia
        await check_once(
            sessions, h, cfg, "web-1", now + timedelta(minutes=10), wa_client=wa_client
        )
    await wa_client.aclose()
    async with sessions() as session:
        alerts = {a.kind: a.state for a in (await session.execute(select(Alert))).scalars()}
    assert alerts.get("whatsapp_desconectado") == "open"


async def test_create_panel_group_from_whatsapp_group(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession], fake: FakeWuzapi
) -> None:
    secret = await seed_admin(sessions)
    now = utcnow()
    async with sessions() as session, session.begin():
        for slug, name in (("ferramentas", "Ferramentas"), ("beleza", "Beleza: maquiagem")):
            session.add(Niche(slug=slug, name=name, active=True, created_at=now))
        session.add(WaSession(name="megapromooz", external_id="abc123", created_at=now))
    fake.sessions["abc123"] = {"name": "megapromooz"}
    fake.logged_in, fake.jid = True, "5511999998888:12@s.whatsapp.net"
    assert (await login(client, secret)).status_code == 303

    page = (await client.get("/grupos")).text
    csrf = csrf_of(page)
    async with sessions() as session:
        beleza = (await session.execute(select(Niche).where(Niche.slug == "beleza"))).scalar_one()
    # sem grupo no painel: o nicho certo já vem escolhido pelo nome do grupo do WhatsApp
    assert f'<option value="n:{beleza.id}" selected>Novo · Beleza: maquiagem</option>' in page

    bad = await client.post(
        "/grupos/whatsapp/vincular", data={"csrf": csrf, "jid": GROUP_BEL, "target": "x:1"}
    )
    assert bad.status_code == 422
    r = await client.post(
        "/grupos/whatsapp/vincular",
        data={
            "csrf": csrf,
            "jid": GROUP_BEL,
            "target": f"n:{beleza.id}",
            "name": "MegaPromoOz - Beleza",
        },
    )
    assert r.status_code == 303 and r.headers["location"] == "/grupos"
    async with sessions() as session:
        channel = (await session.execute(select(Channel))).scalar_one()
        stat = (await session.execute(select(ChannelStat))).scalar_one()
    assert (channel.name, channel.niche_id, channel.wa_jid) == (
        "MegaPromoOz - Beleza",
        beleza.id,
        GROUP_BEL,
    )
    assert channel.max_per_day == 10 and channel.invite_url and stat.members == 5
