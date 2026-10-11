"""Convite diário de compartilhamento: envio pelo worker e agenda do painel.

No máximo um por grupo por dia: a linha do dia (`channel_invite`, índice único) é gravada antes de
chamar a WuzAPI. Timeout ambíguo vira `unknown` e não reenvia; WuzAPI fora do ar apaga a linha e o
próximo ciclo tenta de novo; recusa clara vira `failed`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.domain.distribution import SP
from app.domain.invites import InviteConfig, InvitePlan, parse_config, plan_invite
from app.integrations.wuzapi import WuzapiClient, WuzapiError
from app.models import Channel, ChannelInvite, Niche

logger = get_logger(__name__)
STUCK_AFTER = timedelta(minutes=10)
AGENDA_DAYS = 7
_CONFIG: dict[str, InviteConfig] = {}


def _read_config(path: str) -> InviteConfig:
    return parse_config(json.loads(Path(path).read_text(encoding="utf-8")))


async def invite_config(path: str) -> InviteConfig:
    """Lê `config/convites.json` uma vez por processo (fora do event loop)."""
    if path not in _CONFIG:
        _CONFIG[path] = await asyncio.to_thread(_read_config, path)
    return _CONFIG[path]


def _sends_at_hour(channel: Channel, hour: int) -> bool:
    return channel.window_start_hour <= hour < channel.window_end_hour


async def _candidates(db: AsyncSession, today: date) -> tuple[list[tuple[Channel, str]], set[int]]:
    rows = (
        await db.execute(
            select(Channel, Niche.slug)
            .join(Niche, Niche.id == Channel.niche_id)
            .where(
                Channel.transport == "wuzapi",
                Channel.wa_jid.is_not(None),
                Channel.invite_url.is_not(None),
                Channel.paused.is_(False),
            )
            .order_by(Channel.id)
            .limit(50)
        )
    ).all()
    done = set(
        (
            await db.execute(
                select(ChannelInvite.channel_id).where(ChannelInvite.local_date == today)
            )
        ).scalars()
    )
    return [(c, slug) for c, slug in rows], done


async def recover_stuck(sessions: async_sessionmaker[AsyncSession], now: datetime) -> int:
    async with sessions() as db, db.begin():
        stuck = list(
            (
                await db.execute(
                    select(ChannelInvite)
                    .where(
                        ChannelInvite.state == "sending",
                        ChannelInvite.created_at < now - STUCK_AFTER,
                    )
                    .limit(50)
                )
            ).scalars()
        )
        for row in stuck:
            row.state = "unknown"
            row.note = "o envio foi interrompido: confira no grupo se saiu"
    return len(stuck)


async def _claim(
    sessions: async_sessionmaker[AsyncSession],
    channel_id: int,
    today: date,
    plan: InvitePlan,
    now: datetime,
) -> int | None:
    try:
        async with sessions() as db, db.begin():
            row = ChannelInvite(
                channel_id=channel_id,
                local_date=today,
                kind=plan.kind,
                event_slug=plan.event_slug,
                message=plan.message,
                state="sending",
                created_at=now,
            )
            db.add(row)
            await db.flush()
            return row.id
    except IntegrityError:
        return None  # outro processo pegou o dia (ou o operador pulou agora)


async def _finish(
    sessions: async_sessionmaker[AsyncSession], invite_id: int, **values: object
) -> None:
    async with sessions() as db, db.begin():
        row = await db.get(ChannelInvite, invite_id)
        if row is not None:
            for key, value in values.items():
                setattr(row, key, value)


async def _deliver(wa: WuzapiClient, jid: str, message: str) -> str:
    """Com a prévia do convite (foto e nome do grupo); recusa clara cai para texto puro."""
    try:
        return await wa.send_text(jid, message, link_preview=True)
    except WuzapiError as exc:
        if exc.maybe_sent or exc.status is None:
            raise
        logger.warning("prévia do convite recusada; vai sem prévia", extra={"status": exc.status})
    return await wa.send_text(jid, message)


async def send_invites(
    sessions: async_sessionmaker[AsyncSession],
    wa: WuzapiClient,
    config: InviteConfig,
    now: datetime,
    *,
    hour: int,
) -> int:
    """Manda o convite do dia aos grupos que ainda não receberam, a partir da hora de pico."""
    local = now.astimezone(SP)
    if local.hour < hour:
        return 0
    await recover_stuck(sessions, now)
    today = local.date()
    async with sessions() as db:
        candidates, done = await _candidates(db, today)
    sent = 0
    for channel, niche_slug in candidates:
        if channel.id in done or not _sends_at_hour(channel, hour):
            continue
        if local.hour >= channel.window_end_hour:
            continue  # a janela do grupo fechou: o dia passa sem convite
        assert channel.wa_jid is not None and channel.invite_url is not None
        plan = plan_invite(today, niche_slug, channel.invite_url, config)
        invite_id = await _claim(sessions, channel.id, today, plan, now)
        if invite_id is None:
            continue
        try:
            message_id = await _deliver(wa, channel.wa_jid, plan.message)
        except WuzapiError as exc:
            if exc.maybe_sent:
                await _finish(
                    sessions,
                    invite_id,
                    state="unknown",
                    note="o WhatsApp não confirmou o envio: confira no grupo se saiu",
                )
            elif exc.status is None:
                async with sessions() as db, db.begin():
                    await db.execute(delete(ChannelInvite).where(ChannelInvite.id == invite_id))
            else:
                await _finish(
                    sessions, invite_id, state="failed", note=f"a WuzAPI recusou ({exc.status})"
                )
            logger.warning(
                "convite não saiu", extra={"channel_id": channel.id, "status": exc.status}
            )
            continue
        await _finish(
            sessions,
            invite_id,
            state="sent",
            sent_at=now,
            external_message_id=message_id[:64] or None,
        )
        sent += 1
        logger.info(
            "convite enviado",
            extra={"channel_id": channel.id, "kind": plan.kind, "event": plan.event_slug},
        )
    return sent


# --------------------------------------------------------------------------- agenda do painel


@dataclass
class AgendaDay:
    day: date
    plan: InvitePlan
    state: str  # agendado | sending | sent | unknown | failed | skipped | sem_envio
    note: str | None = None
    can_skip: bool = False
    can_undo: bool = False


@dataclass
class AgendaGroup:
    channel: Channel
    niche_slug: str
    problem: str | None
    days: list[AgendaDay]


def _problem(channel: Channel, hour: int) -> str | None:
    if channel.transport != "wuzapi" or not channel.wa_jid:
        return "grupo sem envio automático: o convite não sai"
    if not channel.invite_url:
        return "sem link de convite cadastrado em Grupos"
    if channel.paused:
        return "grupo pausado"
    if not _sends_at_hour(channel, hour):
        return f"o horário do convite ({hour:02d}h) está fora da janela do grupo"
    return None


async def agenda(
    db: AsyncSession, config: InviteConfig, now: datetime, *, hour: int
) -> list[AgendaGroup]:
    local = now.astimezone(SP)
    today = local.date()
    days = [today + timedelta(days=i) for i in range(AGENDA_DAYS)]
    rows = (
        await db.execute(
            select(Channel, Niche.slug)
            .join(Niche, Niche.id == Channel.niche_id)
            .order_by(Channel.id)
            .limit(50)
        )
    ).all()
    recorded = {
        (r.channel_id, r.local_date): r
        for r in (
            await db.execute(
                select(ChannelInvite).where(
                    ChannelInvite.local_date >= today, ChannelInvite.local_date <= days[-1]
                )
            )
        ).scalars()
    }
    out = []
    for channel, slug in rows:
        problem = _problem(channel, hour)
        link = channel.invite_url or "(link de convite)"
        entries = []
        for day in days:
            row = recorded.get((channel.id, day))
            if row is not None:
                plan = InvitePlan(row.kind, row.event_slug, None, row.message)
                entries.append(
                    AgendaDay(
                        day,
                        plan,
                        row.state,
                        row.note,
                        can_undo=row.state == "skipped",
                    )
                )
                continue
            plan = plan_invite(day, slug, link, config)
            event = next((e for e in config.events if e.slug == plan.event_slug), None)
            if event is not None:
                plan = InvitePlan(plan.kind, plan.event_slug, event.name, plan.message)
            state = "sem_envio" if problem else "agendado"
            entries.append(AgendaDay(day, plan, state, can_skip=problem is None))
        out.append(AgendaGroup(channel, slug, problem, entries))
    return out


async def skip_day(
    db: AsyncSession, *, channel_id: int, day: date, actor: str, now: datetime
) -> bool:
    """O operador pula o convite do dia (antes de ele sair)."""
    if day < now.astimezone(SP).date():
        return False
    exists = (
        await db.execute(
            select(ChannelInvite.id).where(
                ChannelInvite.channel_id == channel_id, ChannelInvite.local_date == day
            )
        )
    ).scalar_one_or_none()
    if exists is not None or await db.get(Channel, channel_id) is None:
        return False
    db.add(
        ChannelInvite(
            channel_id=channel_id,
            local_date=day,
            kind="geral",
            message="(pulado pelo operador)",
            state="skipped",
            created_at=now,
            actor=actor[:80],
        )
    )
    return True


async def undo_skip(db: AsyncSession, *, channel_id: int, day: date, now: datetime) -> bool:
    if day < now.astimezone(SP).date():
        return False
    result = await db.execute(
        delete(ChannelInvite).where(
            ChannelInvite.channel_id == channel_id,
            ChannelInvite.local_date == day,
            ChannelInvite.state == "skipped",
        )
    )
    return bool(getattr(result, "rowcount", 0))
