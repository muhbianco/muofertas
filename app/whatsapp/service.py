"""Casos de uso do WhatsApp (Fase 2b): sessão, pareamento, grupos e tamanho dos grupos.

O envio automático pela WuzAPI não está aqui de propósito: até o suporte do ML responder se
publicação automatizada é permitida (D18), o ML sai só pelo envio manual.

Toda chamada à WuzAPI acontece fora de transação: quem chama lê o banco, fecha, chama a rede e
abre outra transação para gravar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.distribution import SP
from app.integrations.wuzapi import GroupSummary, WuzapiClient, WuzapiError
from app.models import Channel, ChannelStat, WaSession

logger = get_logger(__name__)
SNAPSHOT_EVERY = timedelta(hours=1)


def local_day(now: datetime) -> date:
    return now.astimezone(SP).date()


def masked_number(jid: str | None) -> str | None:
    """`5511999998888:12@s.whatsapp.net` → `+55 11 •••• 8888` (a tela não mostra o número todo)."""
    if not jid:
        return None
    user = jid.split("@", 1)[0].split(":", 1)[0].split(".", 1)[0]
    if len(user) < 8 or not user.isdigit():
        return None
    return f"+{user[:2]} {user[2:4]} •••• {user[-4:]}"


async def get_session(db: AsyncSession, settings: Settings) -> WaSession | None:
    return (
        await db.execute(select(WaSession).where(WaSession.name == settings.wuzapi_session_name))
    ).scalar_one_or_none()


async def create_session(
    sessions: async_sessionmaker[AsyncSession],
    client: WuzapiClient,
    settings: Settings,
    now: datetime,
) -> WaSession:
    """Cria a sessão na WuzAPI se ainda não existe aqui, e conecta (o QR sai depois)."""
    async with sessions() as db:
        row = await get_session(db, settings)
    if row is None:
        external_id = await client.create_session(settings.wuzapi_session_name)
        async with sessions() as db, db.begin():
            row = WaSession(
                name=settings.wuzapi_session_name, external_id=external_id, created_at=now
            )
            db.add(row)
        logger.info("sessão do WhatsApp criada", extra={"session": settings.wuzapi_session_name})
    await client.connect()
    return row


async def refresh_session(
    sessions: async_sessionmaker[AsyncSession],
    client: WuzapiClient,
    settings: Settings,
    now: datetime,
) -> WaSession | None:
    """Estado real (porta admin, só esta sessão) gravado na linha. None: sem sessão aqui."""
    async with sessions() as db:
        row = await get_session(db, settings)
    if row is None:
        return None
    state = await client.read_session(row.external_id)
    async with sessions() as db, db.begin():
        fresh = await db.get(WaSession, row.id)
        if fresh is None:
            return None
        fresh.logged_in = bool(state and state.logged_in)
        if state and state.jid:
            fresh.jid = state.jid
        fresh.checked_at = now
        return fresh


# --------------------------------------------------------------------------- grupos


@dataclass
class GroupRow:
    group: GroupSummary
    channel: Channel | None


async def group_rows(db: AsyncSession, groups: list[GroupSummary]) -> list[GroupRow]:
    jids = [g.jid for g in groups]
    linked = {
        c.wa_jid: c
        for c in (await db.execute(select(Channel).where(Channel.wa_jid.in_(jids)))).scalars()
    }
    return [GroupRow(g, linked.get(g.jid)) for g in groups]


async def link_group(
    sessions: async_sessionmaker[AsyncSession],
    client: WuzapiClient,
    *,
    channel_id: int,
    jid: str,
    now: datetime,
) -> None:
    """Liga o grupo do WhatsApp ao grupo do painel e traz o link de convite oficial."""
    groups = {g.jid: g for g in await client.groups()}
    if jid not in groups:
        raise WuzapiError("esse grupo não está na lista do número conectado")
    try:
        invite = await client.invite_link(jid)
    except WuzapiError:
        invite = ""  # só admin gera convite; sem ele o vínculo vale do mesmo jeito
    async with sessions() as db, db.begin():
        channel = await db.get(Channel, channel_id)
        if channel is None:
            raise WuzapiError("grupo do painel não encontrado")
        previous = (
            await db.execute(select(Channel).where(Channel.wa_jid == jid, Channel.id != channel_id))
        ).scalar_one_or_none()
        if previous is not None:
            previous.wa_jid = None
            await db.flush()
        channel.wa_jid = jid
        if invite:
            channel.invite_url = invite
    await snapshot(sessions, list(groups.values()), now)


async def snapshot(
    sessions: async_sessionmaker[AsyncSession], groups: list[GroupSummary], now: datetime
) -> int:
    """Tamanho de hoje dos grupos ligados (uma linha por grupo e dia, atualizada)."""
    by_jid = {g.jid: g for g in groups}
    day = local_day(now)
    saved = 0
    async with sessions() as db, db.begin():
        channels = list(
            (await db.execute(select(Channel).where(Channel.wa_jid.is_not(None)))).scalars()
        )
        for channel in channels:
            group = by_jid.get(channel.wa_jid or "")
            if group is None:
                continue
            row = (
                await db.execute(
                    select(ChannelStat).where(
                        ChannelStat.channel_id == channel.id, ChannelStat.day == day
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(
                    ChannelStat(
                        channel_id=channel.id,
                        day=day,
                        members=group.members,
                        admins=group.admins,
                        captured_at=now,
                    )
                )
            else:
                row.members, row.admins, row.captured_at = group.members, group.admins, now
            saved += 1
    return saved


async def tick(
    sessions: async_sessionmaker[AsyncSession],
    client: WuzapiClient,
    settings: Settings,
    now: datetime,
) -> None:
    """Rodada do vigia: estado da sessão sempre; tamanho dos grupos a cada hora."""
    if not client.configured:
        return
    row = await refresh_session(sessions, client, settings, now)
    if row is None or not row.logged_in:
        return
    async with sessions() as db:
        last = (await db.execute(select(func.max(ChannelStat.captured_at)))).scalar_one_or_none()
    if last is not None and now - last < SNAPSHOT_EVERY:
        return
    await snapshot(sessions, await client.groups(), now)


@dataclass
class GrowthRow:
    today: int | None
    week_ago: int | None

    @property
    def delta(self) -> int | None:
        if self.today is None or self.week_ago is None:
            return None
        return self.today - self.week_ago


async def growth(db: AsyncSession, now: datetime) -> dict[int, GrowthRow]:
    """Membros hoje e há 7 dias por grupo (o KPI de audiência do plano de 30 dias)."""
    today = local_day(now)
    week_ago = today - timedelta(days=7)
    rows = (
        await db.execute(
            select(ChannelStat.channel_id, ChannelStat.day, ChannelStat.members).where(
                ChannelStat.day.in_((today, week_ago))
            )
        )
    ).all()
    out: dict[int, GrowthRow] = {}
    for channel_id, day, members in rows:
        entry = out.setdefault(channel_id, GrowthRow(None, None))
        if day == today:
            entry.today = members
        else:
            entry.week_ago = members
    return out
