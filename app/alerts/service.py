"""Alertas: fatos do banco → regras puras → estado em `alert` → e-mail pelo n8n.

Roda no processo web (o worker não consegue avisar que ele mesmo morreu), com lease próprio para
haver um vigia só mesmo com duas réplicas durante o deploy. A chamada ao n8n acontece fora de
transação; se falhar, o próximo ciclo tenta de novo (o alerta fica sem `notified_at`).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.lease import LEASE_NAME, acquire_lease, holder_id
from app.core.logging import get_logger
from app.distribution.links import PORTAL_SOURCE
from app.domain.alerts import (
    OVERDUE_AFTER,
    HealthFacts,
    Mail,
    firing,
    mail_for,
    should_remind,
)
from app.integrations.wuzapi import WuzapiClient, WuzapiError
from app.models import (
    Alert,
    CatalogProduct,
    CollectionRun,
    IntegrationError,
    PortalStatus,
    Publication,
    WaSession,
    WorkerLease,
)
from app.whatsapp import service as wa

logger = get_logger(__name__)
ALERT_LEASE = "alerts"
OPEN_LIMIT = 50
RESOLUTION_RETRY = timedelta(days=1)
WHATSAPP_FRESH = timedelta(hours=1)
PORTAL_ALERT_WINDOW = timedelta(hours=3)


async def gather_facts(db: AsyncSession, now: datetime) -> HealthFacts:
    lease = await db.get(WorkerLease, LEASE_NAME)
    # Recusa de sessão só conta se veio depois do último link curto (sessão renovada fecha o
    # alerta no ciclo seguinte) e dentro da janela.
    portal_since = now - PORTAL_ALERT_WINDOW
    portal = await db.get(PortalStatus, PORTAL_SOURCE)
    if portal is not None and portal.last_ok_at >= portal_since:
        portal_since = portal.last_ok_at
    last_ok = (
        await db.execute(
            select(func.max(CollectionRun.finished_at)).where(
                CollectionRun.source == "ml_refresh", CollectionRun.status.in_(("ok", "partial"))
            )
        )
    ).scalar_one_or_none()
    errors = (
        await db.execute(
            select(func.count())
            .select_from(IntegrationError)
            .where(IntegrationError.occurred_at >= now - timedelta(hours=1))
        )
    ).scalar_one()
    active = (
        await db.execute(
            select(func.count()).select_from(CatalogProduct).where(CatalogProduct.active.is_(True))
        )
    ).scalar_one()
    overdue = (
        await db.execute(
            select(func.count())
            .select_from(CatalogProduct)
            .where(
                CatalogProduct.active.is_(True),
                CatalogProduct.next_check_at < now - OVERDUE_AFTER,
            )
        )
    ).scalar_one()
    session = (await db.execute(select(WaSession).limit(1))).scalar_one_or_none()
    # Só alerta número que já esteve pareado (tem JID) e foi conferido há pouco.
    whatsapp_down = bool(
        session is not None
        and session.jid
        and not session.logged_in
        and session.checked_at is not None
        and now - session.checked_at < WHATSAPP_FRESH
    )
    return HealthFacts(
        now=now,
        lease_expires_at=lease.expires_at if lease else None,
        last_refresh_ok_at=last_ok,
        errors_last_hour=errors,
        products_active=active,
        products_overdue=overdue,
        whatsapp_down=whatsapp_down,
        portal_session_errors=(
            await db.execute(
                select(func.count())
                .select_from(IntegrationError)
                .where(
                    IntegrationError.source == PORTAL_SOURCE,
                    IntegrationError.error_code.in_(("session", "blocked")),
                    IntegrationError.occurred_at >= portal_since,
                )
            )
        ).scalar_one(),
        uncertain_sends=(
            await db.execute(
                select(func.count())
                .select_from(Publication)
                .where(Publication.state.in_(("unknown", "failed")))
            )
        ).scalar_one(),
    )


@dataclass(frozen=True, slots=True)
class Notice:
    alert_id: int
    event: str  # aberto | lembrete | resolvido
    mail: Mail


async def sync(
    db: AsyncSession, current: dict[str, str], now: datetime, panel_url: str
) -> list[Notice]:
    """Abre, atualiza e fecha alertas; devolve o que precisa de e-mail."""
    opened = {
        a.kind: a
        for a in (
            await db.execute(select(Alert).where(Alert.state == "open").limit(OPEN_LIMIT))
        ).scalars()
    }
    notices: list[Notice] = []
    for kind, message in current.items():
        alert = opened.get(kind)
        if alert is None:
            alert = Alert(
                kind=kind, state="open", message=message[:255], opened_at=now, last_seen_at=now
            )
            db.add(alert)
            await db.flush()
        else:
            alert.message = message[:255]
            alert.last_seen_at = now
        if should_remind(alert.notified_at, now):
            event = "aberto" if alert.notified_at is None else "lembrete"
            notices.append(
                Notice(
                    alert.id,
                    event,
                    mail_for(
                        kind, message, event=event, opened_at=alert.opened_at, panel_url=panel_url
                    ),
                )
            )
    for kind, alert in opened.items():
        if kind not in current:
            alert.state = "resolved"
            alert.resolved_at = now
    pending = (
        await db.execute(
            select(Alert)
            .where(
                Alert.state == "resolved",
                Alert.notified_at.is_not(None),
                Alert.resolution_notified_at.is_(None),
                Alert.resolved_at >= now - RESOLUTION_RETRY,
            )
            .limit(OPEN_LIMIT)
        )
    ).scalars()
    for alert in pending:
        notices.append(
            Notice(
                alert.id,
                "resolvido",
                mail_for(
                    alert.kind,
                    alert.message,
                    event="resolvido",
                    opened_at=alert.opened_at,
                    panel_url=panel_url,
                ),
            )
        )
    return notices


async def send_mail(http: httpx.AsyncClient, settings: Settings, mail: Mail) -> bool:
    """POST no fluxo `megapromooz-email` do n8n. Sem configuração, o alerta fica só no painel."""
    if not settings.alert_email_webhook_url or not settings.alert_email_to:
        return False
    token = settings.alert_email_webhook_token.get_secret_value()
    try:
        response = await http.post(
            settings.alert_email_webhook_url,
            json={
                "app": "megapromooz",
                "to": settings.alert_email_to,
                "subject": mail.subject,
                "body": mail.body,
            },
            headers={"authorization": token} if token else {},
        )
    except httpx.HTTPError as exc:
        logger.warning("e-mail de alerta falhou", extra={"error": type(exc).__name__})
        return False
    if response.status_code >= 300:
        logger.warning("e-mail de alerta recusado", extra={"status": response.status_code})
        return False
    return True


async def check_once(
    sessions: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    settings: Settings,
    holder: str,
    now: datetime | None = None,
    wa_client: WuzapiClient | None = None,
) -> int:
    """Um ciclo do vigia. Devolve quantos e-mails saíram."""
    now = now or utcnow()
    if not await acquire_lease(
        sessions, holder, settings.alert_check_seconds * 2, name=ALERT_LEASE
    ):
        return 0
    if wa_client is not None:
        try:
            await wa.tick(sessions, wa_client, settings, now)
        except WuzapiError as exc:
            # WuzAPI fora não derruba o vigia; o estado fica o da última leitura boa.
            logger.warning("vigia: WuzAPI não respondeu", extra={"error": str(exc)[:120]})
    async with sessions() as db, db.begin():
        facts = await gather_facts(db, now)
        current = firing(facts)
        notices = await sync(db, current, now, f"{settings.panel_url}/saude")
    if current:
        logger.warning("alertas abertos", extra={"kinds": sorted(current)})
    sent = 0
    for notice in notices:
        if not await send_mail(http, settings, notice.mail):
            continue
        async with sessions() as db, db.begin():
            alert = await db.get(Alert, notice.alert_id)
            if alert is not None:
                if notice.event == "resolvido":
                    alert.resolution_notified_at = now
                else:
                    alert.notified_at = now
        sent += 1
    return sent


async def alert_loop(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, stop: asyncio.Event
) -> None:
    holder = holder_id()
    wa_client = WuzapiClient(settings)
    async with httpx.AsyncClient(timeout=10.0) as http:
        while not stop.is_set():
            try:
                await check_once(sessions, http, settings, holder, wa_client=wa_client)
            except (SQLAlchemyError, OSError) as exc:
                # Banco fora: o próximo ciclo tenta de novo; o /health/ready já denuncia a queda.
                logger.warning("vigia de alertas falhou", extra={"error": type(exc).__name__})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.alert_check_seconds)
    await wa_client.aclose()
