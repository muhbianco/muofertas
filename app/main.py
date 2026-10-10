"""API web: saúde e o painel do operador (ofertas.muhbianco.com.br)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.service import alert_loop
from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import SessionFactory, check_database_health, dispose_engine, get_session
from app.core.logging import configure_logging
from app.models import CatalogProduct, CollectionRun, PriceObservation
from app.panel.auth import AuthContext, LoginRequiredError, require_admin
from app.panel.distribution_routes import router as distribution_router
from app.panel.operators_routes import router as operators_router
from app.panel.routes import router as panel_router
from app.panel.whatsapp_routes import router as whatsapp_router

# Só o próprio painel; imagem de produto vem do CDN do ML. Sem script, sem estilo inline.
CSP = (
    "default-src 'self'; img-src 'self' https://http2.mlstatic.com data:; style-src 'self'; "
    "script-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    watcher = (
        asyncio.create_task(alert_loop(SessionFactory, settings, stop))
        if settings.alerts_enabled
        else None
    )
    yield
    stop.set()
    if watcher is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(watcher, timeout=10)
    for name in ("ml_client", "wa_client"):
        client = getattr(app.state, name, None)
        if client is not None:
            await client.aclose()
    await dispose_engine()


app = FastAPI(
    title="MegaPromoOz", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
app.mount(
    "/static", StaticFiles(directory=str(Path(__file__).parent / "panel" / "static")), name="static"
)
app.include_router(panel_router)
app.include_router(distribution_router)
app.include_router(whatsapp_router)
app.include_router(operators_router)


@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(LoginRequiredError)
async def login_required(_: Request, __: LoginRequiredError) -> Response:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


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
async def collector_health(
    _: Annotated[AuthContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """Contagens do coletor (só logado: o painel é público na internet)."""
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
