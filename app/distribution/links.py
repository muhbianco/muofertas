"""Link de afiliado das publicações aprovadas (worker, sob o lease).

Para cada publicação esperando link num grupo com etiqueta:
1. com a sessão do portal no host: o `meli.la` oficial pelo gerador do ML;
2. sem sessão, ou se o portal falhar: o link longo montado (`matt_word` + `matt_tool`), para o
   post não travar. A falha fica em `integration_error` (fonte `ml_portal`) e vira alerta.
Sem etiqueta no grupo e sem código de afiliado, a publicação fica para a tela Links (manual).

Rodada com link curto e sem recusa de sessão grava o acerto (`portal_status`): o alerta da sessão
fecha no ciclo seguinte, sem esperar 3 h (pedido do dono em 22/10/2026). Produto recusado pelo
gerador (`rejected`, ex.: 1008 "condição desconhecida") não diz nada da sessão.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.distribution import LinkError, build_affiliate_url
from app.integrations.ml_affiliate import AffiliatePortal, PortalError, read_session
from app.models import Channel, IntegrationError, PortalStatus, Publication

logger = get_logger(__name__)
BATCH = 10
PORTAL_PAUSE_SECONDS = 3.0
PORTAL_SOURCE = "ml_portal"
LONG_LINK_NOTE = "link longo: o gerador do ML não respondeu (veja o alerta)"


async def _record(
    sessions: async_sessionmaker[AsyncSession], exc: PortalError, now: datetime
) -> None:
    async with sessions() as db, db.begin():
        db.add(
            IntegrationError(
                source=PORTAL_SOURCE,
                occurred_at=now,
                http_status=exc.status,
                error_code=exc.kind,
                path="/affiliate-program/api/v2/affiliates/createLink",
                message=f"gerador de links: {exc.kind}",
            )
        )


async def _mark_ok(sessions: async_sessionmaker[AsyncSession], now: datetime) -> None:
    """Só o worker escreve aqui (sob o lease): get-or-create sem corrida."""
    async with sessions() as db, db.begin():
        row = await db.get(PortalStatus, PORTAL_SOURCE, with_for_update=True)
        if row is None:
            db.add(PortalStatus(source=PORTAL_SOURCE, last_ok_at=now))
        elif row.last_ok_at < now:
            row.last_ok_at = now


async def resolve_links(
    sessions: async_sessionmaker[AsyncSession],
    portal: AffiliatePortal,
    settings: Settings,
    now: datetime,
    *,
    pause: float = PORTAL_PAUSE_SECONDS,
) -> int:
    async with sessions() as db:
        pending = (
            await db.execute(
                select(Publication.id, Publication.target_url, Channel.ml_tag)
                .join(Channel, Channel.id == Publication.channel_id)
                .where(Publication.state == "awaiting_link", Channel.ml_tag.is_not(None))
                .order_by(Publication.id)
                .limit(BATCH)
            )
        ).all()
    if not pending:
        return 0
    session = read_session(settings.ml_session_file)
    done = 0
    shortened = False
    for index, (pub_id, target_url, tag) in enumerate(pending):
        link: str | None = None
        note: str | None = None
        if session is not None:
            if index:
                await asyncio.sleep(pause)
            try:
                link = await portal.create_link(session, target_url, tag)
                shortened = True
            except PortalError as exc:
                logger.warning(
                    "gerador de links do ML falhou", extra={"kind": exc.kind, "status": exc.status}
                )
                await _record(sessions, exc, now)
                if exc.kind in ("session", "blocked"):
                    session = None  # não insiste nesta rodada
        if link is None and settings.ml_affiliate_tool_id:
            try:
                link = build_affiliate_url(
                    target_url, tag=tag, tool_id=settings.ml_affiliate_tool_id
                )
            except LinkError:
                link = None
            note = LONG_LINK_NOTE if read_session(settings.ml_session_file) else None
        if link is None:
            continue  # fica para a tela Links
        async with sessions() as db, db.begin():
            pub = await db.get(Publication, pub_id)
            if pub is None or pub.state != "awaiting_link":
                continue
            pub.affiliate_url = link
            pub.link_at = now
            pub.state = "ready"
            pub.note = note
        done += 1
    if shortened and session is not None:
        # Link curto saiu e nenhuma recusa de sessão veio depois: a sessão vale.
        await _mark_ok(sessions, now)
    return done
