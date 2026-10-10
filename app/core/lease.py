"""Lease no banco: um líder só por tarefa (coletor, alertas), mesmo durante deploy.

Expira sozinho: se o processo morrer, outro assume depois do TTL (lição do Mu Tower).
"""

from __future__ import annotations

import os
import socket
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import WorkerLease

LEASE_NAME = "collector"


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
