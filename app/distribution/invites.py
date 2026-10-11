"""Convites de compartilhamento: envio pelo worker e agenda do painel.

Dois por grupo por dia: de manhã, no primeiro horário de envio do grupo (o início da janela, antes
da primeira oferta), e à noite, no pico (INVITE_HOUR). Cada um leva a indicação de outro grupo da
MegaPromoOz. O da manhã só vale nas primeiras horas: perdido (worker parado), não sai à tarde
grudado no da noite.

No máximo um por grupo, dia e horário: a linha (`channel_invite`, índice único) é gravada antes de
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
from app.domain.invites import (
    SLOT_EVENING,
    SLOT_MORNING,
    InviteConfig,
    InvitePlan,
    PartnerGroup,
    choose_partner,
    parse_config,
    plan_invite,
)
from app.integrations.wuzapi import WuzapiClient, WuzapiError
from app.models import Channel, ChannelInvite, Niche

logger = get_logger(__name__)
STUCK_AFTER = timedelta(minutes=10)
AGENDA_DAYS = 3
# O convite da manhã só sai nas primeiras horas da janela do grupo.
MORNING_VALID_HOURS = 3
SLOTS = (SLOT_MORNING, SLOT_EVENING)
_CONFIG: dict[str, InviteConfig] = {}


def _read_config(path: str) -> InviteConfig:
    return parse_config(json.loads(Path(path).read_text(encoding="utf-8")))


async def invite_config(path: str) -> InviteConfig:
    """Lê `config/convites.json` uma vez por processo (fora do event loop)."""
    if path not in _CONFIG:
        _CONFIG[path] = await asyncio.to_thread(_read_config, path)
    return _CONFIG[path]


@dataclass(frozen=True, slots=True)
class Slot:
    name: str
    hour: int  # a partir desta hora (Brasília)
    until_hour: int  # até esta hora, exclusive


def slots_for(channel: Channel, evening_hour: int, *, morning: bool = True) -> list[Slot]:
    out = []
    start, end = channel.window_start_hour, channel.window_end_hour
    if morning and start < evening_hour:
        out.append(Slot(SLOT_MORNING, start, min(start + MORNING_VALID_HOURS, end, evening_hour)))
    if start <= evening_hour < end:
        out.append(Slot(SLOT_EVENING, evening_hour, end))
    return out


def _eligible(channel: Channel) -> bool:
    return (
        channel.transport == "wuzapi"
        and bool(channel.wa_jid)
        and bool(channel.invite_url)
        and not channel.paused
    )


def _partners(rows: list[tuple[Channel, str]]) -> list[PartnerGroup]:
    return [
        PartnerGroup(c.id, slug, c.invite_url)
        for c, slug in rows
        if _eligible(c) and c.invite_url is not None
    ]


async def _groups(db: AsyncSession) -> list[tuple[Channel, str]]:
    rows = (
        await db.execute(
            select(Channel, Niche.slug)
            .join(Niche, Niche.id == Channel.niche_id)
            .order_by(Channel.id)
            .limit(50)
        )
    ).all()
    return [(c, slug) for c, slug in rows]


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
    slot: str,
    plan: InvitePlan,
    now: datetime,
) -> int | None:
    try:
        async with sessions() as db, db.begin():
            row = ChannelInvite(
                channel_id=channel_id,
                local_date=today,
                slot=slot,
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
        return None  # outro processo pegou o horário (ou o operador pulou agora)


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
    morning: bool = True,
) -> int:
    """Manda os convites do horário aos grupos que ainda não receberam."""
    local = now.astimezone(SP)
    today = local.date()
    await recover_stuck(sessions, now)
    async with sessions() as db:
        rows = await _groups(db)
        done = {
            (r.channel_id, r.slot)
            for r in (
                await db.execute(
                    select(ChannelInvite.channel_id, ChannelInvite.slot).where(
                        ChannelInvite.local_date == today
                    )
                )
            ).all()
        }
    partners = _partners(rows)
    sent = 0
    for channel, niche_slug in rows:
        if not _eligible(channel):
            continue
        assert channel.wa_jid is not None and channel.invite_url is not None
        for slot in slots_for(channel, hour, morning=morning):
            if (channel.id, slot.name) in done or not slot.hour <= local.hour < slot.until_hour:
                continue
            partner = choose_partner(today, channel.id, partners, config, slot=slot.name)
            plan = plan_invite(
                today, niche_slug, channel.invite_url, config, partner=partner, slot=slot.name
            )
            invite_id = await _claim(sessions, channel.id, today, slot.name, plan, now)
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
                        sessions,
                        invite_id,
                        state="failed",
                        note=f"a WuzAPI recusou ({exc.status})",
                    )
                logger.warning(
                    "convite não saiu",
                    extra={"channel_id": channel.id, "slot": slot.name, "status": exc.status},
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
                extra={
                    "channel_id": channel.id,
                    "slot": slot.name,
                    "kind": plan.kind,
                    "event": plan.event_slug,
                    "partner": plan.partner_channel_id,
                },
            )
    return sent


# --------------------------------------------------------------------------- agenda do painel


@dataclass
class AgendaDay:
    day: date
    slot: str
    hour: int
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


def _problem(channel: Channel) -> str | None:
    if channel.transport != "wuzapi" or not channel.wa_jid:
        return "grupo sem envio automático: o convite não sai"
    if not channel.invite_url:
        return "sem link de convite cadastrado em Grupos"
    if channel.paused:
        return "grupo pausado"
    return None


async def agenda(
    db: AsyncSession, config: InviteConfig, now: datetime, *, hour: int, morning: bool = True
) -> list[AgendaGroup]:
    local = now.astimezone(SP)
    today = local.date()
    days = [today + timedelta(days=i) for i in range(AGENDA_DAYS)]
    rows = await _groups(db)
    partners = _partners(rows)
    recorded = {
        (r.channel_id, r.local_date, r.slot): r
        for r in (
            await db.execute(
                select(ChannelInvite).where(
                    ChannelInvite.local_date >= today, ChannelInvite.local_date <= days[-1]
                )
            )
        ).scalars()
    }
    names = {e.slug: e.name for e in config.events}
    out = []
    for channel, slug in rows:
        problem = _problem(channel)
        link = channel.invite_url or "(link de convite)"
        slots = slots_for(channel, hour, morning=morning)
        if problem is None and not slots:
            problem = f"o horário do convite ({hour:02d}h) está fora da janela do grupo"
        entries = []
        for day in days:
            for slot in slots:
                row = recorded.get((channel.id, day, slot.name))
                if row is not None:
                    plan = InvitePlan(
                        row.kind, row.event_slug, names.get(row.event_slug or ""), row.message
                    )
                    entries.append(
                        AgendaDay(
                            day,
                            slot.name,
                            slot.hour,
                            plan,
                            row.state,
                            row.note,
                            can_undo=row.state == "skipped",
                        )
                    )
                    continue
                partner = choose_partner(day, channel.id, partners, config, slot=slot.name)
                plan = plan_invite(day, slug, link, config, partner=partner, slot=slot.name)
                plan = InvitePlan(
                    plan.kind,
                    plan.event_slug,
                    names.get(plan.event_slug or ""),
                    plan.message,
                    plan.partner_channel_id,
                )
                expired = day == today and local.hour >= slot.until_hour
                state = "sem_envio" if problem or expired else "agendado"
                entries.append(
                    AgendaDay(
                        day,
                        slot.name,
                        slot.hour,
                        plan,
                        state,
                        "o horário já passou" if expired and not problem else None,
                        can_skip=state == "agendado",
                    )
                )
        out.append(AgendaGroup(channel, slug, problem, entries))
    return out


async def skip_day(
    db: AsyncSession,
    *,
    channel_id: int,
    day: date,
    slot: str,
    actor: str,
    now: datetime,
) -> bool:
    """O operador pula um convite (antes de ele sair)."""
    if slot not in SLOTS or day < now.astimezone(SP).date():
        return False
    exists = (
        await db.execute(
            select(ChannelInvite.id).where(
                ChannelInvite.channel_id == channel_id,
                ChannelInvite.local_date == day,
                ChannelInvite.slot == slot,
            )
        )
    ).scalar_one_or_none()
    if exists is not None or await db.get(Channel, channel_id) is None:
        return False
    db.add(
        ChannelInvite(
            channel_id=channel_id,
            local_date=day,
            slot=slot,
            kind="geral",
            message="(pulado pelo operador)",
            state="skipped",
            created_at=now,
            actor=actor[:80],
        )
    )
    return True


async def undo_skip(
    db: AsyncSession, *, channel_id: int, day: date, slot: str, now: datetime
) -> bool:
    if slot not in SLOTS or day < now.astimezone(SP).date():
        return False
    result = await db.execute(
        delete(ChannelInvite).where(
            ChannelInvite.channel_id == channel_id,
            ChannelInvite.local_date == day,
            ChannelInvite.slot == slot,
            ChannelInvite.state == "skipped",
        )
    )
    return bool(getattr(result, "rowcount", 0))
