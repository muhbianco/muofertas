"""Envio automático pela WuzAPI (Fase 2c), rodando no worker sob o lease do coletor.

Para cada grupo com transporte `wuzapi`, ligado a um grupo do WhatsApp e liberado pela política
(janela, intervalo com variação, teto do dia), pega a publicação pronta mais antiga e:

1. relê o preço da oferta do link (fora de transação); subiu mais de 3% ou saiu do ar: `skipped`;
2. monta a mensagem e marca `sending` numa transação **antes** de chamar a WuzAPI;
3. envia. Resposta recebida: `sent` (ou `failed`). Sem resposta depois de enviar: `unknown`,
   com alerta, e **nunca** reenvia sozinho: duplicata no grupo custa mais que oferta perdida.

`sending` esquecido (worker morto no meio) vira `unknown` depois de 10 minutos.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.distribution.service import (
    DistributionError,
    apply_recheck,
    channel_status,
    fetch_recheck,
    message_for,
)
from app.domain.distribution import jitter
from app.integrations.mercadolivre import MercadoLivreClient
from app.integrations.wuzapi import WuzapiClient, WuzapiError
from app.models import CatalogProduct, Channel, Publication

logger = get_logger(__name__)
STUCK_AFTER = timedelta(minutes=10)
ROBOT = "envio automático"


async def recover_stuck(sessions: async_sessionmaker[AsyncSession], now: datetime) -> int:
    async with sessions() as db, db.begin():
        stuck = list(
            (
                await db.execute(
                    select(Publication)
                    .where(
                        Publication.state == "sending",
                        Publication.sending_at < now - STUCK_AFTER,
                    )
                    .limit(50)
                )
            ).scalars()
        )
        for pub in stuck:
            pub.state = "unknown"
            pub.note = "o envio foi interrompido: confira no grupo se saiu"
    return len(stuck)


async def _due(sessions: async_sessionmaker[AsyncSession], now: datetime) -> list[tuple[int, int]]:
    """(grupo, publicação) que podem sair agora: no máximo uma por grupo por rodada."""
    picks: list[tuple[int, int]] = []
    async with sessions() as db:
        channels = list(
            (
                await db.execute(
                    select(Channel)
                    .where(
                        Channel.transport == "wuzapi",
                        Channel.wa_jid.is_not(None),
                        Channel.paused.is_(False),
                    )
                    .order_by(Channel.id)
                    .limit(50)
                )
            ).scalars()
        )
        for channel in channels:
            pub = (
                await db.execute(
                    select(Publication)
                    .where(Publication.channel_id == channel.id, Publication.state == "ready")
                    .order_by(Publication.created_at, Publication.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if pub is None:
                continue
            last_sent = (
                await db.execute(
                    select(Publication.sent_at)
                    .where(Publication.channel_id == channel.id, Publication.state == "sent")
                    .order_by(Publication.sent_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last_sent is not None and now - last_sent < (
                timedelta(minutes=channel.min_interval_minutes) + jitter(pub.id)
            ):
                continue
            if await channel_status(db, channel, now) is None:
                picks.append((channel.id, pub.id))
    return picks


async def _finish(
    sessions: async_sessionmaker[AsyncSession], pub_id: int, **values: object
) -> None:
    async with sessions() as db, db.begin():
        pub = await db.get(Publication, pub_id)
        if pub is not None:
            for key, value in values.items():
                setattr(pub, key, value)


async def send_one(
    sessions: async_sessionmaker[AsyncSession],
    ml: MercadoLivreClient,
    wa: WuzapiClient,
    *,
    channel_id: int,
    publication_id: int,
    now: datetime,
) -> bool:
    async with sessions() as db:
        pub = await db.get(Publication, publication_id)
        channel = await db.get(Channel, channel_id)
        if pub is None or channel is None or pub.state != "ready" or not channel.wa_jid:
            return False
        product = await db.get(CatalogProduct, pub.product_id)
        if product is None:
            return False
        external_id, item_id, approved = product.external_id, pub.item_id, pub.approved_price_cents
        jid = channel.wa_jid

    if not item_id:
        await _finish(sessions, publication_id, state="failed", note="sem a oferta registrada")
        return False
    try:
        result = await fetch_recheck(
            ml, external_id=external_id, item_id=item_id, approved_cents=approved
        )
    except DistributionError:
        return False  # ML fora: tenta na próxima rodada

    async with sessions() as db, db.begin():
        pub = await db.get(Publication, publication_id)
        if pub is None or pub.state != "ready":
            return False  # o operador mexeu enquanto o ML respondia
        apply_recheck(pub, result, now)
        if result.status != "ok":
            pub.state = "skipped"
            logger.info(
                "envio cancelado na revalidação",
                extra={"publication_id": publication_id, "status": result.status},
            )
            return False
        pub.message = await message_for(db, pub)
        pub.state = "sending"
        pub.sending_at = now
        message = pub.message

    try:
        message_id = await wa.send_text(jid, message)
    except WuzapiError as exc:
        if exc.maybe_sent:
            await _finish(
                sessions,
                publication_id,
                state="unknown",
                note="o WhatsApp não confirmou o envio: confira no grupo se saiu",
            )
        elif exc.status is None:
            await _finish(sessions, publication_id, state="ready", sending_at=None)
        else:
            await _finish(
                sessions,
                publication_id,
                state="failed",
                note=f"a WuzAPI recusou o envio ({exc.status})",
            )
        logger.warning(
            "envio automático falhou",
            extra={"publication_id": publication_id, "status": exc.status},
        )
        return False
    await _finish(
        sessions,
        publication_id,
        state="sent",
        sent_at=now,
        actor=ROBOT,
        external_message_id=message_id[:64] or None,
    )
    logger.info("publicação enviada", extra={"publication_id": publication_id, "auto": True})
    return True


async def send_due(
    sessions: async_sessionmaker[AsyncSession],
    ml: MercadoLivreClient,
    wa: WuzapiClient,
    now: datetime,
) -> int:
    if not wa.configured:
        return 0
    await recover_stuck(sessions, now)
    sent = 0
    for channel_id, publication_id in await _due(sessions, now):
        if await send_one(
            sessions, ml, wa, channel_id=channel_id, publication_id=publication_id, now=now
        ):
            sent += 1
    return sent
