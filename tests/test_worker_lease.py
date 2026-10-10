from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import WorkerLease
from app.worker import acquire_lease, release_lease


async def test_single_leader_and_takeover_after_expiry(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    assert await acquire_lease(sessions, "a", 60)
    assert not await acquire_lease(sessions, "b", 60)
    assert await acquire_lease(sessions, "a", 60)  # renovação

    async with sessions() as session, session.begin():
        lease = await session.get(WorkerLease, "collector")
        assert lease is not None
        lease.expires_at = utcnow() - timedelta(seconds=1)

    assert await acquire_lease(sessions, "b", 60)
    assert not await acquire_lease(sessions, "a", 60)


async def test_release_lets_other_take_over(sessions: async_sessionmaker[AsyncSession]) -> None:
    assert await acquire_lease(sessions, "a", 60)
    await release_lease(sessions, "a")
    assert await acquire_lease(sessions, "b", 60)
