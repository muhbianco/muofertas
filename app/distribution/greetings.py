"""Boas-vindas a quem entra no grupo: olhada na lista, espera e envio pelo worker.

A sessão da MegaPromoOz não recebe evento do WhatsApp (sem webhook, de propósito), então quem
entrou se descobre comparando a lista do grupo (`/group/info`) de uma olhada para a outra, a cada
`poll_every`. A lista fica só na memória do worker, em chaves com sal; o banco guarda quantos
entraram. Consequências aceitas:

- a primeira olhada depois de o worker subir (deploy, troca de líder) é só a referência: quem
  entrou com o worker fora não é saudado;
- quem entra e sai entre duas olhadas não conta.

O envio segue o convite: a linha vira `sending` antes de chamar a WuzAPI; timeout ambíguo vira
`unknown` e não reenvia; WuzAPI fora do ar volta para a espera; recusa clara vira `failed`.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.distribution.service import policy_of
from app.domain.greetings import (
    GreetingConfig,
    expired,
    greeting_due,
    new_members,
    parse_config,
    render_greeting,
)
from app.integrations.wuzapi import WuzapiClient, WuzapiError
from app.models import Channel, ChannelGreeting

logger = get_logger(__name__)
STUCK_AFTER = timedelta(minutes=10)
WAITING = "waiting"
# Contam para o intervalo mínimo entre saudações (a tentativa, saindo ou não confirmada).
ATTEMPTED = ("sending", "sent", "unknown")
MAX_GROUPS = 50
_CONFIG: dict[str, GreetingConfig] = {}


def _read_config(path: str) -> GreetingConfig:
    return parse_config(json.loads(Path(path).read_text(encoding="utf-8")))


async def greeting_config(path: str) -> GreetingConfig:
    """Lê `config/boas_vindas.json` uma vez por processo (fora do event loop)."""
    if path not in _CONFIG:
        _CONFIG[path] = await asyncio.to_thread(_read_config, path)
    return _CONFIG[path]


def eligible(channel: Channel) -> bool:
    return channel.transport == "wuzapi" and bool(channel.wa_jid) and not channel.paused


async def _channels(db: AsyncSession) -> list[Channel]:
    rows = await db.execute(select(Channel).order_by(Channel.id).limit(MAX_GROUPS))
    return [c for c in rows.scalars() if eligible(c)]


async def last_attempts(db: AsyncSession, channel_ids: list[int]) -> dict[int, datetime]:
    """Última saudação tentada de cada grupo (para o intervalo mínimo)."""
    if not channel_ids:
        return {}
    rows = await db.execute(
        select(ChannelGreeting.channel_id, func.max(ChannelGreeting.sent_at))
        .where(
            ChannelGreeting.channel_id.in_(channel_ids),
            ChannelGreeting.state.in_(ATTEMPTED),
        )
        .group_by(ChannelGreeting.channel_id)
    )
    return {channel_id: at for channel_id, at in rows.all() if at is not None}


async def record_joins(
    sessions: async_sessionmaker[AsyncSession], channel_id: int, count: int, now: datetime
) -> None:
    """Soma quem entrou à espera do grupo (uma só por grupo: o worker tem o lease)."""
    async with sessions() as db, db.begin():
        waiting = (
            await db.execute(
                select(ChannelGreeting)
                .where(ChannelGreeting.channel_id == channel_id, ChannelGreeting.state == WAITING)
                .order_by(ChannelGreeting.id)
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if waiting is None:
            db.add(
                ChannelGreeting(
                    channel_id=channel_id,
                    state=WAITING,
                    new_members=count,
                    first_join_at=now,
                    last_join_at=now,
                    created_at=now,
                )
            )
        else:
            waiting.new_members += count
            waiting.last_join_at = now


async def recover_stuck(sessions: async_sessionmaker[AsyncSession], now: datetime) -> int:
    async with sessions() as db, db.begin():
        result = await db.execute(
            update(ChannelGreeting)
            .where(ChannelGreeting.state == "sending", ChannelGreeting.sent_at < now - STUCK_AFTER)
            .values(state="unknown", note="o envio foi interrompido: confira no grupo se saiu")
        )
    return int(getattr(result, "rowcount", 0) or 0)


async def _set(
    sessions: async_sessionmaker[AsyncSession], greeting_id: int, **values: object
) -> None:
    async with sessions() as db, db.begin():
        await db.execute(
            update(ChannelGreeting).where(ChannelGreeting.id == greeting_id).values(**values)
        )


async def _claim(
    sessions: async_sessionmaker[AsyncSession], greeting_id: int, message: str, now: datetime
) -> bool:
    async with sessions() as db, db.begin():
        result = await db.execute(
            update(ChannelGreeting)
            .where(ChannelGreeting.id == greeting_id, ChannelGreeting.state == WAITING)
            .values(state="sending", sent_at=now, message=message)
        )
    return bool(getattr(result, "rowcount", 0))


@dataclass
class Greeter:
    sessions: async_sessionmaker[AsyncSession]
    wa: WuzapiClient
    config: GreetingConfig

    def __post_init__(self) -> None:
        self._salt = secrets.token_bytes(16)
        self._seen: dict[int, tuple[str, frozenset[str]]] = {}  # canal → (JID, chaves)
        self._next_look: datetime | None = None

    async def run(self, now: datetime) -> int:
        if self._next_look is None or now >= self._next_look:
            self._next_look = now + self.config.rules.poll_every
            await recover_stuck(self.sessions, now)
            await self.look(now)
        return await self.send_due(now)

    async def look(self, now: datetime) -> None:
        async with self.sessions() as db:
            channels = await _channels(db)
        alive = {c.id for c in channels}
        for gone in set(self._seen) - alive:
            del self._seen[gone]  # pausado: ao voltar, a primeira olhada é referência de novo
        for channel in channels:
            assert channel.wa_jid is not None
            try:
                keys = await self.wa.group_member_keys(channel.wa_jid, self._salt)
            except WuzapiError as exc:
                logger.warning(
                    "lista do grupo indisponível",
                    extra={"channel_id": channel.id, "status": exc.status},
                )
                continue
            seen = self._seen.get(channel.id)
            # Outro grupo ligado ao canal: a lista dele é referência nova.
            before = seen[1] if seen is not None and seen[0] == channel.wa_jid else None
            if keys:
                self._seen[channel.id] = (channel.wa_jid, keys)
            fresh = new_members(before, keys)
            if fresh:
                await record_joins(self.sessions, channel.id, fresh, now)
                logger.info("entrou gente no grupo", extra={"channel_id": channel.id, "n": fresh})

    async def send_due(self, now: datetime) -> int:
        async with self.sessions() as db:
            rows = (
                await db.execute(
                    select(ChannelGreeting, Channel)
                    .join(Channel, Channel.id == ChannelGreeting.channel_id)
                    .where(ChannelGreeting.state == WAITING)
                    .order_by(ChannelGreeting.id)
                    .limit(MAX_GROUPS)
                )
            ).all()
            last = await last_attempts(db, [c.id for _, c in rows])
        sent = 0
        for greeting, channel in rows:
            if expired(greeting.first_join_at, now):
                await _set(
                    self.sessions,
                    greeting.id,
                    state="expired",
                    note="passou de 24 h sem poder sair (grupo pausado ou fora do ar)",
                )
                continue
            if not eligible(channel) or not greeting_due(
                first_join_at=greeting.first_join_at,
                last_join_at=greeting.last_join_at,
                last_greeting_at=last.get(channel.id),
                now=now,
                window_start_hour=channel.window_start_hour,
                window_end_hour=channel.window_end_hour,
                rules=self.config.rules,
            ):
                continue
            assert channel.wa_jid is not None
            message = render_greeting(self.config, policy_of(channel), now)
            if not await _claim(self.sessions, greeting.id, message, now):
                continue
            try:
                message_id = await self.wa.send_text(channel.wa_jid, message)
            except WuzapiError as exc:
                if exc.maybe_sent:
                    await _set(
                        self.sessions,
                        greeting.id,
                        state="unknown",
                        note="o WhatsApp não confirmou o envio: confira no grupo se saiu",
                    )
                elif exc.status is None:
                    await _set(
                        self.sessions, greeting.id, state=WAITING, sent_at=None, message=None
                    )
                else:
                    await _set(
                        self.sessions,
                        greeting.id,
                        state="failed",
                        note=f"a WuzAPI recusou ({exc.status})",
                    )
                logger.warning(
                    "boas-vindas não saíram",
                    extra={"channel_id": channel.id, "status": exc.status},
                )
                continue
            await _set(
                self.sessions,
                greeting.id,
                state="sent",
                external_message_id=message_id[:64] or None,
            )
            sent += 1
            logger.info(
                "boas-vindas enviadas",
                extra={"channel_id": channel.id, "n": greeting.new_members},
            )
        return sent


# --------------------------------------------------------------------------- painel


@dataclass
class GreetingPreview:
    channel: Channel
    message: str


async def panel_view(
    db: AsyncSession, config: GreetingConfig, now: datetime, *, limit: int = 15
) -> tuple[list[GreetingPreview], list[tuple[ChannelGreeting, str]]]:
    """O texto como sairia agora em cada grupo e as últimas levas (com o nome do grupo)."""
    previews = [
        GreetingPreview(c, render_greeting(config, policy_of(c), now)) for c in await _channels(db)
    ]
    recent = (
        await db.execute(
            select(ChannelGreeting, Channel.name)
            .join(Channel, Channel.id == ChannelGreeting.channel_id)
            .order_by(ChannelGreeting.id.desc())
            .limit(limit)
        )
    ).all()
    return previews, [(g, name) for g, name in recent]
