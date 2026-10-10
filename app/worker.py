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
import os
import signal
import socket
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collector.service import Collector
from app.core.clock import utcnow
from app.core.config import Settings, settings
from app.core.database import SessionFactory, dispose_engine
from app.core.logging import configure_logging, get_logger
from app.evaluation.service import Evaluator
from app.integrations.mercadolivre import (
    CallBudgetExceededError,
    MercadoLivreClient,
    RateLimitedError,
)
from app.models import WorkerLease

logger = get_logger(__name__)
LEASE_NAME = "collector"
RATE_LIMIT_PAUSE_SECONDS = 15 * 60


def holder_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:80]


async def acquire_lease(
    sessions: async_sessionmaker[AsyncSession],
    holder: str,
    ttl_seconds: int,
    *,
    name: str = LEASE_NAME,
) -> bool:
    """Pega ou renova o lease. A linha fica travada (FOR UPDATE): nunca há dois líderes."""
    now = utcnow()
    async with sessions() as session, session.begin():
        lease = (
            await session.execute(
                select(WorkerLease).where(WorkerLease.name == name).with_for_update()
            )
        ).scalar_one_or_none()
        if lease is None:
            session.add(
                WorkerLease(
                    name=name, holder=holder, expires_at=now + timedelta(seconds=ttl_seconds)
                )
            )
            return True
        if lease.holder == holder or lease.expires_at <= now:
            lease.holder = holder
            lease.expires_at = now + timedelta(seconds=ttl_seconds)
            return True
        return False


async def release_lease(
    sessions: async_sessionmaker[AsyncSession], holder: str, *, name: str = LEASE_NAME
) -> None:
    async with sessions() as session, session.begin():
        lease = await session.get(WorkerLease, name)
        if lease is not None and lease.holder == holder:
            lease.expires_at = utcnow()


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
