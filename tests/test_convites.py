"""Convite diário de compartilhamento: calendário, envio uma vez por dia e agenda do painel."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.core.config import Settings
from app.distribution.invites import send_invites
from app.domain.invites import (
    InviteConfig,
    InviteConfigError,
    parse_config,
    plan_invite,
    reference_frozen_until,
)
from app.integrations.wuzapi import WuzapiClient
from app.models import Channel, ChannelInvite, Niche
from tests.fake_wuzapi import ADMIN, SESSION, FakeWuzapi
from tests.test_panel import login, seed_admin

LINK = "https://chat.whatsapp.com/AbCdEf"
# 19h05 de sábado, 11/10/2026, em Brasília (UTC-3): véspera do Dia das Crianças.
EVENING = datetime(2026, 10, 11, 22, 5, tzinfo=UTC)
MORNING = datetime(2026, 10, 11, 14, 0, tzinfo=UTC)


def real_config() -> InviteConfig:
    return parse_config(json.loads(Path("config/convites.json").read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- calendário


def test_real_file_is_valid_and_covers_every_niche_with_a_group() -> None:
    config = real_config()
    seeds = json.loads(Path("config/nichos.json").read_text(encoding="utf-8"))["nichos"]
    active = {n["slug"] for n in seeds if n.get("ativo", True)}
    assert active <= set(config.themes)
    for event in config.events:
        assert event.niches <= active | {"*"}, event.slug


def test_text_follows_the_event_phase_of_each_niche() -> None:
    config = real_config()
    eve = plan_invite(date(2026, 10, 11), "pais-maes-filhos", LINK, config)
    assert (eve.kind, eve.event_slug) == ("vespera", "dia-das-criancas-2026")
    assert "Amanhã é Dia das Crianças" in eve.message and eve.message.endswith(LINK)
    # Ferramentas não está no Dia das Crianças: convite geral, com o tema do nicho.
    tools = plan_invite(date(2026, 10, 11), "ferramentas", LINK, config)
    assert tools.kind == "geral" and "ferramentas e jardinagem" in tools.message
    assert plan_invite(date(2026, 10, 12), "tenis-esporte", LINK, config).kind == "auge"
    assert plan_invite(date(2026, 11, 10), "beleza", LINK, config).kind == "periodo"
    assert plan_invite(date(2026, 11, 26), "beleza", LINK, config).kind == "vespera"
    friday = plan_invite(date(2026, 11, 27), "casa-mimos", LINK, config)
    assert "Black Friday" in friday.message and "casa, cozinha e mimos" in friday.message
    assert plan_invite(date(2026, 11, 30), "beleza", LINK, config).kind == "reta_final"


def test_general_invite_changes_from_one_day_to_the_next() -> None:
    config = real_config()
    texts = [
        plan_invite(date(2026, 10, d), "ferramentas", LINK, config).message for d in (14, 15, 16)
    ]
    assert len(set(texts)) == 3


def test_broken_text_is_refused_when_reading_the_file() -> None:
    base = json.loads(Path("config/convites.json").read_text(encoding="utf-8"))
    no_link = {**base, "gerais": ["sem link nenhum"]}
    with pytest.raises(InviteConfigError, match="link"):
        parse_config(no_link)
    typo = {**base, "gerais": ["oferta de {tem}: {link}"]}
    with pytest.raises(InviteConfigError, match="chave desconhecida"):
        parse_config(typo)


def test_black_friday_freezes_the_reference_21_days_before() -> None:
    config = real_config()
    assert reference_frozen_until(date(2026, 11, 20), "beleza", config) == date(2026, 11, 6)
    assert reference_frozen_until(date(2026, 10, 20), "beleza", config) is None
    # Dia das Crianças não congela.
    assert reference_frozen_until(date(2026, 10, 10), "pais-maes-filhos", config) is None


# --------------------------------------------------------------------------- envio


def settings() -> Settings:
    return Settings(
        database_url_override="sqlite+aiosqlite://",
        wuzapi_base_url="http://wuzapi.test",
        wuzapi_admin_token=SecretStr(ADMIN),
        wuzapi_session_token=SecretStr(SESSION),
    )


async def seed_groups(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    now = utcnow()
    ids: dict[str, int] = {}
    async with sessions() as session, session.begin():
        for n, (slug, extra) in enumerate(
            [
                ("pais-maes-filhos", {}),
                ("ferramentas", {}),
                ("beleza", {"paused": True}),
                ("tecnologia", {"invite_url": None}),
                ("casa-mimos", {"transport": "manual", "wa_jid": None}),
            ]
        ):
            niche = Niche(slug=slug, name=slug, active=True, created_at=now)
            session.add(niche)
            await session.flush()
            values: dict[str, object] = {
                "niche_id": niche.id,
                "name": f"Grupo {slug}",
                "transport": "wuzapi",
                "wa_jid": f"1203630{n}@g.us",
                "invite_url": f"https://chat.whatsapp.com/G{n}",
                "window_start_hour": 7,
                "window_end_hour": 22,
                "created_at": now,
                **extra,
            }
            channel = Channel(**values)
            session.add(channel)
            await session.flush()
            ids[slug] = channel.id
    return ids


async def run(sessions: async_sessionmaker[AsyncSession], wa: FakeWuzapi, now: datetime) -> int:
    client = WuzapiClient(settings(), transport=wa.transport())
    try:
        return await send_invites(sessions, client, real_config(), now, hour=19)
    finally:
        await client.aclose()


async def test_one_invite_per_group_per_day_at_the_peak_hour(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await seed_groups(sessions)
    wa = FakeWuzapi()
    assert await run(sessions, wa, MORNING) == 0  # antes das 19h, nada
    assert await run(sessions, wa, EVENING) == 2  # só os grupos automáticos, ativos e com link
    assert await run(sessions, wa, EVENING) == 0  # já foi hoje
    by_jid = {m["Phone"]: m for m in wa.sent}
    family = by_jid["12036300@g.us"]
    assert "Amanhã é Dia das Crianças" in family["Body"] and family["LinkPreview"] is True
    assert "https://chat.whatsapp.com/G1" in by_jid["12036301@g.us"]["Body"]
    async with sessions() as session:
        rows = (await session.execute(select(ChannelInvite))).scalars().all()
    assert sorted((r.channel_id, r.state, r.kind) for r in rows) == [
        (ids["pais-maes-filhos"], "sent", "vespera"),
        (ids["ferramentas"], "sent", "geral"),
    ]


async def test_skipped_day_and_failures_never_resend(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await seed_groups(sessions)
    async with sessions() as session, session.begin():
        session.add(
            ChannelInvite(
                channel_id=ids["ferramentas"],
                local_date=date(2026, 10, 11),
                kind="geral",
                message="(pulado)",
                state="skipped",
                created_at=utcnow(),
            )
        )
    # WuzAPI fora: nada gravado, tenta de novo no próximo ciclo.
    wa = FakeWuzapi(send_mode="connect")
    assert await run(sessions, wa, EVENING) == 0
    async with sessions() as session:
        count = (await session.execute(select(func.count()).select_from(ChannelInvite))).scalar()
    assert count == 1  # só o pulado
    # Timeout depois de enviar: fica "sem confirmação" e não reenvia.
    wa = FakeWuzapi(send_mode="timeout")
    assert await run(sessions, wa, EVENING) == 0
    assert await run(sessions, wa, EVENING) == 0
    assert len(wa.sent) == 1
    async with sessions() as session:
        family = (
            await session.execute(
                select(ChannelInvite).where(ChannelInvite.channel_id == ids["pais-maes-filhos"])
            )
        ).scalar_one()
    assert family.state == "unknown"


# --------------------------------------------------------------------------- painel


async def test_agenda_shows_the_week_and_skips_a_day(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    ids = await seed_groups(sessions)
    assert (await login(client, secret)).status_code == 303
    page = await client.get("/convites")
    assert page.status_code == 200
    assert "Grupo pais-maes-filhos" in page.text
    assert "sem link de convite" in page.text and "grupo pausado" in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    today = utcnow().date().isoformat()
    r = await client.post(
        "/convites/pular",
        data={"csrf": csrf, "channel_id": ids["ferramentas"], "day": today},
    )
    assert r.status_code == 303
    async with sessions() as session:
        row = (await session.execute(select(ChannelInvite))).scalar_one()
    assert (row.state, row.actor) == ("skipped", "dono")
    assert "Desfazer" in (await client.get("/convites")).text
    await client.post(
        "/convites/desfazer",
        data={"csrf": csrf, "channel_id": ids["ferramentas"], "day": today},
    )
    async with sessions() as session:
        assert (
            await session.execute(select(func.count()).select_from(ChannelInvite))
        ).scalar() == 0
