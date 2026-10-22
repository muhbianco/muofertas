"""Worker do coletor: um processo, um agendador, protegido por lease no banco.

Rodar: python -m app.worker

- O lease (`worker_lease`) garante um líder só, inclusive durante deploy (stop-first na stack).
  Expira sozinho: se o processo morrer, outro assume depois de `worker_lease_seconds`.
- 429 do ML ou teto por hora: só o que chama o ML (coleta e revalidação antes do envio) para.
  A pausa começa curta e dobra a cada 429 seguido. O lease, os links e o laço seguem vivos, então
  o alerta "worker parado" não dispara por causa de um 429.
- SIGTERM/SIGINT: termina o lote atual, solta o lease e sai.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.collector.service import Collector
from app.core.clock import utcnow
from app.core.config import Settings, settings
from app.core.database import SessionFactory, dispose_engine
from app.core.lease import acquire_lease, holder_id, release_lease
from app.core.logging import configure_logging, get_logger
from app.distribution.auto_approve import AutoApprover
from app.distribution.greetings import Greeter, greeting_config
from app.distribution.invites import invite_config, send_invites
from app.distribution.links import resolve_links
from app.distribution.sender import send_due
from app.domain.greetings import GreetingConfigError
from app.domain.invites import InviteConfig, InviteConfigError
from app.evaluation.service import Evaluator
from app.integrations.mercadolivre import (
    CallBudgetExceededError,
    MercadoLivreClient,
    RateLimitedError,
    UnstableError,
)
from app.integrations.ml_affiliate import AffiliatePortal
from app.integrations.wuzapi import WuzapiClient
from app.maintenance.retention import Retention
from app.measurement.sales import SalesWatch

logger = get_logger(__name__)
RATE_LIMIT_FIRST_PAUSE_SECONDS = 120
RATE_LIMIT_MAX_PAUSE_SECONDS = 15 * 60
# ML instável (disjuntor do coletor): pausa fixa; a instabilidade de 14/10/2026 durou 51 min.
UNSTABLE_PAUSE_SECONDS = 10 * 60


@dataclass
class MlPause:
    """Pausa de tudo o que chama a API do ML. O resto do ciclo (lease, links) não para."""

    until: datetime | None = None
    streak: int = 0

    def active(self, now: datetime) -> bool:
        return self.until is not None and now < self.until

    def rate_limited(self, now: datetime) -> float:
        """1º 429: 2 min; cada 429 seguido dobra, até 15 min."""
        self.streak += 1
        seconds = min(
            RATE_LIMIT_FIRST_PAUSE_SECONDS * 2 ** (self.streak - 1), RATE_LIMIT_MAX_PAUSE_SECONDS
        )
        self.until = now + timedelta(seconds=seconds)
        return float(seconds)

    def unstable(self, now: datetime) -> float:
        """Muitos produtos falhando juntos: 10 min sem chamar o ML (nem coleta nem envio)."""
        self.until = now + timedelta(seconds=UNSTABLE_PAUSE_SECONDS)
        return float(UNSTABLE_PAUSE_SECONDS)

    def budget_exhausted(self, now: datetime) -> float:
        """Teto nosso por hora: volta 5 s depois da virada da hora."""
        seconds = 3600 - (now.minute * 60 + now.second) + 5
        self.until = now + timedelta(seconds=seconds)
        return float(seconds)

    def clear(self) -> None:
        self.until = None
        self.streak = 0


async def _collect_round(
    collector: Collector, client: MercadoLivreClient, ml_pause: MlPause
) -> bool:
    """Descoberta + um lote de preços. True quando o lote teve trabalho (pode haver mais)."""
    try:
        await collector.discover_due()
        stats = await collector.refresh_due()
        if not stats.seen:
            # Lote em dia: sobra fôlego para o nome dos tipos de produto (painel), poucos por vez.
            await collector.name_domains()
    except RateLimitedError:
        wait = ml_pause.rate_limited(utcnow())
        logger.warning(
            "ML respondeu 429; coleta e envio em pausa",
            extra={"pause_s": wait, "seguidos": ml_pause.streak},
        )
        return False
    except UnstableError as exc:
        wait = ml_pause.unstable(utcnow())
        logger.warning(
            "ML instável; coleta e envio em pausa",
            extra={"pause_s": wait, "why": str(exc), "status": exc.status},
        )
        return False
    except CallBudgetExceededError:
        wait = ml_pause.budget_exhausted(utcnow())
        logger.info("teto de chamadas da hora atingido", extra={"pause_s": wait})
        return False
    ml_pause.clear()
    if stats.seen:
        logger.info(
            "lote atualizado",
            extra={
                "seen": stats.seen,
                "changed": stats.changed,
                "errors": stats.errors,
                "calls": stats.calls,
                "calls_this_hour": client.calls_this_hour,
            },
        )
    return bool(stats.seen)


async def run_worker(cfg: Settings, stop: asyncio.Event) -> None:
    holder = holder_id()
    client = MercadoLivreClient(cfg)
    wa_client = WuzapiClient(cfg)
    images = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    portal = AffiliatePortal(cfg)
    mail_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    sales_watch = SalesWatch(SessionFactory, portal, cfg, mail_http)
    retention = Retention(SessionFactory, cfg)
    collector = Collector(SessionFactory, client, cfg)
    logger.info(
        "worker iniciado", extra={"holder": holder, "collector_enabled": cfg.collector_enabled}
    )
    ml_pause = MlPause()
    # Calendário de eventos (config/convites.json): textos do convite diário e o congelamento da
    # referência da nota antes da Black Friday. Quebrado, não derruba as ofertas: só desliga os
    # dois, e o log diz.
    calendar: InviteConfig | None = None
    try:
        calendar = await invite_config(cfg.invites_file)
    except (OSError, ValueError, InviteConfigError):
        logger.exception("calendário inválido: convites e congelamento desligados")
    greeter: Greeter | None = None
    if cfg.greetings_enabled:
        try:
            greeter = Greeter(SessionFactory, wa_client, await greeting_config(cfg.greetings_file))
        except (OSError, ValueError, GreetingConfigError):
            logger.exception("texto de boas-vindas inválido: boas-vindas desligadas")
    evaluator = Evaluator(SessionFactory, calendar=calendar)
    approver = AutoApprover(
        SessionFactory,
        tiers=cfg.auto_approve_tier_list,
        affiliate_tool_id=cfg.ml_affiliate_tool_id,
    )
    try:
        while not stop.is_set():
            pause = cfg.worker_idle_seconds
            try:
                if not await acquire_lease(SessionFactory, holder, cfg.worker_lease_seconds):
                    pause = cfg.worker_lease_seconds / 2
                else:
                    if cfg.collector_enabled:
                        if not ml_pause.active(utcnow()) and await _collect_round(
                            collector, client, ml_pause
                        ):
                            pause = 0.5  # ainda pode haver lote vencido
                        await evaluator.evaluate_due()  # só banco: roda mesmo com o ML em pausa
                        await approver.run(utcnow())
                    linked = await resolve_links(SessionFactory, portal, cfg, utcnow())
                    if cfg.sales_watch_enabled:
                        # Portal de afiliado (não a API do ML): roda mesmo com a coleta em pausa.
                        await sales_watch.run(utcnow())
                    if linked:
                        logger.info("links de afiliado", extra={"linked": linked})
                    if greeter is not None:
                        # Antes do convite e das ofertas: quem chegou é saudado primeiro. Banco
                        # com problema aqui não segura o convite nem as ofertas do ciclo.
                        try:
                            await greeter.run(utcnow())
                        except SQLAlchemyError:
                            logger.exception("boas-vindas: erro no banco")
                    if cfg.invites_enabled and calendar is not None:
                        # Antes das ofertas: o convite da manhã é a primeira mensagem do dia.
                        # Não chama o ML, então sai mesmo com a coleta em pausa.
                        await send_invites(
                            SessionFactory,
                            wa_client,
                            calendar,
                            utcnow(),
                            hour=cfg.invite_hour,
                            morning=cfg.invite_morning_enabled,
                        )
                    # O envio revalida o preço no ML: espera a pausa junto com a coleta.
                    if cfg.autosend_enabled and not ml_pause.active(utcnow()):
                        # Só com o lease: um envio por vez, nunca dois workers no mesmo grupo.
                        sent = await send_due(
                            SessionFactory, client, wa_client, utcnow(), images=images
                        )
                        if sent:
                            logger.info("envio automático", extra={"sent": sent})
                    if cfg.retention_enabled:
                        await retention.run(utcnow())  # uma vez por dia, depois das 4h
            except Exception:
                logger.exception("erro no ciclo do worker")
                pause = 30
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=pause)
    finally:
        await release_lease(SessionFactory, holder)
        await client.aclose()
        await wa_client.aclose()
        await images.aclose()
        await mail_http.aclose()
        await portal.aclose()
        logger.info("worker encerrado", extra={"holder": holder})


async def _main() -> None:
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Windows não tem add_signal_handler: lá o Ctrl+C chega como KeyboardInterrupt.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await run_worker(settings, stop)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
