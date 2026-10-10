from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# READ COMMITTED, como na api-commerce (ADR 0008 do mucommerce): leitura com lock pega só as linhas
# encontradas, sem gap lock. O código não depende de snapshot repetível.
APP_ISOLATION_LEVEL = "READ COMMITTED"


def create_app_engine(url: str, *, pooled: bool = True) -> AsyncEngine:
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=False)
    options: dict[str, object] = {
        "isolation_level": APP_ISOLATION_LEVEL,
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": settings.db_connect_timeout_seconds},
    }
    if pooled:
        options |= {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_recycle": settings.db_pool_recycle_seconds,
            "pool_timeout": 10,
        }
    return create_async_engine(url, echo=False, **options)


engine: AsyncEngine = create_app_engine(settings.database_url)

SessionFactory = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Para rotas que precisam fechar a transação antes de chamar a rede e abrir outra depois."""
    return SessionFactory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Uma sessão por request: commit no fim, rollback em qualquer erro."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database_health() -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def dispose_engine() -> None:
    await engine.dispose()
