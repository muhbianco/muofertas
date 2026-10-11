"""Vendas do afiliado: o worker confere a lista de vendas do painel e avisa quando entra venda.

A cada 3 h, com a sessão do portal (a mesma do gerador de links), lê o total de vendas dos
últimos 30 dias. Subiu em relação à última checagem boa: e-mail ("primeira venda" na primeira).
Guarda só os nomes dos campos de uma venda, nunca os valores: é o que falta para separar as
vendas por etiqueta (grupo) sem guardar dado de comprador.

Sessão vencida ou bloqueio: fica registrado e o próximo ciclo tenta de novo; quem avisa da
sessão é o alerta "sessao_ml" do gerador de links. Nenhuma transação fica aberta durante a rede.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import send_mail
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.alerts import Mail
from app.domain.distribution import SP
from app.integrations.ml_affiliate import DASHBOARD, AffiliatePortal, PortalError, read_session
from app.models import SalesCheck

logger = get_logger(__name__)
CHECK_EVERY = timedelta(hours=3)
WINDOW_DAYS = 30


def sales_mail(total: int, previous: int | None, *, first: bool) -> Mail:
    if first:
        subject = "MegaPromoOz: primeira venda pelo link de afiliado"
        lead = "Entrou a primeira venda pelos links da MegaPromoOz."
    else:
        subject = f"MegaPromoOz: nova venda pelo afiliado ({total} em {WINDOW_DAYS} dias)"
        lead = f"Entrou venda nova pelos links da MegaPromoOz (antes: {previous})."
    body = (
        f"{lead}\n\n"
        f"Vendas nos últimos {WINDOW_DAYS} dias no painel de afiliado do Mercado Livre: {total}.\n"
        f"Detalhes (produto, comissão estimada, status): {DASHBOARD}\n\n"
        "A comissão aparece como estimada até o Mercado Livre confirmar a venda."
    )
    return Mail(subject, body)


class SalesWatch:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        portal: AffiliatePortal,
        settings: Settings,
        http: httpx.AsyncClient,
    ) -> None:
        self._sessions = sessions
        self._portal = portal
        self._settings = settings
        self._http = http
        self._next_at: datetime | None = None

    async def _last(self, db: AsyncSession, *, ok_only: bool) -> SalesCheck | None:
        query = select(SalesCheck).order_by(SalesCheck.id.desc()).limit(1)
        if ok_only:
            query = query.where(SalesCheck.status == "ok")
        return (await db.execute(query)).scalar_one_or_none()

    async def run(self, now: datetime) -> bool:
        """Uma checagem, se já deu a hora. True quando saiu e-mail de venda."""
        if self._next_at is None:
            async with self._sessions() as db:
                last = await self._last(db, ok_only=False)
            self._next_at = last.checked_at + CHECK_EVERY if last else now
        if now < self._next_at:
            return False
        self._next_at = now + CHECK_EVERY
        session = read_session(self._settings.ml_session_file)
        if session is None:
            await self._record(now, "sem_sessao")
            return False
        end = now.astimezone(SP).replace(hour=0, minute=0, second=0, microsecond=0)
        end += timedelta(days=1)
        try:
            page = await self._portal.sales(session, end - timedelta(days=WINDOW_DAYS), end)
        except PortalError as exc:
            logger.warning("vendas do afiliado: portal não respondeu", extra={"kind": exc.kind})
            await self._record(now, exc.kind[:12])
            return False
        async with self._sessions() as db, db.begin():
            previous = await self._last(db, ok_only=True)
            ever_sold = (
                await db.execute(select(SalesCheck.id).where(SalesCheck.total_results > 0).limit(1))
            ).scalar_one_or_none() is not None
            row = SalesCheck(
                checked_at=now,
                status="ok",
                total_results=page.total,
                item_keys=json.dumps(page.item_keys) if page.item_keys else None,
            )
            db.add(row)
            await db.flush()
            row_id = row.id
        before = previous.total_results if previous else None
        if page.total <= (before or 0):
            return False
        mail = sales_mail(page.total, before, first=not ever_sold)
        if not await send_mail(self._http, self._settings, mail):
            return False
        async with self._sessions() as db, db.begin():
            saved = await db.get(SalesCheck, row_id)
            if saved is not None:
                saved.notified_at = now
        logger.info("venda nova pelo afiliado", extra={"total": page.total, "before": before})
        return True

    async def _record(self, now: datetime, status: str) -> None:
        async with self._sessions() as db, db.begin():
            db.add(SalesCheck(checked_at=now, status=status))
