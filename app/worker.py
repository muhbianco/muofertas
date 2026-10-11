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

from app.collector.service import Collector
from app.core.clock import utcnow
from app.core.config import Settings, settings
from app.core.database import SessionFactory, dispose_engine
from app.core.lease import acquire_lease, holder_id, release_lease
from app.core.logging import configure_logging, get_logger
from app.distribution.links import resolve_links
from app.distribution.sender import send_due
from app.evaluation.service import Evaluator
from app.integrations.mercadolivre import (
    CallBudgetExceededError,
    MercadoLivreClient,
    RateLimitedError,
)
from app.integrations.ml_affiliate import AffiliatePortal
from app.integrations.wuzapi import WuzapiClient

logger = get_logger(__name__)
RATE_LIMIT_FIRST_PAUSE_SECONDS = 120
RATE_LIMIT_MAX_PAUSE_SECONDS = 15 * 60


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
    except RateLimitedError:
        wait = ml_pause.rate_limited(utcnow())
        logger.warning(
            "ML respondeu 429; coleta e envio em pausa",
            extra={"pause_s": wait, "seguidos": ml_pause.streak},
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
    collector = Collector(SessionFactory, client, cfg)
    evaluator = Evaluator(SessionFactory)
    logger.info(
        "worker iniciado", extra={"holder": holder, "collector_enabled": cfg.collector_enabled}
    )
    ml_pause = MlPause()
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
                    linked = await resolve_links(SessionFactory, portal, cfg, utcnow())
                    if linked:
                        logger.info("links de afiliado", extra={"linked": linked})
                    # O envio revalida o preço no ML: espera a pausa junto com a coleta.
                    if cfg.autosend_enabled and not ml_pause.active(utcnow()):
                        # Só com o lease: um envio por vez, nunca dois workers no mesmo grupo.
                        sent = await send_due(
                            SessionFactory, client, wa_client, utcnow(), images=images
                        )
                        if sent:
                            logger.info("envio automático", extra={"sent": sent})
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
