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

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.distribution.service import (
    DistributionError,
    apply_recheck,
    channel_status,
    fetch_recheck,
    message_for,
    policy_of,
    ready_order,
)
from app.domain.distribution import MANUAL_TIER, Recheck, jitter, manual_block_reason
from app.integrations.images import fetch_product_image
from app.integrations.mercadolivre import MercadoLivreClient
from app.integrations.wuzapi import WuzapiClient, WuzapiError
from app.models import CatalogProduct, Channel, ChannelInvite, Publication

logger = get_logger(__name__)
STUCK_AFTER = timedelta(minutes=10)
# Folga entre o convite do grupo e a oferta seguinte.
AFTER_INVITE = timedelta(minutes=5)
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
                    .order_by(*ready_order(now))
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
            policy = policy_of(channel)
            if last_sent is not None and now - last_sent < (
                policy.min_interval + jitter(pub.id, policy.jitter_cap_seconds)
            ):
                continue
            last_invite = (
                await db.execute(
                    select(func.max(ChannelInvite.created_at)).where(
                        ChannelInvite.channel_id == channel.id,
                        ChannelInvite.state.in_(("sending", "sent", "unknown")),
                    )
                )
            ).scalar_one_or_none()
            if last_invite is not None and now - last_invite < AFTER_INVITE:
                continue  # o convite acabou de sair: a oferta não vai colada nele
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
    images: httpx.AsyncClient | None = None,
    actor: str = ROBOT,
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
        image_url = product.image_url
        manual = pub.tier == MANUAL_TIER
        expired = manual_block_reason(pub.created_at, pub.flash_ends_at, now) if manual else None

    if expired:
        await _finish(sessions, publication_id, state="skipped", note=expired)
        logger.info("link avulso vencido", extra={"publication_id": publication_id})
        return False
    if not item_id:
        await _finish(sessions, publication_id, state="failed", note="sem a oferta registrada")
        return False
    result: Recheck | None = None
    if not manual:  # o link avulso não tem revalidação: a API não lê o anúncio
        try:
            result = await fetch_recheck(
                ml, external_id=external_id, item_id=item_id, approved_cents=approved
            )
        except DistributionError:
            return False  # ML fora: tenta na próxima rodada
    photo = await fetch_product_image(images, image_url) if images is not None else None

    async with sessions() as db, db.begin():
        # Trava a linha até gravar "sending": o robô e o botão "Enviar agora" no mesmo segundo
        # não mandam duas vezes (o segundo espera e encontra "sending").
        pub = await db.get(Publication, publication_id, with_for_update=True)
        if pub is None or pub.state != "ready":
            return False  # o operador mexeu enquanto o ML respondia
        if result is not None:
            apply_recheck(pub, result, now)
            if result.status != "ok":
                pub.state = "skipped"
                logger.info(
                    "envio cancelado na revalidação",
                    extra={"publication_id": publication_id, "status": result.status},
                )
                return False
        pub.message = await message_for(db, pub, now=now)
        pub.state = "sending"
        pub.sending_at = now
        message = pub.message

    try:
        message_id = await _deliver(wa, jid, message, photo)
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
        actor=actor,
        external_message_id=message_id[:64] or None,
    )
    logger.info("publicação enviada", extra={"publication_id": publication_id, "auto": True})
    return True


async def _deliver(wa: WuzapiClient, jid: str, message: str, photo: str | None) -> str:
    """Foto com a mensagem na legenda; recusa clara da foto (nada saiu) cai para texto."""
    if photo:
        try:
            return await wa.send_image(jid, photo, message)
        except WuzapiError as exc:
            if exc.maybe_sent or exc.status is None:
                raise  # incerto, ou a WuzAPI fora: não arrisca duplicar com o texto
            logger.warning("foto recusada; vai só o texto", extra={"status": exc.status})
    return await wa.send_text(jid, message)


async def resend_now(
    sessions: async_sessionmaker[AsyncSession],
    ml: MercadoLivreClient,
    wa: WuzapiClient,
    *,
    publication_id: int,
    actor: str,
    now: datetime,
) -> int:
    """Cópia de uma publicação enviada, mandada já, fora do intervalo (pedido explícito).

    A cópia é gravada antes da rede; o envio dela segue a regra de no máximo uma vez.
    Grupo manual: a cópia fica pronta em Postar.
    """
    async with sessions() as db, db.begin():
        original = await db.get(Publication, publication_id)
        if original is None or original.state != "sent":
            raise DistributionError("só publicação enviada pode ser reenviada")
        channel = await db.get(Channel, original.channel_id)
        if channel is None:
            raise DistributionError("grupo não encontrado")
        copies = (
            await db.execute(
                select(func.count()).where(
                    Publication.idempotency_key.like(f"{original.idempotency_key}:r%")
                )
            )
        ).scalar_one()
        copy = Publication(
            channel_id=original.channel_id,
            product_id=original.product_id,
            evaluation_id=original.evaluation_id,
            decision_id=original.decision_id,
            idempotency_key=f"{original.idempotency_key}:r{copies + 1}"[:80],
            dedup_key=original.dedup_key,
            state="ready",
            tier=original.tier,
            item_id=original.item_id,
            target_url=original.target_url,
            affiliate_url=original.affiliate_url,
            link_at=original.link_at,
            approved_price_cents=original.approved_price_cents,
            pix_price_cents=original.pix_price_cents,
            pix_at=original.pix_at,
            coupon_price_cents=original.coupon_price_cents,
            coupon_at=original.coupon_at,
            created_at=now,
        )
        db.add(copy)
        await db.flush()
        copy_id, channel_id = copy.id, channel.id
        automatic = channel.transport == "wuzapi" and bool(channel.wa_jid)
    logger.info(
        "reenvio pedido", extra={"publication_id": publication_id, "copy": copy_id, "user": actor}
    )
    if automatic:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as images:
            await send_one(
                sessions,
                ml,
                wa,
                channel_id=channel_id,
                publication_id=copy_id,
                now=now,
                images=images,
            )
    return copy_id


async def send_now(
    sessions: async_sessionmaker[AsyncSession],
    ml: MercadoLivreClient,
    wa: WuzapiClient,
    *,
    publication_id: int,
    actor: str,
    now: datetime,
) -> bool:
    """Botão "Enviar agora" da fila: manda já, sem esperar o intervalo (pedido explícito).

    Mesma revalidação de preço e mesma regra de no máximo uma vez do envio automático. Conta no
    limite do dia e reinicia o intervalo do grupo.
    """
    async with sessions() as db:
        pub = await db.get(Publication, publication_id)
        if pub is None or pub.state != "ready":
            raise DistributionError("só publicação pronta pode sair agora")
        channel = await db.get(Channel, pub.channel_id)
        if channel is None or channel.transport != "wuzapi" or not channel.wa_jid:
            raise DistributionError("grupo de envio manual: poste pelo celular")
        channel_id = channel.id
    logger.info("envio agora pedido", extra={"publication_id": publication_id, "user": actor})
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as images:
        return await send_one(
            sessions,
            ml,
            wa,
            channel_id=channel_id,
            publication_id=publication_id,
            now=now,
            images=images,
            actor=actor[:40],
        )


async def send_due(
    sessions: async_sessionmaker[AsyncSession],
    ml: MercadoLivreClient,
    wa: WuzapiClient,
    now: datetime,
    images: httpx.AsyncClient | None = None,
) -> int:
    if not wa.configured:
        return 0
    await recover_stuck(sessions, now)
    sent = 0
    for channel_id, publication_id in await _due(sessions, now):
        if await send_one(
            sessions,
            ml,
            wa,
            channel_id=channel_id,
            publication_id=publication_id,
            now=now,
            images=images,
        ):
            sent += 1
    return sent
