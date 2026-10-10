"""Worker do coletor: um processo, um agendador, protegido por lease no banco.

Rodar: python -m app.worker

- O lease (`worker_lease`) garante um líder só, inclusive durante deploy (stop-first na stack).
  Expira sozinho: se o processo morrer, outro assume depois de `worker_lease_seconds`.
- 429 do ML ou teto por hora: o ciclo para e dorme; nada de insistir.
- SIGTERM/SIGINT: termina o lote atual, solta o lease e sai.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from app.collector.service import Collector
from app.core.clock import utcnow
from app.core.config import Settings, settings
from app.core.database import SessionFactory, dispose_engine
from app.core.lease import acquire_lease, holder_id, release_lease
from app.core.logging import configure_logging, get_logger
from app.evaluation.service import Evaluator
from app.integrations.mercadolivre import (
    CallBudgetExceededError,
    MercadoLivreClient,
    RateLimitedError,
)

logger = get_logger(__name__)
RATE_LIMIT_PAUSE_SECONDS = 15 * 60


async def run_worker(cfg: Settings, stop: asyncio.Event) -> None:
    holder = holder_id()
    client = MercadoLivreClient(cfg)
    collector = Collector(SessionFactory, client, cfg)
    evaluator = Evaluator(SessionFactory)
    logger.info(
        "worker iniciado", extra={"holder": holder, "collector_enabled": cfg.collector_enabled}
    )
    try:
        while not stop.is_set():
            pause = cfg.worker_idle_seconds
            try:
                if not await acquire_lease(SessionFactory, holder, cfg.worker_lease_seconds):
                    pause = cfg.worker_lease_seconds / 2
                elif cfg.collector_enabled:
                    await collector.discover_due()
                    stats = await collector.refresh_due()
                    await evaluator.evaluate_due()
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
                        pause = 0.5  # ainda pode haver lote vencido
            except RateLimitedError:
                logger.warning(
                    "ML respondeu 429; pausa longa", extra={"pause_s": RATE_LIMIT_PAUSE_SECONDS}
                )
                pause = RATE_LIMIT_PAUSE_SECONDS
            except CallBudgetExceededError:
                now = utcnow()
                pause = 3600 - (now.minute * 60 + now.second) + 5
                logger.info("teto de chamadas da hora atingido", extra={"pause_s": pause})
            except Exception:
                logger.exception("erro no ciclo do worker")
                pause = 30
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=pause)
    finally:
        await release_lease(SessionFactory, holder)
        await client.aclose()
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
