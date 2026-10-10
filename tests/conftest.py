from __future__ import annotations

import os

# Antes de qualquer import do app: o engine do módulo é criado na importação.
os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ML_CLIENT_ID", "test-client")
os.environ.setdefault("ML_CLIENT_SECRET", "test-secret")

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.models import Base


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ml_client_id="test-client",
        ml_client_secret="test-secret",  # type: ignore[arg-type]
        ml_requests_per_second=1000.0,
        ml_hourly_call_cap=10_000,
        database_url_override="sqlite+aiosqlite://",
    )
