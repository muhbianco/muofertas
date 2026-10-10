"""API web. Na Fase 1 só saúde e estado do coletor; o painel entra em seguida."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import check_database_health, dispose_engine, get_session
from app.core.logging import configure_logging
from app.models import CatalogProduct, CollectionRun, PriceObservation


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    yield
    await dispose_engine()


app = FastAPI(
    title="MegaPromoOz", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)


@app.get("/health/live")
async def live() -> dict[str, str]:
    """Processo de pé. Não toca dependência: queda do banco não deve reiniciar o container."""
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(response: Response) -> dict[str, str]:
    """Dependências alcançáveis (banco)."""
    ok = await check_database_health()
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded"}


@app.get("/health/collector")
async def collector_health(session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    """Para monitoração: o coletor está andando? (sem dado de produto, só contagens)."""
    since = utcnow() - timedelta(hours=1)
    last_ok = (
        await session.execute(
            select(func.max(CollectionRun.finished_at)).where(
                CollectionRun.source == "ml_refresh", CollectionRun.status.in_(("ok", "partial"))
            )
        )
    ).scalar_one_or_none()
    return {
        "products_active": (
            await session.execute(
                select(func.count())
                .select_from(CatalogProduct)
                .where(CatalogProduct.active.is_(True))
            )
        ).scalar_one(),
        "observations_last_hour": (
            await session.execute(
                select(func.count())
                .select_from(PriceObservation)
                .where(PriceObservation.observed_at >= since)
            )
        ).scalar_one(),
        "last_refresh_finished_at": last_ok.isoformat() if last_ok else None,
    }
